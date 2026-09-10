"""客觀風險指標（spec 7.6）。

純量化描述，不含任何適合性判斷。輸出用於表格欄位與篩選條件，
供使用者自行判斷，不用來產生建議。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.numeric import is_positive

TRADING_DAYS = 252
LEVELS = ("極低", "低", "中", "高", "極高")


def annualized_volatility(adjclose: pd.Series, window: int = TRADING_DAYS) -> float | None:
    returns = adjclose.pct_change().dropna().tail(window)
    if len(returns) < 20:
        return None
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))


def downside_volatility(adjclose: pd.Series, window: int = TRADING_DAYS) -> float | None:
    """只取負報酬計算。同樣波動度下，下檔波動高者風險性質不同。"""
    returns = adjclose.pct_change().dropna().tail(window)
    negative = returns[returns < 0]
    if len(negative) < 10:
        return None
    return float(negative.std(ddof=1) * np.sqrt(TRADING_DAYS))


def beta(adjclose: pd.Series, benchmark: pd.Series, window: int = TRADING_DAYS) -> float | None:
    """對基準的迴歸斜率。兩序列需已按日期對齊。"""
    stock = adjclose.pct_change()
    bench = benchmark.pct_change()
    joined = pd.concat([stock, bench], axis=1, join="inner").dropna().tail(window)
    if len(joined) < 60:
        return None
    variance = joined.iloc[:, 1].var(ddof=1)
    if not variance or np.isclose(variance, 0.0):
        return None
    return float(joined.iloc[:, 0].cov(joined.iloc[:, 1]) / variance)


def max_drawdown(adjclose: pd.Series, window: int = TRADING_DAYS) -> float | None:
    series = adjclose.dropna().tail(window)
    if len(series) < 20:
        return None
    peak = series.cummax()
    return float((series / peak - 1).min())


def quintile_level(value: float | None, population: list[float]) -> str | None:
    """把數值放到全體分佈的五等分中，回傳中文分級。"""
    if value is None:
        return None
    valid = [v for v in population if v is not None and not np.isnan(v)]
    if len(valid) < 5:
        return None
    edges = np.quantile(valid, [0.2, 0.4, 0.6, 0.8])
    return LEVELS[int(np.searchsorted(edges, value, side="right"))]


def percentile_rank(value: float | None, population: list[float]) -> float | None:
    """該值在全體中的百分位（0-100）。頁面以中性敘述呈現。"""
    if value is None:
        return None
    valid = [v for v in population if v is not None and not np.isnan(v)]
    if not valid:
        return None
    return float(100.0 * np.mean([v <= value for v in valid]))


def compute(adjclose: pd.Series, benchmark: pd.Series | None, turnover: float | None) -> dict:
    return {
        "volatility_60d": annualized_volatility(adjclose, 60),
        "volatility_252d": annualized_volatility(adjclose, TRADING_DAYS),
        "downside_volatility": downside_volatility(adjclose),
        "beta": beta(adjclose, benchmark) if benchmark is not None else None,
        "max_drawdown": max_drawdown(adjclose),
        "turnover_ma20": turnover,
    }


# --- 波動區間 -----------------------------------------------------------

# 常態假設下約對應 10/90 百分位，與原本 low/high 的語意一致
BAND_K = 1.28


def volatility_band(*, price: float, annual_vol: float, horizon_days: int) -> dict | None:
    """以實現波動度推出的價格區間（spec §4.3）。

    **這是描述性的，不是預測。** 它取代原本 `compose._tech_prices()` 用
    LightGBM 分位數乘現價的做法 —— 那組分位數的校準從未驗證，實測產出
    「Adobe 一個月 +37.1%」「建通 +37.9%」這類數字，而它們的信心度全部
    都是 40（分歧上限與顯示門檻相等所致）。

    區間 = 現價 × (1 ± k·σ·√(N/252))，σ 取年化實現波動度。

    常態假設是明講的簡化：股價報酬有厚尾，實際覆蓋率會低於 80%。
    這句限制必須跟著區間一起顯示。

    波動度不可用時回 None，**不得退化成「區間等於現價」** ——
    那會把「不知道」呈現成「非常確定」。
    """
    if not is_positive(price) or not is_positive(annual_vol) or horizon_days <= 0:
        return None
    spread = BAND_K * float(annual_vol) * math.sqrt(horizon_days / TRADING_DAYS)
    return {
        "low": max(0.0, float(price) * (1.0 - spread)),
        "high": float(price) * (1.0 + spread),
        "spread": spread,
    }
