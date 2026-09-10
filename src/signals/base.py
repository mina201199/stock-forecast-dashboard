"""訊號的核心物件。

**為什麼 `fires` 是純函數**

因為「今天掃描」和「三年回測」必須執行同一段程式。掃描與回測若是
兩份實作，它們遲早會不一致 —— 而不一致的方向永遠是回測看起來比較準
（回測是拿來說服自己的，掃描是拿來下單的，人會不自覺地讓前者好看）。

一個吃 DataFrame、回傳 bool Series 的純函數，讓這件事在結構上不可能發生：
`baserate.py` 走三年歷史和 `analysis.py` 掃今天，呼叫的是同一個 `fires`。

**為什麼 NaN 一律變成 False**

資料不足時所有指標都是 NaN。NaN 在比較運算下已經是 False，但 `bullish_stack`
這類先轉成 float 的欄位可能留下 NaN。「沒有資料」必須是「沒有觸發」，
絕不能是觸發 —— 這是 spec 9.4 那個 NaN 陷阱的同一條教訓。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

FAMILIES = ("technical", "flow", "fundamental", "event")


DEFAULT_MIN_BARS = 60


@dataclass(frozen=True)
class Signal:
    key: str
    name: str
    family: str
    markets: tuple[str, ...]
    rule: Callable[[pd.DataFrame], pd.Series]
    description: str = ""
    min_bars: int = DEFAULT_MIN_BARS

    def fires(self, df: pd.DataFrame) -> pd.Series:
        """回傳每根 bar 是否觸發。保證 bool dtype，不含 NaN，且前 min_bars 根恆為 False。

        暖機遮罩不是保險，是必要的。`features.macd()` 用
        `ewm(adjust=False)` 且未設 min_periods，所以 MACD 從第 0 根就有值 ——
        兩條 EMA 起始相等使柱狀體為 0，價格一漲，第 1 根的柱狀體就由 0
        轉正，`hist.shift(1) <= 0` 在那個種子零上成立。實測 3 根 bar 的
        序列就會觸發「MACD 零軸上金叉」。

        EMA 類指標沒有 NaN 可以擋，所以只能用位置擋。遮罩依位置而非日期，
        因此截斷序列不會改變任何一根的結果（前視偏誤測試因此仍然成立）。
        """
        out = self.rule(df)
        if out.dtype != bool:
            out = out.fillna(False).astype(bool)
        if self.min_bars > 0 and len(out) > 0:
            out = out.copy()
            out.iloc[: self.min_bars] = False
        return out
