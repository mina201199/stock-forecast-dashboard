"""訊號 registry。

新增一個訊號＝在對應的家族模組寫一個規則函式，再加進該模組的 tuple。
`REGISTRY` 是唯一的真相來源：掃描、回測、多重檢定校正的分母都取自它。

第一階段只有技術面。籌碼、基本面、事件驅動三個家族的資料庫裡各只有
一個日期的快照（見 spec §1.3），連「異動」與「轉折」都定義不出來，
所以刻意不放進 registry —— 放進去就得給勝率，而給不出勝率的訊號
不得出貨（spec §2.5）。
"""
from __future__ import annotations

from src.signals.base import FAMILIES, Signal
from src.signals.technical import TECHNICAL

REGISTRY: tuple[Signal, ...] = TECHNICAL

BY_KEY: dict[str, Signal] = {s.key: s for s in REGISTRY}

__all__ = ["FAMILIES", "REGISTRY", "BY_KEY", "Signal"]
