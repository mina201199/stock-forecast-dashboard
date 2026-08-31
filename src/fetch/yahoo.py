"""Yahoo Finance：日線歷史（台美股共用）與美股基本面。"""
from __future__ import annotations

from datetime import UTC, datetime

from src.http import fetch_json

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _epoch_to_iso(ts: int | float) -> str:
    """epoch -> ISO 日期。

    以 UTC 換算。台股日線時間戳為 09:00 台北（01:00 UTC）、美股為當地開盤
    （13:30/14:30 UTC），兩者都落在同一日曆日，且與 Yahoo 自身 fmt 欄位一致。
    """
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")


def _chart_payload(session, symbol: str, range_: str, interval: str, limiter):
    payload = fetch_json(
        session,
        CHART_URL.format(symbol=symbol),
        params={"range": range_, "interval": interval, "events": "div,split"},
        limiter=limiter,
    )
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        raise RuntimeError(f"{symbol} chart 無結果")
    return results[0]


def fetch_chart(
    session, symbol: str, *, range_: str = "3y", interval: str = "1d", limiter=None
) -> list[dict]:
    """日線。必須帶 events=div,split 才會有 adjclose（spec 2.3）。"""
    result = _chart_payload(session, symbol, range_, interval, limiter)
    timestamps = result.get("timestamp") or []
    quote = (result["indicators"]["quote"] or [{}])[0]
    adj_block = (result["indicators"].get("adjclose") or [{}])[0]
    adjclose = adj_block.get("adjclose") or [None] * len(timestamps)

    rows = []
    for i, ts in enumerate(timestamps):
        close = quote["close"][i]
        adj = adjclose[i]
        if close is None or adj is None:
            continue  # 停牌日 Yahoo 回 null
        rows.append(
            {
                "symbol": symbol,
                "date": _epoch_to_iso(ts),
                "open": quote["open"][i],
                "high": quote["high"][i],
                "low": quote["low"][i],
                "close": close,
                "adjclose": adj,
                "volume": quote["volume"][i],
            }
        )
    return rows


def fetch_dividend_events(session, symbol: str, limiter=None) -> list[dict]:
    result = _chart_payload(session, symbol, "3y", "1d", limiter)
    dividends = (result.get("events") or {}).get("dividends") or {}
    return [
        {
            "symbol": symbol,
            "ex_date": _epoch_to_iso(item["date"]),
            "cash_dividend": item["amount"],
        }
        for item in sorted(dividends.values(), key=lambda d: d["date"])
    ]


SUMMARY_MODULES = [
    "summaryDetail",
    "defaultKeyStatistics",
    "financialData",
    "summaryProfile",
    "calendarEvents",
    "earningsTrend",
]


def _raw(block: dict | None, key: str):
    """Yahoo 以 {"raw":..., "fmt":...} 包裝數值，取 raw；純量則原樣回傳。"""
    if not block:
        return None
    value = block.get(key)
    if isinstance(value, dict):
        return value.get("raw")
    return value


def _iso_from_epoch_field(block: dict | None, key: str) -> str | None:
    value = _raw(block, key)
    return _epoch_to_iso(value) if isinstance(value, int | float) else None


def _forward_eps(summary: dict) -> float | None:
    for item in (summary.get("earningsTrend") or {}).get("trend") or []:
        if item.get("period") == "+1y":
            return _raw(item.get("earningsEstimate"), "avg")
    return None


def _earnings_date(summary: dict) -> str | None:
    dates = ((summary.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate") or []
    if not dates:
        return None
    first = dates[0]
    raw = first.get("raw") if isinstance(first, dict) else first
    return _epoch_to_iso(raw) if isinstance(raw, int | float) else None


def fetch_us_fundamentals(client, symbol: str) -> dict:
    summary = client.quote_summary(symbol, SUMMARY_MODULES)
    detail = summary.get("summaryDetail") or {}
    stats = summary.get("defaultKeyStatistics") or {}
    fin = summary.get("financialData") or {}
    profile = summary.get("summaryProfile") or {}
    return {
        "symbol": symbol,
        "pe": _raw(detail, "trailingPE"),
        "forward_pe": _raw(detail, "forwardPE"),
        "pb": _raw(stats, "priceToBook"),
        "dividend_yield": _raw(detail, "dividendYield"),
        "market_cap": _raw(detail, "marketCap"),
        "beta": _raw(detail, "beta"),
        "revenue_yoy": _raw(fin, "revenueGrowth"),
        "gross_margin": _raw(fin, "grossMargins"),
        "op_margin": _raw(fin, "operatingMargins"),
        "net_margin": _raw(fin, "profitMargins"),
        "roe": _raw(fin, "returnOnEquity"),
        "roa": _raw(fin, "returnOnAssets"),
        "debt_to_equity": _raw(fin, "debtToEquity"),
        "current_ratio": _raw(fin, "currentRatio"),
        "free_cashflow": _raw(fin, "freeCashflow"),
        "eps_forward": _forward_eps(summary),
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "ex_dividend_date": _iso_from_epoch_field(detail, "exDividendDate"),
        "earnings_date": _earnings_date(summary),
        "short_percent_float": _raw(stats, "shortPercentOfFloat"),
        "held_percent_institutions": _raw(stats, "heldPercentInstitutions"),
    }


def fetch_us_analyst(client, symbol: str) -> dict:
    fin = client.quote_summary(symbol, ["financialData"]).get("financialData") or {}
    return {
        "symbol": symbol,
        "target_mean": _raw(fin, "targetMeanPrice"),
        "target_high": _raw(fin, "targetHighPrice"),
        "target_low": _raw(fin, "targetLowPrice"),
        "recommendation": fin.get("recommendationKey"),
        "n_analysts": _raw(fin, "numberOfAnalystOpinions"),
    }
