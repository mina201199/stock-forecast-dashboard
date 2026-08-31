import numpy as np
import pandas as pd
import pytest

from src import features


def _series(closes, adj=None, volume=None):
    n = len(closes)
    close = np.array(closes, dtype=float)
    adjclose = np.array(adj if adj is not None else closes, dtype=float)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d"),
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "adjclose": adjclose,
        "volume": np.array(volume if volume is not None else [1000] * n, dtype=float),
    })


# --- 無前視性：最重要的性質 -------------------------------------------


@pytest.mark.parametrize("column", features.FEATURE_COLUMNS)
def test_no_lookahead_bias(column):
    """截斷序列後，最後一根的指標值必須完全不變。

    任何用到未來資料的寫法（例如置中滾動、整段標準化）都會讓這個測試失敗。
    """
    rng = np.random.default_rng(42)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, 400)))
    volume = rng.integers(500, 5000, 400)

    full = features.add_indicators(_series(closes, volume=volume))
    truncated = features.add_indicators(_series(closes[:300], volume=volume[:300]))

    a = full[column].iloc[299]
    b = truncated[column].iloc[299]
    if pd.isna(a) and pd.isna(b):
        return
    assert a == pytest.approx(b, rel=1e-9), f"{column} 使用了未來資料"


# --- 指標正確性 -------------------------------------------------------


def test_uses_adjclose_not_close():
    """close 與 adjclose 不同時，報酬必須依 adjclose 計算。"""
    df = features.add_indicators(_series([100, 100, 100], adj=[100, 110, 121]))
    assert df["ret_1d"].iloc[1] == pytest.approx(0.10)
    assert df["ret_1d"].iloc[2] == pytest.approx(0.10)


def test_moving_average_matches_manual_mean():
    closes = list(range(1, 31))
    df = features.add_indicators(_series(closes))
    assert df["ma5"].iloc[9] == pytest.approx(np.mean(closes[5:10]))
    assert pd.isna(df["ma5"].iloc[3])  # 資料不足時不得給值


def test_rsi_is_100_for_monotonic_rise():
    df = features.add_indicators(_series(list(np.arange(1, 60, dtype=float))))
    assert df["rsi14"].iloc[-1] == pytest.approx(100.0)


def test_rsi_is_0_for_monotonic_fall():
    df = features.add_indicators(_series(list(np.arange(60, 1, -1, dtype=float))))
    assert df["rsi14"].iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_stays_within_bounds_on_random_walk():
    rng = np.random.default_rng(7)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    r = features.add_indicators(_series(closes))["rsi14"].dropna()
    assert r.between(0, 100).all()


def test_macd_hist_is_line_minus_signal():
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    df = features.add_indicators(_series(closes))
    diff = df["macd"] - df["macd_signal"]
    assert np.allclose(df["macd_hist"].dropna(), diff.dropna())


def test_bullish_stack_flags_only_when_all_three_align():
    rising = features.add_indicators(_series(list(np.arange(1, 200, dtype=float))))
    assert rising["bullish_stack"].iloc[-1] == 1.0
    falling = features.add_indicators(_series(list(np.arange(200, 1, -1, dtype=float))))
    assert falling["bullish_stack"].iloc[-1] == 0.0


def test_pos_52w_is_one_at_new_high_and_zero_at_new_low():
    rising = features.add_indicators(_series(list(np.arange(1, 300, dtype=float))))
    assert rising["pos_52w"].iloc[-1] == pytest.approx(1.0)
    assert rising["dist_from_high"].iloc[-1] == pytest.approx(0.0)
    falling = features.add_indicators(_series(list(np.arange(300, 1, -1, dtype=float))))
    assert falling["pos_52w"].iloc[-1] == pytest.approx(0.0)


def test_volume_ratio_detects_surge():
    volume = [1000] * 40 + [5000] * 5
    df = features.add_indicators(_series(list(np.linspace(100, 120, 45)), volume=volume))
    assert df["volume_ratio"].iloc[-1] > 2.0


def test_flat_series_produces_no_infinities():
    """定價不動時分母會是 0，必須成為 NaN 而不是 inf。"""
    df = features.add_indicators(_series([50.0] * 300))
    numeric = df[features.FEATURE_COLUMNS]
    assert not np.isinf(numeric.to_numpy(dtype=float)).any()


def test_short_series_returns_nan_not_error():
    df = features.add_indicators(_series([10.0, 11.0, 12.0]))
    assert pd.isna(df["ma20"].iloc[-1])
    assert pd.isna(df["pos_52w"].iloc[-1])
