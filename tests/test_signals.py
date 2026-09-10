"""訊號層測試。

最重要的兩組：
1. 無前視性（沿用 test_features.py 的截斷不變性手法）
2. 觸發語意正確 —— 每個訊號都要有一個「該觸發」與一個「不該觸發」的案例
"""
import numpy as np
import pandas as pd
import pytest

from src import features, signals


def _frame(closes, volume=None, adj=None):
    n = len(closes)
    close = np.array(closes, dtype=float)
    return features.add_indicators(pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d"),
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close,
        "adjclose": np.array(adj if adj is not None else closes, dtype=float),
        "volume": np.array(volume if volume is not None else [1000.0] * n, dtype=float),
    }))


# --- registry -----------------------------------------------------------


def test_registry_is_not_empty():
    assert len(signals.REGISTRY) > 0


def test_registry_keys_are_unique():
    keys = [s.key for s in signals.REGISTRY]
    assert len(keys) == len(set(keys))


def test_every_signal_declares_a_family_and_markets():
    for s in signals.REGISTRY:
        assert s.family in {"technical", "flow", "fundamental", "event"}
        assert s.markets, f"{s.key} 未宣告適用市場"
        assert set(s.markets) <= {"TW", "US"}


# --- 無前視性：整個 registry 一律適用 -----------------------------------


@pytest.mark.parametrize("signal", signals.REGISTRY, ids=lambda s: s.key)
def test_no_lookahead_bias(signal):
    """截斷序列後，最後一根的觸發結果必須完全不變。

    任何用到未來資料的寫法（例如 shift(-1)、置中滾動、整段標準化）
    都會讓這個測試失敗。這是訊號層唯一不可妥協的性質 —— 掃描與回測
    跑同一段程式，只有在這個性質成立時才有意義。
    """
    rng = np.random.default_rng(42)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, 500)))
    volume = rng.integers(500, 5000, 500).astype(float)

    full = signal.fires(_frame(closes, volume=volume))
    truncated = signal.fires(_frame(closes[:400], volume=volume[:400]))

    # 比整段前綴而不只是最後一根：只比一根的話，兩邊剛好都 False 就會
    # 讓有前視偏誤的訊號僥倖過關。
    assert np.array_equal(full.to_numpy()[:400], truncated.to_numpy()), \
        f"{signal.key} 使用了未來資料"


@pytest.mark.parametrize("signal", signals.REGISTRY, ids=lambda s: s.key)
def test_fires_returns_boolean_series_aligned_to_input(signal):
    df = _frame(list(100 + np.arange(300, dtype=float)))
    out = signal.fires(df)
    assert len(out) == len(df)
    assert out.dtype == bool, f"{signal.key} 必須回傳 bool dtype，不得有 NaN"


@pytest.mark.parametrize("signal", signals.REGISTRY, ids=lambda s: s.key)
def test_short_series_fires_nothing_instead_of_raising(signal):
    """資料不足時所有指標是 NaN。訊號必須回傳全 False，不得拋錯或給 True。"""
    out = signal.fires(_frame([10.0, 11.0, 12.0]))
    assert not out.any()


# --- breakout_20d_vol2 --------------------------------------------------


def _signal(key):
    return next(s for s in signals.REGISTRY if s.key == key)


def test_breakout_20d_vol2_fires_on_new_high_with_volume_surge():
    # 前 60 根在 100 附近盤整，最後一根突破且爆量
    closes = [100.0] * 60 + [110.0]
    volume = [1000.0] * 60 + [3000.0]
    out = _signal("breakout_20d_vol2").fires(_frame(closes, volume=volume))
    assert bool(out.iloc[-1])


def test_breakout_20d_vol2_does_not_fire_without_volume_surge():
    """同樣的價格突破，但量沒放大 —— 不該觸發。"""
    closes = [100.0] * 60 + [110.0]
    volume = [1000.0] * 61
    out = _signal("breakout_20d_vol2").fires(_frame(closes, volume=volume))
    assert not bool(out.iloc[-1])


def test_breakout_20d_vol2_does_not_fire_without_new_high():
    """爆量但沒破高 —— 不該觸發。"""
    closes = [100.0] * 60 + [99.0]
    volume = [1000.0] * 60 + [3000.0]
    out = _signal("breakout_20d_vol2").fires(_frame(closes, volume=volume))
    assert not bool(out.iloc[-1])


# --- 其餘訊號的語意 ----------------------------------------------------


def test_breakout_52w_fires_only_above_prior_year_high():
    rising = _frame(list(100 + np.arange(300, dtype=float)))
    assert bool(_signal("breakout_52w").fires(rising).iloc[-1])

    falling = _frame(list(400 - np.arange(300, dtype=float)))
    assert not bool(_signal("breakout_52w").fires(falling).iloc[-1])


def test_oversold_rsi30_uptrend_needs_both_conditions():
    """長期均線向上但短期急殺 —— 該觸發。

    資料刻意設計成陡升後急跌 10 根：跌幅要夠深才能把 RSI14 壓到 30 以下，
    但又不能深到讓 ma20 跌破 ma60（那就變成單純的下跌趨勢，不該觸發）。
    """
    closes = list(100 + np.arange(250, dtype=float) * 2.0) + list(598 - np.arange(1, 11) * 8.0)
    out = _signal("oversold_rsi30_uptrend").fires(_frame(closes))
    assert bool(out.iloc[-1])


def test_oversold_rsi30_uptrend_does_not_fire_in_downtrend():
    """單純的下跌趨勢：RSI 低但長期均線也向下 —— 不該觸發。"""
    closes = list(400 - np.arange(300, dtype=float))
    out = _signal("oversold_rsi30_uptrend").fires(_frame(closes))
    assert not bool(out.iloc[-1])


def test_ma_stack_turn_fires_only_on_the_turning_bar():
    """由非多頭排列轉為多頭排列的那一根才觸發，之後持續多頭不再觸發。"""
    closes = list(200 - np.arange(150, dtype=float) * 0.5) + list(125 + np.arange(150) * 1.0)
    out = _signal("ma_stack_turn").fires(_frame(closes))
    fired = np.flatnonzero(out.to_numpy())
    assert len(fired) >= 1
    # 轉折點只有一次，不該每根都觸發
    assert out.sum() < 10


def test_near_high_strength_fires_near_52w_high():
    rising = _frame(list(100 + np.arange(300, dtype=float)))
    assert bool(_signal("near_high_strength").fires(rising).iloc[-1])


def test_near_high_strength_does_not_fire_far_below_high():
    closes = list(100 + np.arange(250, dtype=float)) + list(350 * 0.7 - np.arange(50) * 0.1)
    assert not bool(_signal("near_high_strength").fires(_frame(closes)).iloc[-1])
