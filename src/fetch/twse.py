"""TWSE（上市）資料擷取。

注意：TWSE 憑證缺 Subject Key Identifier，必須經 src.http（requests）取得，
不可使用標準庫的 urllib 取用器（spec 9.3）。
"""
from __future__ import annotations

from datetime import date as _date
from datetime import timedelta

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


def gregorian_to_iso(value: str | None) -> str | None:
    """8 碼西元日期 -> ISO。"19620209" -> "1962-02-09"。

    t187ap03_L 在同一份 payload 內混用兩種格式：
    「出表日期」是 7 碼民國，「成立日期」「上市日期」是 8 碼西元。
    用錯解析器會靜默得到 None，故兩者分開實作且互不接受對方的長度。
    """
    text = (value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


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


T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"

# T86 回傳 fields/data 二維陣列。欄位以名稱定位，不寫死索引 ——
# 欄位順序若調整，寫死索引會靜默取到錯的數字。
INSTI_CODE_FIELD = "證券代號"
INSTI_FOREIGN_FIELD = "外陸資買賣超股數(不含外資自營商)"
INSTI_TRUST_FIELD = "投信買賣超股數"
INSTI_DEALER_FIELD = "自營商買賣超股數"


def fetch_institutional(
    session, date: str, limiter=None, lookback_days: int = 7
) -> list[dict]:
    """上市三大法人買賣超。

    TWSE 於收盤後才發布當日 T86，盤中查當日會得到
    「很抱歉，沒有符合條件的資料!」。若不往回找，交易日盤中執行就會
    完全拿不到籌碼資料。因此自當日起往回尋找最近一個有資料的交易日，
    並在每列標註實際資料日期 —— 不得謊報成執行當天。
    """
    start = _date.fromisoformat(date)
    payload = None
    actual = None
    for offset in range(lookback_days + 1):
        probe = (start - timedelta(days=offset)).isoformat()
        candidate = fetch_json(
            session,
            T86_URL,
            params={"date": probe.replace("-", ""), "selectType": "ALL",
                    "response": "json"},
            limiter=limiter,
        )
        if str(candidate.get("stat", "")).upper() == "OK":
            payload, actual = candidate, probe
            break
    if payload is None:
        return []

    fields = payload.get("fields") or []
    try:
        i_code = fields.index(INSTI_CODE_FIELD)
        i_foreign = fields.index(INSTI_FOREIGN_FIELD)
        i_trust = fields.index(INSTI_TRUST_FIELD)
        i_dealer = fields.index(INSTI_DEALER_FIELD)
    except ValueError as exc:
        raise RuntimeError(f"T86 欄位結構改變：{exc}") from exc

    return [
        {
            "code": (row[i_code] or "").strip(),
            "date": actual,
            "foreign_net": parse_num(row[i_foreign], int),
            "trust_net": parse_num(row[i_trust], int),
            "dealer_net": parse_num(row[i_dealer], int),
        }
        for row in payload.get("data") or []
    ]


def fetch_company_profiles(session, limiter=None) -> list[dict]:
    """上市公司基本資料。產業別在此為代碼（如 "24"），對照見 src/industry.py。"""
    raw = fetch_json(session, f"{BASE}/opendata/t187ap03_L", limiter=limiter)
    return [
        {
            "code": row[C.PROFILE_CODE],
            "short_name": row.get(C.PROFILE_SHORT_NAME),
            "industry_code": (row.get(C.PROFILE_INDUSTRY) or "").strip() or None,
            "listed_date": gregorian_to_iso(row.get("上市日期")),
        }
        for row in raw
    ]
