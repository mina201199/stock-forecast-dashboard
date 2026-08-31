"""TPEx（上櫃）資料擷取。

TPEx 三大法人端點的欄位名含不一致空格（前導空格、中間空格），
直接以字面 key 取值會 KeyError，故一律先正規化欄位名（spec 9.3）。
"""
from __future__ import annotations

import re

from src.fetch.twse import roc_to_iso as _roc_to_iso
from src.http import fetch_json
from src.parsing import parse_num

BASE = "https://www.tpex.org.tw/openapi/v1"

_WS = re.compile(r"\s+")


def to_symbol(code: str) -> str:
    return f"{code}.TWO"


def normalize_key(key: str) -> str:
    """去除所有空白並轉小寫，讓帶空格的變體對應到同一個鍵。"""
    return _WS.sub("", key).lower()


def normalize_row(row: dict) -> tuple[dict, set[str]]:
    """正規化欄位名，並回報碰撞。

    實測 TPEx 三大法人同時存在 "Dealers-TotalSell" 與 "Dealers -TotalSell"，
    正規化後會撞成同一鍵。保留先出現者，並回報碰撞集合，
    避免後者靜默覆蓋前者而讓下游拿到錯的數字。
    """
    normalized: dict = {}
    collisions: set[str] = set()
    for key, value in row.items():
        norm = normalize_key(key)
        if norm in normalized:
            collisions.add(norm)
            continue
        normalized[norm] = value
    return normalized, collisions


def _normalized(row: dict) -> dict:
    return normalize_row(row)[0]


def fetch_quotes(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/tpex_mainboard_daily_close_quotes", limiter=limiter)
    out = []
    for row in raw:
        r = _normalized(row)
        out.append(
            {
                "code": r["securitiescompanycode"],
                "name": r.get("companyname"),
                "open": parse_num(r.get("open")),
                "high": parse_num(r.get("high")),
                "low": parse_num(r.get("low")),
                "close": parse_num(r.get("close")),
                "volume": parse_num(r.get("tradingshares"), int),
                "trade_value": parse_num(r.get("transactionamount")),
            }
        )
    return out


_FOREIGN_DIFF = normalize_key(
    "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference"
)
_TRUST_DIFF = normalize_key("SecuritiesInvestmentTrustCompanies-Difference")
_DEALER_DIFF = normalize_key("Dealers-Difference")


def fetch_institutional(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/tpex_3insti_daily_trading", limiter=limiter)
    out = []
    for row in raw:
        r = _normalized(row)
        out.append(
            {
                "code": r["securitiescompanycode"],
                "foreign_net": parse_num(r.get(_FOREIGN_DIFF), int),
                "trust_net": parse_num(r.get(_TRUST_DIFF), int),
                "dealer_net": parse_num(r.get(_DEALER_DIFF), int),
            }
        )
    return out


def fetch_company_profiles(session, limiter=None) -> list[dict]:
    """上櫃公司基本資料。

    產業別只給代碼（SecuritiesIndustryCode），沒有中文名稱，
    需經 src/industry.py 由上市資料推導出的對照表轉換。
    """
    raw = fetch_json(session, f"{BASE}/mopsfin_t187ap03_O", limiter=limiter)
    out = []
    for row in raw:
        r = _normalized(row)
        out.append(
            {
                "code": r["securitiescompanycode"],
                "short_name": r.get("companyabbreviation"),
                "industry_code": (r.get("securitiesindustrycode") or "").strip() or None,
                "listed_date": _roc_to_iso(r.get("dateoflisting")),
            }
        )
    return out
