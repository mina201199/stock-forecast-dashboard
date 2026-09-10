"""技術面訊號（10 個）。

參數一律用慣例值寫死，**不對著結果最佳化**（spec §3.4）。理由：
若拿全歷史比較「20 日新高」與「30 日新高」再回報贏家的勝率，
那個勝率是搜出來的，不是量出來的。第一階段完全不搜參數，
所以基準率引擎報出的數字才有意義。

所有規則只使用 `≤ t` 的資料。`_prior_max` 刻意先 shift(1) 再 rolling ——
含當根的話 `adj > rolling.max()` 恆為 False（當根本身就在 max 裡面），
這個錯誤會讓突破類訊號永遠不觸發，而且不會拋錯，只會靜默地什麼都沒有。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.signals.base import Signal


def _prior_max(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    """前 window 根（**不含**當根）的最高值。"""
    return series.shift(1).rolling(window, min_periods=min_periods or window).max()


def _volume_vs_mean(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """當根量 ÷ 近 window 根均量。用當根量而非 features 的 volume_ratio ——
    後者是 5 日均量比 20 日均量，抓不到「今天單日爆量」。"""
    mean = df["volume"].rolling(window, min_periods=window).mean()
    return df["volume"] / mean.replace(0, np.nan)


# --- 突破類 -------------------------------------------------------------


def _breakout_20d_vol2(df: pd.DataFrame) -> pd.Series:
    adj = df["adjclose"]
    return (adj > _prior_max(adj, 20)) & (_volume_vs_mean(df, 20) >= 2.0)


def _breakout_52w(df: pd.DataFrame) -> pd.Series:
    adj = df["adjclose"]
    return adj > _prior_max(adj, 252, min_periods=60)


def _bb_squeeze_breakout(df: pd.DataFrame) -> pd.Series:
    """布林通道收窄後向上突破。

    「收窄」用**前一根**的頻寬判定，突破用當根 —— 否則突破本身會把頻寬
    撐開，條件互相矛盾，訊號幾乎不會觸發。
    """
    std20 = df["adjclose"].rolling(20, min_periods=20).std()
    bandwidth = (2 * std20) / df["ma20"].replace(0, np.nan)
    floor = bandwidth.rolling(120, min_periods=60).quantile(0.25)
    return (bandwidth.shift(1) <= floor.shift(1)) & (df["bb_position"] > 1.0)


# --- 回檔／超賣類 -------------------------------------------------------


def _pullback_ma20_uptrend(df: pd.DataFrame) -> pd.Series:
    return (
        (df["bullish_stack"] == 1.0)
        & (df["ma20_over_ma60"] > 0)
        & (df["bias_ma20"].abs() <= 0.02)
    )


def _oversold_rsi30_uptrend(df: pd.DataFrame) -> pd.Series:
    return (df["ma20_over_ma60"] > 0) & (df["rsi14"] < 30)


# --- 交叉類 -------------------------------------------------------------


def _macd_golden_above_zero(df: pd.DataFrame) -> pd.Series:
    hist = df["macd_hist"]
    return (hist > 0) & (hist.shift(1) <= 0) & (df["macd"] > 0)


def _kd_golden_oversold(df: pd.DataFrame) -> pd.Series:
    k, d = df["kd_k"], df["kd_d"]
    return (k > d) & (k.shift(1) <= d.shift(1)) & (d < 20)


def _ma_stack_turn(df: pd.DataFrame) -> pd.Series:
    stack = df["bullish_stack"]
    return (stack == 1.0) & (stack.shift(1) == 0.0)


# --- 量能類 -------------------------------------------------------------


def _volume_dryup_reversal(df: pd.DataFrame) -> pd.Series:
    vmean = df["volume"].rolling(20, min_periods=20).mean()
    dried_up = df["volume"].shift(1) <= 0.5 * vmean.shift(1)
    return dried_up & (df["ret_1d"] > 0.02) & (df["volume"] > vmean)


# --- 強勢延續 -----------------------------------------------------------


def _near_high_strength(df: pd.DataFrame) -> pd.Series:
    return (df["dist_from_high"] > -0.05) & (df["pos_52w"] > 0.9)


TECHNICAL: tuple[Signal, ...] = (
    Signal(
        key="breakout_20d_vol2", name="20 日新高＋量增 2 倍",
        family="technical", markets=("TW", "US"), rule=_breakout_20d_vol2,
        description="收盤突破前 20 根最高價，且當日量達 20 日均量 2 倍以上",
    ),
    Signal(
        key="breakout_52w", name="突破 52 週高點",
        family="technical", markets=("TW", "US"), rule=_breakout_52w,
        description="收盤突破前 252 根最高價",
        # 名字裡寫著 52 週就必須真的有 52 週。features 的 pos_52w 用
        # min_periods=60，第 60 根就給值 —— 那個值不是 52 週高點。
        min_bars=252,
    ),
    Signal(
        key="bb_squeeze_breakout", name="布林收窄後突破",
        family="technical", markets=("TW", "US"), rule=_bb_squeeze_breakout,
        description="前一根頻寬處於 120 日最低四分之一，當根收在上軌之外",
    ),
    Signal(
        key="pullback_ma20_uptrend", name="多頭排列回測 20 日均線",
        family="technical", markets=("TW", "US"), rule=_pullback_ma20_uptrend,
        description="均線多頭排列且收盤距 20 日均線 2% 以內",
    ),
    Signal(
        key="oversold_rsi30_uptrend", name="上升趨勢中 RSI 超賣",
        family="technical", markets=("TW", "US"), rule=_oversold_rsi30_uptrend,
        description="20 日均線在 60 日均線之上，且 RSI14 低於 30",
    ),
    Signal(
        key="macd_golden_above_zero", name="MACD 零軸上金叉",
        family="technical", markets=("TW", "US"), rule=_macd_golden_above_zero,
        description="柱狀體由負轉正且 MACD 線在零軸之上",
    ),
    Signal(
        key="kd_golden_oversold", name="KD 低位金叉",
        family="technical", markets=("TW", "US"), rule=_kd_golden_oversold,
        description="K 由下向上穿越 D，且 D 值低於 20",
    ),
    Signal(
        key="ma_stack_turn", name="轉為多頭排列",
        family="technical", markets=("TW", "US"), rule=_ma_stack_turn,
        description="由非多頭排列轉為 MA5 > MA20 > MA60 的那一根",
    ),
    Signal(
        key="volume_dryup_reversal", name="量縮後放量上攻",
        family="technical", markets=("TW", "US"), rule=_volume_dryup_reversal,
        description="前一根量縮至均量一半以下，當根放量且漲逾 2%",
    ),
    Signal(
        key="near_high_strength", name="貼近 52 週高的強勢",
        family="technical", markets=("TW", "US"), rule=_near_high_strength,
        description="距 52 週高點 5% 以內且位階高於 90%",
        min_bars=252,  # 同上：52 週的語意需要 52 週的資料
    ),
)
