"""預測留存與事後準度驗證。

背景：`predictions` 表在 schema 裡從第一天就存在、註解也寫著供事後
檢驗用，但沒有任何程式寫入它。使用者問「昨天的預測準不準」時，只能
從舊的 index.html 反解 JSON 硬湊 —— 檔案一被覆蓋就永遠比不了。
"""
import pandas as pd
import pytest

from src import accuracy, store


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    store.init_schema(c)
    yield c
    c.close()


def _payload(*stocks):
    return {"stocks": list(stocks), "horizon_days": 20}


def _stock(symbol="2330.TW", *, predictable=True, upside=0.10, as_of="2026-08-31",
           price=100.0, confidence=70.0):
    return {
        "symbol": symbol, "price": price, "as_of": as_of, "trend": "uptrend",
        "technical": {"tech_q10": -0.05, "tech_q90": 0.20},
        "valuation": {"fair": price * 1.08},
        "news": {"sentiment": 0.2},
        "prediction": {
            "predictable": predictable, "upside_pct": upside,
            "target_low": price * 0.95, "target_mid": price * (1 + upside),
            "target_high": price * 1.3, "confidence": confidence,
            "tracks": {"tech": {"fair": price * (1 + upside)}},
        },
    }


def _prices(conn, symbol, series):
    store.upsert(conn, "prices", [
        {"symbol": symbol, "date": d, "open": v, "high": v, "low": v,
         "close": v, "adjclose": v, "volume": 1000}
        for d, v in series.items()
    ])


def _register(conn, symbol, market="TW"):
    store.upsert(conn, "stocks", [{
        "symbol": symbol, "market": market, "code": symbol.split(".")[0],
        "name": symbol, "name_en": None, "sector": None, "industry": None,
        "currency": "TWD", "listed_date": None, "updated_at": "2026-08-31",
    }])


# --- 留存 -------------------------------------------------------------


def test_snapshot_keeps_only_predictable_stocks():
    """不可預測的個股沒有可驗證的主張，留下來只會製造分母噪音。"""
    rows = accuracy.snapshot_rows(_payload(
        _stock("A", predictable=True), _stock("B", predictable=False)))
    assert [r["symbol"] for r in rows] == ["A"]


def test_snapshot_uses_data_date_not_run_date():
    """同一天重跑兩次不應產生兩筆；盤後跑與隔天早上跑基於同一份收盤資料。"""
    rows = accuracy.snapshot_rows(_payload(_stock(as_of="2026-08-31")))
    assert rows[0]["run_date"] == "2026-08-31"


def test_snapshot_captures_the_verifiable_claim():
    row = accuracy.snapshot_rows(_payload(_stock(upside=0.12, price=200.0)))[0]
    assert row["close"] == 200.0
    assert row["upside_pct"] == 0.12
    assert row["target_mid"] == pytest.approx(224.0)
    assert row["confidence"] == 70.0


def test_save_snapshot_is_idempotent_for_the_same_data_date(conn):
    payload = _payload(_stock())
    accuracy.save_snapshot(conn, payload)
    accuracy.save_snapshot(conn, payload)
    assert conn.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"] == 1


def test_snapshot_of_empty_payload_writes_nothing(conn):
    assert accuracy.save_snapshot(conn, {"stocks": []}) == 0


# --- 比對 -------------------------------------------------------------


def _setup(conn, *, stock_end, bench_end, upside=0.10):
    _register(conn, "2330.TW")
    _register(conn, "0050.TW")
    _prices(conn, "2330.TW", {"2026-08-31": 100.0, "2026-09-01": stock_end})
    _prices(conn, "0050.TW", {"2026-08-31": 50.0, "2026-09-01": bench_end})
    accuracy.save_snapshot(conn, _payload(_stock(upside=upside)))


def test_score_computes_excess_over_benchmark(conn):
    """模型預測的是超額報酬，比對也必須用超額。"""
    _setup(conn, stock_end=110.0, bench_end=52.5)   # 個股 +10%、基準 +5%
    row = accuracy.score(conn).frame.iloc[0]
    assert row["actual_return"] == pytest.approx(0.10)
    assert row["benchmark_return"] == pytest.approx(0.05)
    assert row["actual_excess"] == pytest.approx(0.05)


