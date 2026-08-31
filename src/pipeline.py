"""抓取流程組裝。

核心原則（spec 9.1）：任何單一來源失效都不得讓整份報表產不出來。
因此每個批次來源與每檔個股的抓取都各自包在 try 內，失敗只記錄、不中斷。
"""
from __future__ import annotations

from collections.abc import Callable

import config
from src import industry, store
from src.dates import market_date, utc_now_iso
from src.fetch import news, tdcc, tpex, twse, universe, yahoo
from src.http import fetch_text


def _index(rows: list[dict], key: str = "code") -> dict[str, dict]:
    return {row[key]: row for row in rows if row.get(key)}


def _safe(label: str, stats: dict, log: Callable[[str], None], fn, *args, **kwargs):
    """執行批次抓取；失敗時回空並記入 degraded，不讓整體中斷。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        stats["degraded"].append(f"{label}: {exc}")
        log(f"  ! {label} 取得失敗，該區塊將標示為過期：{exc}")
        return []


def fetch_all(
    conn, session, *, tw_limit: int, us_limit: int, yahoo_client=None, progress=None
) -> dict:
    stats: dict = {"failed": [], "degraded": [], "universe": 0}

    def log(message: str) -> None:
        if progress:
            progress(message)

    tw_today = market_date("TW")

    # --- 選股池 ---
    log("抓取台股全市場報價…")
    twse_rows = _safe("twse.day_all", stats, log, twse.fetch_day_all, session)
    tpex_rows = _safe("tpex.quotes", stats, log, tpex.fetch_quotes, session)
    tw_picks = universe.select_taiwan(twse_rows, tpex_rows, tw_limit)

    log("抓取 S&P 500 成分股…")
    try:
        sp500 = universe.parse_sp500(fetch_text(session, universe.SP500_URL))
    except Exception as exc:
        stats["degraded"].append(f"universe.sp500: {exc}")
        log(f"  ! S&P 500 清單取得失敗：{exc}")
        sp500 = []
    us_picks = universe.select_us(sp500, us_limit)

    # --- 台股全市場批次（不逐檔請求，spec 4）---
    log("抓取台股估值／營收／產業／除權息／融資融券／籌碼…")
    valuation = _index(_safe("twse.valuation", stats, log, twse.fetch_valuation, session))
    revenue_rows = _safe("twse.revenue", stats, log, twse.fetch_monthly_revenue, session)
    revenue = _index(revenue_rows)
    exright = _index(_safe("twse.exright", stats, log, twse.fetch_exright, session))
    margin = _index(_safe("twse.margin", stats, log, twse.fetch_margin, session))
    holders = _index(_safe("tdcc.holders", stats, log, tdcc.fetch_big_holders, session))

    # 三大法人：上市走 T86、上櫃走 TPEx，兩份合併
    institutional = _index(
        _safe("twse.institutional", stats, log, twse.fetch_institutional, session, tw_today)
    )
    institutional.update(
        _index(_safe("tpex.institutional", stats, log, tpex.fetch_institutional, session))
    )

    # 產業分類：上市月營收直接給中文；上櫃只給代碼，靠上市資料推導對照表
    twse_profiles = _safe("twse.profiles", stats, log, twse.fetch_company_profiles, session)
    tpex_profiles = _safe("tpex.profiles", stats, log, tpex.fetch_company_profiles, session)
    code_map = industry.build_code_map(twse_profiles, revenue_rows)
    profiles = _index(twse_profiles)
    profiles.update(_index(tpex_profiles))

    store.upsert(conn, "stocks", [
        {"symbol": p["symbol"], "market": p["market"], "code": p["code"],
         "name": p.get("name"), "name_en": p.get("name") if p["market"] == "US" else None,
         "sector": p.get("sector") if p["market"] == "US" else industry.resolve(
             (revenue.get(p["code"]) or {}).get("industry"),
             (profiles.get(p["code"]) or {}).get("industry_code"), code_map),
         "industry": p.get("sector") if p["market"] == "US" else industry.resolve(
             (revenue.get(p["code"]) or {}).get("industry"),
             (profiles.get(p["code"]) or {}).get("industry_code"), code_map),
         "currency": "TWD" if p["market"] == "TW" else "USD",
         "listed_date": (profiles.get(p["code"]) or {}).get("listed_date"),
         "updated_at": utc_now_iso()}
        for p in tw_picks + us_picks
    ])
    stats["universe"] = len(tw_picks) + len(us_picks)

    tw_fundamentals, tw_dividends, tw_margin, tw_holders, tw_insti = [], [], [], [], []
    for pick in tw_picks:
        code, symbol = pick["code"], pick["symbol"]
        val, rev = valuation.get(code, {}), revenue.get(code, {})
        # asof 用營收公布日；沒有營收資料時退回今日（spec 6.1 R3）
        asof = rev.get("publish_date") or tw_today
        tw_fundamentals.append({
            "symbol": symbol, "asof": asof, "pe": val.get("pe"), "forward_pe": None,
            "pb": val.get("pb"), "dividend_yield": val.get("dividend_yield"),
            "market_cap": None, "eps_ttm": None, "eps_forward": None,
            "revenue_yoy": rev.get("yoy"), "revenue_mom": rev.get("mom"),
            "gross_margin": None, "op_margin": None, "net_margin": None,
            "roe": None, "roa": None, "debt_to_equity": None,
            "current_ratio": None, "free_cashflow": None, "source": "twse",
        })
        if code in exright and exright[code].get("ex_date"):
            e = exright[code]
            tw_dividends.append({
                "symbol": symbol, "ex_date": e["ex_date"], "type": e.get("type"),
                "cash_dividend": e.get("cash_dividend"),
                "stock_dividend": e.get("stock_dividend"), "pay_date": None})
        if code in margin:
            m = margin[code]
            tw_margin.append({
                "symbol": symbol, "date": tw_today,
                "margin_balance": m.get("margin_balance"),
                "margin_balance_prev": m.get("margin_balance_prev"),
                "short_balance": m.get("short_balance"),
                "short_balance_prev": m.get("short_balance_prev"),
                "margin_util": None})
        if code in holders:
            h = holders[code]
            tw_holders.append({
                "symbol": symbol, "date": h["date"], "big_holder_pct": h["big_holder_pct"]})
        if code in institutional:
            i = institutional[code]
            tw_insti.append({
                # 用來源標註的實際資料日；T86 盤中查不到當日，會回退到前一交易日
                "symbol": symbol, "date": i.get("date") or tw_today,
                "foreign_net": i.get("foreign_net"), "trust_net": i.get("trust_net"),
                "dealer_net": i.get("dealer_net")})

    store.upsert(conn, "fundamentals", tw_fundamentals)
    store.upsert(conn, "dividends", tw_dividends)
    store.upsert(conn, "margin", tw_margin)
    store.upsert(conn, "holders", tw_holders)
    store.upsert(conn, "institutional", tw_insti)

    # --- 逐檔：日線（台美股共用）---
    log(f"抓取 {stats['universe']} 檔日線…")
    for pick in tw_picks + us_picks:
        symbol = pick["symbol"]
        try:
            rows = yahoo.fetch_chart(session, symbol, range_=f"{config.HISTORY_YEARS}y")
            store.upsert(conn, "prices", rows)
            # 歷史除息：TWSE 的除權息表只含未來 14 天，歷史配息要從 Yahoo 的
            # events 取得，否則 dividends 表對多數個股會是空的。
            events = yahoo.fetch_dividend_events(session, symbol)
            store.upsert(conn, "dividends", [
                {"symbol": e["symbol"], "ex_date": e["ex_date"], "type": "息",
                 "cash_dividend": e["cash_dividend"], "stock_dividend": None,
                 "pay_date": None}
                for e in events
            ])
        except Exception as exc:  # 單檔失敗不得中斷整體（spec 9.1）
            stats["failed"].append(symbol)
            log(f"  x {symbol} 日線失敗：{exc}")

    # --- 逐檔：美股基本面與分析師 ---
    if yahoo_client is not None and us_picks:
        log(f"抓取 {len(us_picks)} 檔美股基本面…")
        us_fundamentals, us_analyst = [], []
        us_today = market_date("US")
        for pick in us_picks:
            symbol = pick["symbol"]
            try:
                f = yahoo.fetch_us_fundamentals(yahoo_client, symbol)
                us_fundamentals.append({
                    "symbol": symbol, "asof": us_today, "pe": f["pe"],
                    "forward_pe": f["forward_pe"], "pb": f["pb"],
                    "dividend_yield": f["dividend_yield"], "market_cap": f["market_cap"],
                    "eps_ttm": None, "eps_forward": f["eps_forward"],
                    "revenue_yoy": f["revenue_yoy"], "revenue_mom": None,
                    "gross_margin": f["gross_margin"], "op_margin": f["op_margin"],
                    "net_margin": f["net_margin"], "roe": f["roe"], "roa": f["roa"],
                    "debt_to_equity": f["debt_to_equity"],
                    "current_ratio": f["current_ratio"],
                    "free_cashflow": f["free_cashflow"], "source": "yahoo"})
                a = yahoo.fetch_us_analyst(yahoo_client, symbol)
                us_analyst.append({
                    "symbol": symbol, "asof": us_today,
                    "target_mean": a["target_mean"], "target_high": a["target_high"],
                    "target_low": a["target_low"], "recommendation": a["recommendation"],
                    "n_analysts": a["n_analysts"]})
            except Exception as exc:
                stats["failed"].append(symbol)
                log(f"  x {symbol} 基本面失敗：{exc}")
        store.upsert(conn, "fundamentals", us_fundamentals)
        store.upsert(conn, "analyst", us_analyst)

    # --- 逐檔：新聞 ---
    log("抓取新聞…")
    for pick in tw_picks + us_picks:
        try:
            items = news.fetch_news(
                session, pick["symbol"], pick.get("name") or pick["code"], pick["market"])
            store.upsert(conn, "news", items)
        except Exception as exc:
            log(f"  x {pick['symbol']} 新聞失敗：{exc}")

    store.set_meta(conn, "last_fetch", utc_now_iso())
    return stats
