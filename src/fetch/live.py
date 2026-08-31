"""即時報價（spec 2.7）。

台股走 TWSE MIS（delay=0，真即時）；美股走 Yahoo 分鐘線（延遲約 15 分）。
兩個關鍵限制：
1. 必須先 GET index.jsp 建立 session，直接打 API 會被拒（spec 9.3）
2. 成交價 z 在無成交時為字串 "-"，直接 float() 會 ValueError（spec 9.3）
"""
from __future__ import annotations

import requests

from src.http import fetch_json
from src.parsing import parse_num

MIS_SESSION_URL = "https://mis.twse.com.tw/stock/index.jsp"
MIS_API_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


def build_ex_ch(symbols: list[str]) -> str:
    parts = []
    for symbol in symbols:
        code, _, suffix = symbol.partition(".")
        prefix = "otc" if suffix.upper() == "TWO" else "tse"
        parts.append(f"{prefix}_{code}.tw")
    return "|".join(parts)


def parse_mis(payload: dict) -> list[dict]:
    if str(payload.get("rtcode")) != "0000":
        return []
    out = []
    for row in payload.get("msgArray") or []:
        traded = parse_num(row.get("z"))
        prev_close = parse_num(row.get("y"))
        price = traded if traded is not None else prev_close
        if price is None:
            continue
        out.append(
            {
                "code": row.get("c"),
                "name": row.get("n"),
                "price": price,
                "prev_close": prev_close,
                "open": parse_num(row.get("o")),
                "high": parse_num(row.get("h")),
                "low": parse_num(row.get("l")),
                "volume": parse_num(row.get("v"), int),
                "time": row.get("t"),
                "is_stale": traded is None,
            }
        )
    return out


def fetch_mis(session, symbols: list[str], limiter=None) -> list[dict]:
    try:
        session.get(MIS_SESSION_URL, timeout=20)
    except requests.RequestException:
        pass  # cookie 端點失敗時仍嘗試 API
    payload = fetch_json(
        session,
        MIS_API_URL,
        params={"ex_ch": build_ex_ch(symbols), "json": "1", "delay": "0"},
        limiter=limiter,
    )
    return parse_mis(payload)
