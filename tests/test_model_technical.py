"""LightGBM 軌道的正確性，重點在防資料洩漏。

spec 6.1 的四條規則若違反，回測會漂亮到不真實 —— 這是整個專案
最容易自欺的地方，所以測試寫得比其他模組密。
"""
import numpy as np
import pandas as pd
import pytest

from src import features
from src.models import technical as T


def _panel(n_symbols=6, n_days=300, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B").strftime("%Y-%m-%d")
    frames = []
    for i in range(n_symbols):
        closes = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n_days)))
        frames.append(pd.DataFrame({
            "symbol": f"S{i}", "market": "TW", "date": dates,
            "adjclose": closes, "close": closes,
            "high": closes * 1.01, "low": closes * 0.99,
            "open": closes, "volume": rng.integers(1000, 9000, n_days),
        }))
    return pd.concat(frames, ignore_index=True)


def _benchmark(dates, seed=99):
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.010, len(dates))))
    return pd.DataFrame({"market": "TW", "date": dates, "adjclose": closes})


# --- 標籤定義 ---------------------------------------------------------


def test_forward_return_looks_exactly_horizon_ahead():
    series = pd.Series([100.0, 110.0, 121.0, 133.1])
    fwd = T.forward_return(series, 1)
    assert fwd.iloc[0] == pytest.approx(0.10)
    assert pd.isna(fwd.iloc[-1])  # 最後一根沒有未來


def test_forward_return_last_horizon_rows_are_nan():
    series = pd.Series(np.arange(1, 51, dtype=float))
    fwd = T.forward_return(series, 20)
    assert fwd.tail(20).isna().all()
    assert fwd.head(30).notna().all()


def test_label_is_excess_over_benchmark_not_absolute():
    """個股與基準同步時，超額報酬應為 0。"""
    dates = pd.date_range("2024-01-01", periods=60, freq="B").strftime("%Y-%m-%d")
    prices = 100 * np.exp(np.cumsum(np.full(60, 0.01)))
    panel = pd.DataFrame({"symbol": "A", "market": "TW", "date": dates, "adjclose": prices})
    bench = pd.DataFrame({"market": "TW", "date": dates, "adjclose": prices.copy()})
    y = T.build_labels(panel, bench, horizon=5).dropna()
    assert np.allclose(y.to_numpy(), 0.0, atol=1e-12)


def test_label_is_positive_when_stock_beats_benchmark():
    dates = pd.date_range("2024-01-01", periods=60, freq="B").strftime("%Y-%m-%d")
    panel = pd.DataFrame({"symbol": "A", "market": "TW", "date": dates,
                          "adjclose": 100 * np.exp(np.cumsum(np.full(60, 0.02)))})
    bench = pd.DataFrame({"market": "TW", "date": dates,
                          "adjclose": 100 * np.exp(np.cumsum(np.full(60, 0.01)))})
    y = T.build_labels(panel, bench, horizon=5).dropna()
    assert (y > 0).all()


def test_label_column_is_not_among_features():
    """標籤刻意看未來，混進特徵就是徹底的洩漏。"""
    for banned in ("y", "label", "forward_return", "fwd"):
        assert banned not in features.FEATURE_COLUMNS


# --- 時序切分與 purge -------------------------------------------------


def test_split_is_chronological_not_random():
    dates = pd.Series(pd.date_range("2024-01-01", periods=200, freq="B")
                      .strftime("%Y-%m-%d"))
    split = T.purged_split(dates, horizon=20)
    train_max = dates[split.train].max()
    valid_min = dates[split.valid].min()
    test_min = dates[split.test].min()
    assert train_max < valid_min < test_min


def test_purge_gap_is_at_least_horizon_trading_days():
    """spec 6.1 R2：訓練集尾端必須砍掉 horizon 天。"""
    unique = pd.date_range("2024-01-01", periods=200, freq="B").strftime("%Y-%m-%d")
    dates = pd.Series(unique)
    horizon = 20
    split = T.purged_split(dates, horizon=horizon)
    train_max = dates[split.train].max()
    valid_min = dates[split.valid].min()
    gap = list(unique).index(valid_min) - list(unique).index(train_max)
    assert gap >= horizon


def test_split_partitions_do_not_overlap():
    dates = pd.Series(pd.date_range("2024-01-01", periods=200, freq="B")
                      .strftime("%Y-%m-%d"))
    s = T.purged_split(dates, horizon=20)
    assert not (s.train & s.valid).any()
    assert not (s.valid & s.test).any()
    assert not (s.train & s.test).any()