def test_score_marks_immature_predictions(conn):
    """沒走完 20 日的預測必須標明，否則會拿半途結果當最終表現。"""
    _setup(conn, stock_end=110.0, bench_end=50.0)
    row = accuracy.score(conn).frame.iloc[0]
    assert row["elapsed_days"] == 1
    assert row["matured"] is False or row["matured"] == 0


def test_score_marks_matured_when_horizon_reached(conn):
    _register(conn, "2330.TW")
    _register(conn, "0050.TW")
    days = {f"2026-09-{d:02d}": 100.0 + d for d in range(1, 26)}
    _prices(conn, "2330.TW", {"2026-08-31": 100.0, **days})
    _prices(conn, "0050.TW", {"2026-08-31": 50.0,
                              **{d: 50.0 for d in days}})
    accuracy.save_snapshot(conn, _payload(_stock()))
    row = accuracy.score(conn).frame.iloc[0]
    assert row["elapsed_days"] >= 20
    assert bool(row["matured"]) is True


def test_score_skips_predictions_without_subsequent_prices(conn):
    _register(conn, "2330.TW")
    _register(conn, "0050.TW")
    _prices(conn, "2330.TW", {"2026-08-31": 100.0})
    _prices(conn, "0050.TW", {"2026-08-31": 50.0})
    accuracy.save_snapshot(conn, _payload(_stock()))
    assert accuracy.score(conn).frame.empty


def test_score_on_empty_table_returns_empty(conn):
    assert accuracy.score(conn).frame.empty


# --- 摘要與顯著性 -----------------------------------------------------


def _scored(pred_and_actual):
    frame = pd.DataFrame([
        {"symbol": f"S{i}", "market": "TW", "upside_pct": p,
         "actual_excess": a, "matured": True, "elapsed_days": 20}
        for i, (p, a) in enumerate(pred_and_actual)
    ])
    return accuracy.Scored(frame, 20)


def test_summary_reports_p_values_not_just_rates():
    """單看命中率 55% 會誤以為有效，30 筆樣本下那與擲硬幣無法區分。"""
    summary = accuracy.summarise(_scored(
        [(0.1, 0.01)] * 11 + [(0.1, -0.01)] * 9))
    assert "hit_pvalue" in summary
    assert "ic_pvalue" in summary


def test_perfect_predictor_is_significant():
    data = [(0.01 * i, 0.01 * i) for i in range(-15, 16) if i != 0]
    summary = accuracy.summarise(_scored(data))
    assert summary["hit_rate"] == pytest.approx(1.0)
    assert summary["hit_pvalue"] < 0.01
    assert summary["ic"] > 0.9


def test_coin_flip_predictor_is_not_significant():
    data = [(0.05, 0.01), (0.05, -0.01)] * 15
    summary = accuracy.summarise(_scored(data))
    assert summary["hit_rate"] == pytest.approx(0.5)
    assert summary["hit_pvalue"] > 0.05


def test_tiny_sample_refuses_to_report_statistics():
    summary = accuracy.summarise(_scored([(0.1, 0.02), (0.1, 0.03)]))
    assert "reason" in summary
    assert "不足以統計" in summary["reason"]


def test_format_flags_insignificant_results_explicitly():
    lines = accuracy.format_summary(accuracy.summarise(
        _scored([(0.05, 0.01), (0.05, -0.01)] * 15)))
    assert any("與隨機無法區分" in line for line in lines)


def test_format_warns_when_predictions_are_immature():
    frame = pd.DataFrame([
        {"symbol": f"S{i}", "market": "TW", "upside_pct": 0.05,
         "actual_excess": 0.01 * (1 if i % 2 else -1),
         "matured": False, "elapsed_days": 2}
        for i in range(20)
    ])
    lines = accuracy.format_summary(accuracy.summarise(accuracy.Scored(frame, 20)))
    assert any("尚未走滿預測期間" in line for line in lines)


def test_nan_rows_are_excluded_from_statistics():
    frame = pd.DataFrame([
        {"symbol": "A", "market": "TW", "upside_pct": float("nan"),
         "actual_excess": 0.01, "matured": True, "elapsed_days": 20},
        *[{"symbol": f"S{i}", "market": "TW", "upside_pct": 0.05,
           "actual_excess": 0.01, "matured": True, "elapsed_days": 20}
          for i in range(10)],
    ])
    summary = accuracy.summarise(accuracy.Scored(frame, 20))
    assert summary["n"] == 10


