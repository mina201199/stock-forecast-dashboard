"""數值防護。

存在的理由是實跑時踩到的一連串 NaN 陷阱：SQLite 的 NULL 經 pandas
變成 NaN，而 Python 對 NaN 的行為會讓一般寫法靜默出錯：

    not float("nan")            -> False   （守衛 `if not x` 放它過去）
    float("nan") <= 0           -> False   （範圍檢查放它過去）
    all([nan, nan])             -> True    （完整性檢查放它過去）
    max(0.0, min(100.0, nan))   -> 100.0   （夾取把它變成上界！）

最後一條最致命：它把「沒有資料」轉成「滿分」。所有數值守衛與夾取
都必須經過本模組，不得直接用真值判斷或 min/max。
"""
from __future__ import annotations

import math


def is_finite(value: object) -> bool:
    """僅在 value 是有限的實數時為 True。None、NaN、±Inf 皆為 False。"""
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def is_positive(value: object) -> bool:
    return is_finite(value) and float(value) > 0.0


def clamp(value: object, low: float, high: float) -> float | None:
    """夾取到 [low, high]。非有限值回 None，絕不回上下界。"""
    if not is_finite(value):
        return None
    return max(low, min(high, float(value)))


def all_finite(values) -> bool:
    return all(is_finite(v) for v in values)
