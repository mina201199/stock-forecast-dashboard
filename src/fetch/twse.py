"""TWSE（上市）資料擷取。

注意：TWSE 憑證缺 Subject Key Identifier，必須經 src.http（requests）取得，
不可使用標準庫的 urllib 取用器（spec 9.3）。
"""
from __future__ import annotations

from src import constants as C
from src.http import fetch_json
from src.parsing import parse_num

BASE = "https://openapi.twse.com.tw/v1"


def to_symbol(code: str) -> str:
    return f"{code}.TW"


def fetch_day_all(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/exchangeReport/STOCK_DAY_ALL", limiter=limiter)
    return [
        {
            "code": row[C.TWSE_DAY_CODE],
            "name": row["Name"],
            "open": parse_num(row["OpeningPrice"]),
            "high": parse_num(row["HighestPrice"]),
            "low": parse_num(row["LowestPrice"]),
            "close": parse_num(row[C.TWSE_DAY_CLOSE]),
            "volume": parse_num(row["TradeVolume"], int),
            "trade_value": parse_num(row[C.TWSE_DAY_VALUE]),
        }
        for row in raw
    ]


def fetch_valuation(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/exchangeReport/BWIBBU_ALL", limiter=limiter)
    return [
        {
            "code": row[C.TWSE_DAY_CODE],
            "pe": parse_num(row[C.TWSE_BWIBBU_PE]),
            "dividend_yield": parse_num(row[C.TWSE_BWIBBU_YIELD]),
            "pb": parse_num(row[C.TWSE_BWIBBU_PB]),
        }
        for row in raw
    ]


def roc_to_iso(value: str | None) -> str | None:
    """民國日期字串 -> ISO。"1150817" -> "2026-08-17"。"""
    text = (value or "").strip()
    if len(text) != 7 or not text.isdigit():
        return None
    year = int(text[:3]) + 1911
    return f"{year:04d}-{text[3:5]}-{text[5:7]}"


def _roc_ym_to_iso(value: str | None) -> str | None:
    """民國年月 -> "YYYY-MM"。"11507" -> "2026-07"。"""
    text = (value or "").strip()
    if len(text) != 5 or not text.isdigit():
        return None
    return f"{int(text[:3]) + 1911:04d}-{text[3:5]}"


def fetch_monthly_revenue(session, limiter=None) -> list[dict]:
    """月營收。

    publish_date 取自「出表日期」，即實際公布日。特徵工程必須以此對齊，
    不可用「資料年月」，否則會用到當時尚未公布的資訊（spec 6.1 R3）。
    """
    raw = fetch_json(session, f"{BASE}/opendata/t187ap05_L", limiter=limiter)
    return [
        {
            "code": row[C.REV_CODE],
            "publish_date": roc_to_iso(row[C.REV_PUBLISH_DATE]),
            "period": _roc_ym_to_iso(row[C.REV_YM]),
            "industry": row.get(C.REV_INDUSTRY),
            "revenue": parse_num(row[C.REV_MONTH_REVENUE]),
            "yoy": parse_num(row[C.REV_YOY]),
            "mom": parse_num(row[C.REV_MOM]),
        }
        for row in raw
    ]


def fetch_exright(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/exchangeReport/TWT48U_ALL", limiter=limiter)
    return [
        {
            "code": row[C.TWSE_DAY_CODE],
            "ex_date": roc_to_iso(row[C.TWSE_EXRIGHT_DATE]),
            "type": (row.get(C.TWSE_EXRIGHT_TYPE) or "").strip() or None,
            "cash_dividend": parse_num(row.get(C.TWSE_EXRIGHT_CASH)),
            "stock_dividend": parse_num(row.get(C.TWSE_EXRIGHT_STOCK)),
        }
        for row in raw
    ]


def fetch_margin(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/exchangeReport/MI_MARGN", limiter=limiter)
    return [
        {
            "code": row[C.MARGIN_CODE],
            "margin_balance": parse_num(row[C.MARGIN_BALANCE_TODAY], int),
            "margin_balance_prev": parse_num(row[C.MARGIN_BALANCE_PREV], int),
            "short_balance": parse_num(row[C.SHORT_BALANCE_TODAY], int),
            "short_balance_prev": parse_num(row[C.SHORT_BALANCE_PREV], int),
        }
        for row in raw
    ]