def test_by_market_splits_results():
    frame = pd.DataFrame([
        {"symbol": f"T{i}", "market": "TW", "upside_pct": 0.05,
         "actual_excess": 0.01, "matured": True, "elapsed_days": 20}
        for i in range(8)
    ] + [
        {"symbol": f"U{i}", "market": "US", "upside_pct": 0.05,
         "actual_excess": -0.01, "matured": True, "elapsed_days": 20}
        for i in range(8)
    ])
    out = accuracy.by_market(accuracy.Scored(frame, 20))
    assert set(out) == {"TW", "US"}
    assert out["TW"]["hit_rate"] == pytest.approx(1.0)
    assert out["US"]["hit_rate"] == pytest.approx(0.0)


def test_worst_misses_ranks_by_absolute_error():
    frame = pd.DataFrame([
        {"symbol": "BIG", "market": "TW", "upside_pct": 0.40,
         "actual_excess": -0.05, "matured": True, "elapsed_days": 20},
        {"symbol": "SMALL", "market": "TW", "upside_pct": 0.02,
         "actual_excess": 0.01, "matured": True, "elapsed_days": 20},
    ])
    assert accuracy.worst_misses(accuracy.Scored(frame, 20)).iloc[0]["symbol"] == "BIG"


def test_constant_predictions_report_ic_as_undefined_not_nan():
    """所有預測值相同時秩相關無定義。回 NaN 會在報表顯示成「IC nan」，
    看起來像計算失敗；明確說「無定義」才說得清楚原因。
    """
    summary = accuracy.summarise(_scored([(0.05, 0.01 * (i - 5)) for i in range(12)]))
    assert summary["ic"] is None
    assert summary["ic_undefined"] is True
    assert any("無定義" in line for line in accuracy.format_summary(summary))


# --- 重跑同一個資料日必須得到一致的快照（2026-09-10）------------------


def test_rerun_removes_rows_for_symbols_that_became_unpredictable(tmp_path):
    """同一個 run_date 重跑時，已不可預測的個股不得留下舊列。

    `snapshot_rows` 只寫入可預測的個股，所以個股由「可預測」轉為
    「不可預測」時，舊列不會被覆寫，會留在表裡。實測加上波動區間閘門
    後 ADBE 由可預測轉為不可預測（預測 +39.5%、區間僅 ±18.1%），
    但那筆 +39.5% 仍留在 predictions 表裡 —— `--accuracy` 會去評分一個
    系統已經不再做出的預測。

    只清理**同一個 run_date**。其他日期的留存紀錄是事前寫死的證據，
    不得因為後來改了程式就回頭修改。
    """
    conn = store.connect(tmp_path / "t.db")
    store.init_schema(conn)
    try:
        first = {"stocks": [
            _stock("ADBE", predictable=True, upside=0.395, as_of="2026-09-08"),
            _stock("AAPL", predictable=True, upside=0.05, as_of="2026-09-08"),
        ]}
        assert accuracy.save_snapshot(conn, first) == 2

        # 加上閘門後 ADBE 不再可預測
        second = {"stocks": [
            _stock("ADBE", predictable=False, upside=0.395, as_of="2026-09-08"),
            _stock("AAPL", predictable=True, upside=0.05, as_of="2026-09-08"),
        ]}
        accuracy.save_snapshot(conn, second)

        kept = {r[0] for r in conn.execute("SELECT symbol FROM predictions")}
        assert kept == {"AAPL"}
    finally:
        conn.close()


def test_rerun_does_not_touch_other_run_dates(tmp_path):
    """8/31 留存的證據不得被 9/10 的重跑動到。"""
    conn = store.connect(tmp_path / "t.db")
    store.init_schema(conn)
    try:
        accuracy.save_snapshot(conn, {"stocks": [
            _stock("ADBE", predictable=True, upside=0.30, as_of="2026-08-31"),
        ]})
        accuracy.save_snapshot(conn, {"stocks": [
            _stock("ADBE", predictable=False, upside=0.30, as_of="2026-09-08"),
        ]})

        rows = list(conn.execute("SELECT run_date FROM predictions"))
        assert [r[0] for r in rows] == ["2026-08-31"]
    finally:
        conn.close()
