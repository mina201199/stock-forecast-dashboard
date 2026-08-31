"""選股池決定（spec 1.2）。

台股 300 檔以成交值排序、美股 200 檔取 S&P 500 前段。
ETF／權證／特別股／TDR 不納入 —— 它們沒有可比較的基本面，
放進去會讓估值模型產生無意義的目標價。
"""
from __future__ import annotations

import re

from lxml import html as lxml_html

from src.fetch import tpex, twse

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# 台股普通股為 4 碼數字且不以 0 開頭；
# 0 開頭是 ETF、5 碼以上帶英文是權證/特別股、6 碼是 TDR。
_COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")


def is_common_stock(code: str) -> bool:
    return bool(_COMMON_STOCK.match((code or "").strip()))


def select_taiwan(twse_rows: list[dict], tpex_rows: list[dict], limit: int) -> list[dict]:
    candidates = []
    for row in twse_rows:
        if is_common_stock(row["code"]):
            candidates.append((row, twse.to_symbol(row["code"])))
    for row in tpex_rows:
        if is_common_stock(row["code"]):
            candidates.append((row, tpex.to_symbol(row["code"])))

    candidates.sort(key=lambda pair: pair[0].get("trade_value") or 0.0, reverse=True)
    return [
        {"symbol": symbol, "code": row["code"], "market": "TW", "name": row.get("name")}
        for row, symbol in candidates[:limit]
    ]


def parse_sp500(html_text: str) -> list[dict]:
    tree = lxml_html.fromstring(html_text)
    out = []
    for tr in tree.xpath("//table[contains(@class,'wikitable')]//tr"):
        cells = tr.xpath("./td")
        if len(cells) < 3:
            continue
        code = cells[0].text_content().strip().replace(".", "-")  # Yahoo 用 BRK-B
        if not code:
            continue
        out.append(
            {
                "code": code,
                "name": cells[1].text_content().strip(),
                "sector": cells[2].text_content().strip(),
            }
        )
    return out


def select_us(rows: list[dict], limit: int) -> list[dict]:
    return [
        {
            "symbol": row["code"], "code": row["code"], "market": "US",
            "name": row.get("name"), "sector": row.get("sector"),
        }
        for row in rows[:limit]
    ]
