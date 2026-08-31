"""數值解析工具。

台灣的資料源用不同方式表示「沒有資料」：
- TWSE OpenAPI：空字串（例如虧損公司的本益比）
- TWSE MIS：字串 "-"（無成交時的成交價）
- 借券餘額：帶千分位逗號的數字字串

一律轉成 None 而非 0 —— 把「沒有資料」當成 0 會讓模型學到錯的東西
（虧損公司的 PE 被當成 0 會排進「最便宜」的一端）。
"""
from __future__ import annotations

from collections.abc import Callable

_MISSING = {"", "-", "--", "N/A", "n/a", "null", "None"}


def parse_num[T: (int, float)](
    value: object, cast: Callable[[str], T] = float
) -> T | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in _MISSING:
        return None
    try:
        return cast(text)
    except (TypeError, ValueError):
        return None
