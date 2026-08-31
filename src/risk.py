"""客觀風險指標（spec 7.6）。

純量化描述，不含任何適合性判斷。輸出用於表格欄位與篩選條件，
供使用者自行判斷，不用來產生建議。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

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
