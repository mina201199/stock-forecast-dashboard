"""集保戶股權分散表（大戶持股）。

回傳為 CSV 而非 JSON，且帶 UTF-8 BOM，必須以 utf-8-sig 解碼（spec 9.3）。
用一般 utf-8 解會讓第一個欄位名前面黏著 U+FEFF，之後 DictReader 取
"資料日期" 就會 KeyError —— 而且是靜默的，因為那個字元看不見。
持股分級 15 代表 400 張以上，即一般所稱「大戶」。資料為週更。
"""
from __future__ import annotations

import csv
import io

from src import constants as C
from src.http import fetch_text
from src.parsing import parse_num

URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"


def _iso(compact: str) -> str | None:
    text = (compact or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def parse_big_holders(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    out = []
    for row in reader:
        if (row.get(C.TDCC_LEVEL) or "").strip() != C.TDCC_BIG_HOLDER_LEVEL:
            continue
        pct = parse_num(row.get(C.TDCC_PCT))
        code = (row.get(C.TDCC_CODE) or "").strip()
        date = _iso(row.get(C.TDCC_DATE, ""))
        if pct is None or not code or date is None:
            continue
        out.append({"code": code, "date": date, "big_holder_pct": pct})
    return out


def fetch_big_holders(session, limiter=None) -> list[dict]:
    text = fetch_text(session, URL, limiter=limiter, encoding="utf-8-sig", timeout=120)
    return parse_big_holders(text)
