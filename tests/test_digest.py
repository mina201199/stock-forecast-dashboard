"""每日日報測試（spec §7）。

日報的內容順序是刻意的：準度計分卡放最上面，因為它決定讀者該多信
下面的東西。測試把這個順序當成契約。
"""
import json

import numpy as np
import pandas as pd
import pytest

from src import baserate, digest, store
from src.signals.base import Signal


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    store.init_schema(c)
    yield c
    c.close()


def _frame(symbol, market, closes):
    n = len(closes)
    return baserate.SymbolFrame(symbol=symbol, market=market, df=pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d"),
        "adjclose": np.array(closes, dtype=float),
        "close": np.array(closes, dtype=float),
        "volume": np.ones(n) * 1000,
    }))


def _always(key="always"):
    return Signal(key=key, name=f"永遠觸發-{key}", family="technical",
                  markets=("TW", "US"),
                  rule=lambda df: pd.Series(True, index=df.index), min_bars=0)


def _never(key="never"):
    return Signal(key=key, name="不觸發", family="technical", markets=("TW", "US"),
                  rule=lambda df: pd.Series(False, index=df.index), min_bars=0)


def _rate(key, market="TW", horizon=20, verdict="edge", **kw):
    base = dict(
        signal_key=key, market=market, horizon=horizon, n_fires=500, n_effective=40,
        hit_rate=0.55, baseline_hit_rate=0.47, median_excess=0.02,
        ci_low=0.005, ci_high=0.04, p_value=0.01, p_adjusted=0.03,
        first_half_excess=0.01, second_half_excess=0.02,
        first_half_median=0.01, second_half_median=0.02, verdict=verdict,
    )
    return baserate.BaseRate(**{**base, **kw})


# --- 今日掃描 -----------------------------------------------------------


def test_scan_latest_reports_the_last_bar_only():
    frames = [_frame("2330.TW", "TW", list(range(100, 200)))]
    fires = baserate.scan_latest([_always()], frames)
    assert len(fires) == 1
    assert fires[0].symbol == "2330.TW"
    assert fires[0].date == frames[0].df["date"].iloc[-1]
    assert fires[0].price == pytest.approx(199.0)


def test_scan_latest_skips_signals_that_did_not_fire():
    frames = [_frame("2330.TW", "TW", list(range(100, 200)))]
    assert baserate.scan_latest([_never()], frames) == []


def test_scan_latest_respects_market_applicability():
    tw_only = Signal(key="tw_only", name="僅台股", family="flow", markets=("TW",),
                     rule=lambda df: pd.Series(True, index=df.index), min_bars=0)
    frames = [_frame("2330.TW", "TW", list(range(100, 200))),
              _frame("AAPL", "US", list(range(100, 200)))]
    fires = baserate.scan_latest([tw_only], frames)
    assert [f.symbol for f in fires] == ["2330.TW"]


# --- signal_stats 持久化 ------------------------------------------------


def test_save_and_load_signal_stats_round_trip(conn):
    rates = [_rate("a"), _rate("b", verdict="no_edge")]
    assert baserate.save_stats(conn, rates) == 2

    loaded = {(r.signal_key, r.market, r.horizon): r for r in baserate.load_stats(conn)}
    assert len(loaded) == 2
    got = loaded[("a", "TW", 20)]
    assert got.verdict == "edge"
    assert got.hit_rate == pytest.approx(0.55)
    assert got.n_effective == 40


def test_save_stats_replaces_previous_run(conn):
    baserate.save_stats(conn, [_rate("a", verdict="edge")])
    baserate.save_stats(conn, [_rate("a", verdict="no_edge")])
    loaded = baserate.load_stats(conn)
    assert len(loaded) == 1
    assert loaded[0].verdict == "no_edge"


# --- 日報結構 -----------------------------------------------------------


