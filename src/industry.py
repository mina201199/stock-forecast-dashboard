"""產業分類代碼對照。

TWSE 與 TPEx 對「產業別」給的格式不同：
- t187ap05_L（上市月營收）：中文全名，例如「半導體業」
- t187ap03_L（上市公司基本資料）：代碼，例如「24」
- mopsfin_t187ap03_O（上櫃公司基本資料）：只有代碼

兩份上市資料都以公司代號為鍵，join 之後即可推導出權威的
代碼→中文對照，再套用到只給代碼的上櫃資料。
這樣不需要在程式裡硬寫一張會隨主管機關調整而過時的對照表。
"""
from __future__ import annotations

from collections import Counter, defaultdict


def build_code_map(profiles: list[dict], revenue_rows: list[dict]) -> dict[str, str]:
    """由上市資料推導 產業代碼 -> 中文名稱。"""
    names_by_code = {
        row["code"]: (row.get("industry") or "").strip()
        for row in revenue_rows
        if row.get("code") and (row.get("industry") or "").strip()
    }
    votes: dict[str, Counter] = defaultdict(Counter)
    for profile in profiles:
        code = (profile.get("industry_code") or "").strip()
        name = names_by_code.get(profile.get("code"))
        if code and name:
            votes[code][name] += 1
    return {code: counter.most_common(1)[0][0] for code, counter in votes.items()}


def resolve(text: str | None, code: str | None, code_map: dict[str, str]) -> str | None:
    """優先用現成的中文；沒有才用代碼查表。"""
    cleaned = (text or "").strip()
    if cleaned:
        return cleaned
    return code_map.get((code or "").strip()) or None
