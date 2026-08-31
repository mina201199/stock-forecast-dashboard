"""技術指標與統一特徵表。

兩條不可違反的規則：
1. 所有報酬與指標一律以 adjclose 計算（spec 2.3）。用 close 會把除息
   當成暴跌，高股息股一年會被灌入一次假訊號。
2. 任何指標在第 i 根的值只能取決於第 i 根（含）以前的資料。
   測試以「截斷序列後最後一根的值不變」驗證這點。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RETURN_WINDOWS = (1, 5, 20, 60, 120)
MA_WINDOWS = (5, 20, 60, 120)


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder 平滑，等同 alpha = 1/period 的 EMA。RSI 與 ATR 皆用此法。"""
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rsi(adjclose: pd.Series, period: int = 14) -> pd.Series:
    delta = adjclose.diff()
    gain = _wilder(delta.clip(lower=0), period)
    loss = _wilder(-delta.clip(upper=0), period)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # loss 為 0 代表區間內只漲不跌，RSI 定義上為 100
    return out.mask((loss == 0) & (gain > 0), 100.0)


def macd(
    adjclose: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    ema_fast = adjclose.ewm(span=fast, adjust=False).mean()
    ema_slow = adjclose.ewm(span=slow, adjust=False).mean()
    line = ema_fast - ema_slow
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 9
) -> pd.DataFrame:
    lowest = low.rolling(period, min_periods=period).min()
    highest = high.rolling(period, min_periods=period).max()
    span = (highest - lowest).replace(0, np.nan)
    rsv = (close - lowest) / span * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    return pd.DataFrame({"kd_k": k, "kd_d": d})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return _wilder(true_range, period)


def add_indicators(prices: pd.DataFrame) -> pd.DataFrame:
    """輸入需含 date/open/high/low/close/adjclose/volume 且依 date 遞增。"""
    df = prices.copy()
    adj = df["adjclose"]

    for window in RETURN_WINDOWS:
        df[f"ret_{window}d"] = adj.pct_change(window)

    for window in MA_WINDOWS:
        df[f"ma{window}"] = adj.rolling(window, min_periods=window).mean()

    df["bias_ma20"] = adj / df["ma20"] - 1
    df["ma5_over_ma20"] = df["ma5"] / df["ma20"] - 1
    df["ma20_over_ma60"] = df["ma20"] / df["ma60"] - 1
    df["bullish_stack"] = (
        (df["ma5"] > df["ma20"]) & (df["ma20"] > df["ma60"])
    ).astype(float)

    df["rsi14"] = rsi(adj)
    df = df.join(macd(adj))
    # KD 用還原後的高低價，維持與 adjclose 同一尺度
    ratio = (adj / df["close"]).replace([np.inf, -np.inf], np.nan)
    df = df.join(stochastic(df["high"] * ratio, df["low"] * ratio, adj))

    df["atr14"] = atr(df["high"] * ratio, df["low"] * ratio, adj)
    df["atr_pct"] = df["atr14"] / adj
    df["volatility_20d"] = adj.pct_change().rolling(20, min_periods=20).std() * np.sqrt(252)

    std20 = adj.rolling(20, min_periods=20).std()
    df["bb_position"] = (adj - df["ma20"]) / (2 * std20).replace(0, np.nan)

    vol_ma5 = df["volume"].rolling(5, min_periods=5).mean()
    vol_ma20 = df["volume"].rolling(20, min_periods=20).mean()
    df["volume_ratio"] = vol_ma5 / vol_ma20.replace(0, np.nan)
    df["turnover_ma20"] = (df["volume"] * df["close"]).rolling(20, min_periods=20).mean()

    high_252 = adj.rolling(252, min_periods=60).max()
    low_252 = adj.rolling(252, min_periods=60).min()
    band = (high_252 - low_252).replace(0, np.nan)
    df["pos_52w"] = (adj - low_252) / band
    df["dist_from_high"] = adj / high_252 - 1
    df["dist_from_low"] = adj / low_252 - 1

    return df


FEATURE_COLUMNS = [
    *[f"ret_{w}d" for w in RETURN_WINDOWS],
    "bias_ma20", "ma5_over_ma20", "ma20_over_ma60", "bullish_stack",
    "rsi14", "macd", "macd_signal", "macd_hist", "kd_k", "kd_d",
    "atr_pct", "volatility_20d", "bb_position",
    "volume_ratio", "pos_52w", "dist_from_high", "dist_from_low",
]