def test_split_keeps_all_rows_of_a_date_together():
    """同一天的不同個股必須落在同一個分割，否則橫斷面資訊會跨界洩漏。"""
    panel = _panel(n_symbols=4, n_days=120)
    s = T.purged_split(panel["date"], horizon=10)
    for mask in (s.train, s.valid, s.test):
        subset = panel.loc[mask]
        for date, group in subset.groupby("date"):
            assert len(group) == len(panel[panel["date"] == date])


def test_zero_horizon_still_produces_valid_split():
    dates = pd.Series(pd.date_range("2024-01-01", periods=50, freq="B").strftime("%Y-%m-%d"))
    s = T.purged_split(dates, horizon=0)
    assert s.train.sum() > 0 and s.valid.sum() > 0


# --- 訓練與預測 -------------------------------------------------------


@pytest.fixture(scope="module")
def trained():
    panel = _panel(n_symbols=8, n_days=320, seed=4)
    enriched = panel.groupby("symbol", group_keys=False).apply(
        features.add_indicators, include_groups=False
    )
    enriched[["symbol", "market", "date"]] = panel[["symbol", "market", "date"]]
    bench = _benchmark(sorted(panel["date"].unique()))
    enriched["y"] = T.build_labels(
        enriched[["symbol", "market", "date", "adjclose"]], bench, horizon=20
    )
    usable = enriched.dropna(subset=["y"] + features.FEATURE_COLUMNS).reset_index(drop=True)
    split = T.purged_split(usable["date"], horizon=20)
    models = T.train_quantile_models(
        usable.loc[split.train, features.FEATURE_COLUMNS], usable.loc[split.train, "y"],
        usable.loc[split.valid, features.FEATURE_COLUMNS], usable.loc[split.valid, "y"],
        num_boost_round=60, early_stopping=20,
    )
    return models, usable, split


def test_trains_one_model_per_quantile(trained):
    models, _, _ = trained
    assert set(models) == set(T.QUANTILES)


def test_prediction_bounds_are_ordered(trained):
    """q10 <= q50 <= q90 必須成立，否則下游會算出負寬度的區間。"""
    models, usable, split = trained
    out = T.predict(models, usable.loc[split.test, features.FEATURE_COLUMNS])
    assert (out["tech_q10"] <= out["tech_q50"]).all()
    assert (out["tech_q50"] <= out["tech_q90"]).all()


def test_prediction_returns_one_row_per_input(trained):
    models, usable, split = trained
    x = usable.loc[split.test, features.FEATURE_COLUMNS]
    assert len(T.predict(models, x)) == len(x)


def test_predict_sorts_crossed_quantiles():
    class _Fake:
        def __init__(self, value):
            self.value = value

        def predict(self, x):
            return np.full(len(x), self.value)

    # 刻意交叉：q10 高於 q90
    models = {0.1: _Fake(0.5), 0.5: _Fake(0.2), 0.9: _Fake(-0.3)}
    out = T.predict(models, pd.DataFrame({"a": [1, 2, 3]}))
    assert (out["tech_q10"] <= out["tech_q50"]).all()
    assert (out["tech_q50"] <= out["tech_q90"]).all()
    assert out["tech_q10"].iloc[0] == pytest.approx(-0.3)


# --- 評估指標 ---------------------------------------------------------


def test_information_coefficient_is_one_for_perfect_ranking():
    p = pd.Series(np.arange(100, dtype=float))
    assert T.information_coefficient(p, p * 3 + 1) == pytest.approx(1.0)


def test_information_coefficient_is_negative_one_when_inverted():
    p = pd.Series(np.arange(100, dtype=float))
    assert T.information_coefficient(p, -p) == pytest.approx(-1.0)


def test_information_coefficient_none_for_tiny_sample():
    assert T.information_coefficient(pd.Series([1.0, 2.0]), pd.Series([1.0, 2.0])) is None


def test_quintile_returns_are_monotonic_for_perfect_predictor():
    p = pd.Series(np.arange(200, dtype=float))
    buckets = T.quintile_returns(p, p.copy())
    assert buckets == sorted(buckets)
    assert len(buckets) == 5


def test_quintile_returns_empty_for_small_sample():
    assert T.quintile_returns(pd.Series([1.0] * 10), pd.Series([1.0] * 10)) == []