def _payload(**kw):
    base = dict(
        as_of="2026-09-10",
        last_success="2026-09-10",
        accuracy_lines=["方向命中率 48.9%（p=0.915）與隨機無法區分"],
        fires=[],
        rates=[],
        bands=[],
        plan=None,
        degraded=[],
    )
    return {**base, **kw}


def test_accuracy_scorecard_comes_first():
    """比的是章節標題的位置，不是任意子字串 —— 標題「每日訊號日報」
    本身就含「訊號」，用子字串比會永遠失敗。"""
    md = digest.render_markdown(digest.build(_payload()))
    assert md.index("## 1. 準度計分卡") < md.index("## 2. 今日觸發的訊號")


def test_edge_section_states_emptiness_explicitly():
    """沒有任何 edge 訊號時，必須明說，不能只是空白。

    空白區塊會被讀成「今天沒觸發」，而實際意思是「沒有任何訊號有
    統計優勢」—— 完全不同的兩件事。
    """
    md = digest.render_markdown(digest.build(_payload()))
    assert "沒有任何訊號" in md


def test_edge_fires_show_win_rate_with_baseline_and_effective_sample():
    data = digest.build(_payload(
        fires=[baserate.Fire("2330.TW", "TW", "a", "2026-09-10", 1000.0)],
        rates=[_rate("a")],
    ))
    md = digest.render_markdown(data)
    assert "2330.TW" in md
    assert "55.0%" in md          # 勝率
    assert "47.0%" in md          # 基準對照，必須並列
    assert "40" in md             # 有效樣本


def test_non_edge_fires_are_labelled_not_hidden():
    data = digest.build(_payload(
        fires=[baserate.Fire("2317.TW", "TW", "b", "2026-09-10", 200.0)],
        rates=[_rate("b", verdict="no_edge")],
    ))
    md = digest.render_markdown(data)
    assert "2317.TW" in md
    assert "無統計優勢" in md


def test_unstable_fires_say_which_half_failed():
    data = digest.build(_payload(
        fires=[baserate.Fire("2454.TW", "TW", "c", "2026-09-10", 900.0)],
        rates=[_rate("c", verdict="unstable", first_half_median=-0.014,
                     second_half_median=0.053)],
    ))
    md = digest.render_markdown(data)
    assert "前半" in md and "-1.4%" in md


def test_bands_are_not_called_target_prices():
    data = digest.build(_payload(
        bands=[{"symbol": "2330.TW", "price": 1000.0, "low": 900.0, "high": 1100.0}],
    ))
    md = digest.render_markdown(data)
    assert "波動區間" in md
    assert "目標價" not in md


def test_stale_fetch_raises_a_warning_in_the_header():
    md = digest.render_markdown(digest.build(
        _payload(as_of="2026-09-10", last_success="2026-09-01")))
    assert "抓取" in md
    assert "9" in md   # 落後天數


def test_fresh_fetch_has_no_warning():
    md = digest.render_markdown(digest.build(
        _payload(as_of="2026-09-10", last_success="2026-09-10")))
    assert "資料已過期" not in md


# --- 輸出 ---------------------------------------------------------------


def test_write_produces_markdown_and_parseable_json(tmp_path):
    data = digest.build(_payload(
        fires=[baserate.Fire("2330.TW", "TW", "a", "2026-09-10", 1000.0)],
        rates=[_rate("a")],
    ))
    md_path, json_path = digest.write(data, tmp_path)

    assert md_path.exists() and json_path.exists()
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["as_of"] == "2026-09-10"
    assert parsed["signals"]["edge"][0]["symbol"] == "2330.TW"


def test_digest_stays_within_size_budget(tmp_path):
    """1.7 MB 的 index.html 貼不進外部工具，日報必須小。"""
    fires = [baserate.Fire(f"S{i}.TW", "TW", "a", "2026-09-10", 100.0) for i in range(120)]
    data = digest.build(_payload(fires=fires, rates=[_rate("a")]))
    md_path, _ = digest.write(data, tmp_path)
    assert md_path.stat().st_size < digest.SIZE_BUDGET_BYTES
