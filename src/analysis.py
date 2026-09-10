"""從 cache.db 到頁面資料的完整分析流程。

流程：讀資料 -> 算特徵 -> 訓練模型 -> 三軌合成 -> 榜單 -> 回傳頁面資料。
所有輸出皆為統計結果，不含任何建議性判斷（spec 1.4）。
"""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

import config
from src import compose, features, risk, store
from src.models import sentiment as sentiment_model
from src.models import technical as tech_model
from src.models import valuation as valuation_model
from src.numeric import is_finite

BENCHMARKS = {"TW": config.TW_BENCHMARK, "US": config.US_BENCHMARK}
CHART_DAYS = 250
CANDLE_DAYS = 60


def _load_panel(conn) -> pd.DataFrame:
    stocks = pd.read_sql_query(
        "SELECT symbol, market, code, name, name_en, sector, industry, currency,"
        " listed_date FROM stocks", conn
    )
    prices = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, adjclose, volume"
        " FROM prices ORDER BY symbol, date", conn
    )
    return stocks, prices


def _nan_to_none(records: dict[str, dict]) -> dict[str, dict]:
    """pandas 會把 SQLite 的 NULL 讀成 NaN。

    NaN 通得過 `is not None` 與 `if not x` 這類守衛，一路傳到夾取階段
    會被 min/max 變成上界 —— 實測曾讓「完全沒有資料」顯示為
    「信心度 100」。在讀取層就轉回 None，從源頭斷掉這條路徑。
    詳見 src/numeric.py。
    """
    return {
        key: {k: (v if is_finite(v) or isinstance(v, str) else None)
              for k, v in row.items()}
        for key, row in records.items()
    }


def _latest_by(conn, table: str, order_column: str) -> pd.DataFrame:
    return pd.read_sql_query(
        f"SELECT t.* FROM {table} t JOIN ("
        f"  SELECT symbol, MAX({order_column}) AS m FROM {table} GROUP BY symbol"
        f") x ON t.symbol = x.symbol AND t.{order_column} = x.m",
        conn,
    )


def _benchmark_frame(prices: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for market, symbol in BENCHMARKS.items():
        rows = prices[prices["symbol"] == symbol][["date", "adjclose"]].copy()
        if rows.empty:
            continue
        rows["market"] = market
        frames.append(rows)
    if not frames:
        return pd.DataFrame(columns=["market", "date", "adjclose"])
    return pd.concat(frames, ignore_index=True)


def build_feature_panel(stocks: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """每檔各自算指標後縱向合併。跨檔一起算會讓 rolling 窗口跨越個股邊界。"""
    market_by_symbol = dict(zip(stocks["symbol"], stocks["market"], strict=False))
    frames = []
    for symbol, group in prices.groupby("symbol", sort=False):
        if symbol not in market_by_symbol:
            continue
        enriched = features.add_indicators(group.reset_index(drop=True))
        enriched["symbol"] = symbol
        enriched["market"] = market_by_symbol[symbol]
        frames.append(enriched)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def train_and_predict(panel: pd.DataFrame, benchmark: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """訓練分位數模型並對每檔最新一筆做預測。

    回傳 (每檔最新預測, 驗證報告)。資料不足以做時序切分時回傳空預測
    並在報告中說明 —— 不得偷偷用全部資料訓練後回頭預測自己。
    """
    report: dict = {"trained": False, "reason": None, "ic": None,
                    "quintile_returns": [], "momentum_ic": None, "n_samples": 0}

    if panel.empty or benchmark.empty:
        report["reason"] = "缺少價格或基準資料"
        return pd.DataFrame(), report

    labelled = panel.copy()
    labelled["y"] = tech_model.build_labels(
        labelled[["symbol", "market", "date", "adjclose"]], benchmark,
        horizon=config.HORIZON_DAYS,
    )
    usable = labelled.dropna(subset=["y", *features.FEATURE_COLUMNS])
    report["n_samples"] = int(len(usable))

    if len(usable) < 500 or usable["date"].nunique() < 60:
        report["reason"] = (
            f"可用樣本 {len(usable)} 筆、{usable['date'].nunique()} 個交易日，"
            "不足以做時序切分驗證"
        )
        return pd.DataFrame(), report

    split = tech_model.purged_split(usable["date"], horizon=config.HORIZON_DAYS)
    if split.train.sum() < 200 or split.valid.sum() < 50 or split.test.sum() < 50:
        report["reason"] = "切分後任一分割樣本過少"
        return pd.DataFrame(), report

    models = tech_model.train_quantile_models(
        usable.loc[split.train, features.FEATURE_COLUMNS], usable.loc[split.train, "y"],
        usable.loc[split.valid, features.FEATURE_COLUMNS], usable.loc[split.valid, "y"],
    )

    # 在測試集上誠實評估
    test_x = usable.loc[split.test, features.FEATURE_COLUMNS]
    test_pred = tech_model.predict(models, test_x)["tech_q50"]
    test_y = usable.loc[split.test, "y"]
    report["ic"] = tech_model.information_coefficient(test_pred, test_y)
    report["quintile_returns"] = tech_model.quintile_returns(test_pred, test_y)
    # 基準：純 20 日動能。贏不過它就代表模型沒有附加價值
    report["momentum_ic"] = tech_model.information_coefficient(
        usable.loc[split.test, "ret_20d"], test_y
    )
    report["trained"] = True

    # 對每檔最新一筆做預測（不需要標籤）
    latest = (
        panel.dropna(subset=features.FEATURE_COLUMNS)
        .sort_values("date")
        .groupby("symbol", as_index=False)
        .tail(1)
        .set_index("symbol")
    )
    if latest.empty:
        return pd.DataFrame(), report
    predictions = tech_model.predict(models, latest[features.FEATURE_COLUMNS])
    predictions.index = latest.index
    return predictions, report


def _model_accuracy(report: dict) -> float | None:
    """把測試集 IC 轉成 0-100 的分數。IC 0.05 已算不錯，故以 0.15 為滿分刻度。"""
    ic = report.get("ic")
    if ic is None:
        return None
    return float(max(0.0, min(100.0, (ic / 0.15) * 100.0)))


def analyse(conn, now: datetime | None = None, *, edge_fires=()) -> dict:
    now = now or datetime.now(UTC)
    stocks, prices = _load_panel(conn)
    if stocks.empty or prices.empty:
        return {"stocks": [], "report": {"trained": False, "reason": "資料庫為空"},
                "generated_at": now.isoformat(timespec="seconds"), "boards": {}}

    fundamentals = _latest_by(conn, "fundamentals", "asof")
    analysts = _latest_by(conn, "analyst", "asof")
    institutional = _latest_by(conn, "institutional", "date")
    margin = _latest_by(conn, "margin", "date")
    holders = _latest_by(conn, "holders", "date")
    dividends = pd.read_sql_query(
        "SELECT symbol, ex_date, type, cash_dividend FROM dividends ORDER BY ex_date", conn
    )
    news = pd.read_sql_query(
        "SELECT symbol, published_at, title, link, source FROM news", conn
    )

    panel = build_feature_panel(stocks, prices)
    benchmark = _benchmark_frame(prices)
    predictions, report = train_and_predict(panel, benchmark)
    accuracy = _model_accuracy(report)

    fund_by = _nan_to_none(fundamentals.set_index("symbol").to_dict("index"))
    analyst_by = _nan_to_none(analysts.set_index("symbol").to_dict("index"))
    insti_by = _nan_to_none(institutional.set_index("symbol").to_dict("index"))
    margin_by = _nan_to_none(margin.set_index("symbol").to_dict("index"))
    holders_by = _nan_to_none(holders.set_index("symbol").to_dict("index"))

    # 同業比較用的橫斷面表
    peer_frame = stocks[["symbol", "industry", "market"]].copy()
    peer_frame["pe"] = peer_frame["symbol"].map(
        lambda s: (fund_by.get(s) or {}).get("pe"))
    peer_frame["pb"] = peer_frame["symbol"].map(
        lambda s: (fund_by.get(s) or {}).get("pb"))
    peer_frame["dividend_yield"] = peer_frame["symbol"].map(
        lambda s: (fund_by.get(s) or {}).get("dividend_yield"))

    bench_series = {
        market: prices[prices["symbol"] == symbol].set_index("date")["adjclose"]
        for market, symbol in BENCHMARKS.items()
    }

    latest_rows = (
        panel.sort_values("date").groupby("symbol", as_index=False).tail(1)
        .set_index("symbol")
    )

    results = []
    volatilities: list[float] = []
    for _, meta in stocks.iterrows():
        symbol = meta["symbol"]
        if symbol in BENCHMARKS.values():
            continue
        series = prices[prices["symbol"] == symbol].reset_index(drop=True)
        if series.empty or symbol not in latest_rows.index:
            continue
        row = latest_rows.loc[symbol]
        price = float(series["close"].iloc[-1])
        bars = len(series)

        fund = fund_by.get(symbol) or {}
        risk_metrics = risk.compute(
            series.set_index("date")["adjclose"],
            bench_series.get(meta["market"]),
            float(row.get("turnover_ma20")) if pd.notna(row.get("turnover_ma20")) else None,
        )
        volatilities.append(risk_metrics["volatility_252d"])

        peer_pes, scope = valuation_model.industry_peer_values(
            peer_frame, meta["market"], meta.get("industry"), "pe")
        peer_pbs, _ = valuation_model.industry_peer_values(
            peer_frame, meta["market"], meta.get("industry"), "pb")
        peer_yields, _ = valuation_model.industry_peer_values(
            peer_frame, meta["market"], meta.get("industry"), "dividend_yield")
        val = valuation_model.estimate(
            price=price, pe=fund.get("pe"), pb=fund.get("pb"),
            dividend_yield=fund.get("dividend_yield"), industry=meta.get("industry"),
            peer_pes=peer_pes, peer_pbs=peer_pbs, peer_yields=peer_yields,
            peer_scope=scope,
        )

        stock_news = news[news["symbol"] == symbol].to_dict("records")
        mood = sentiment_model.analyse_batch(stock_news, now)

        tech = predictions.loc[symbol].to_dict() if symbol in predictions.index else None
        feature_values = {c: row.get(c) for c in features.FEATURE_COLUMNS}

        blended = compose.blend(
            price=price, tech=tech, valuation=val,
            analyst=analyst_by.get(symbol), sentiment=mood["sentiment"],
            market=meta["market"], bars=bars,
            has_forward_eps=bool(fund.get("eps_forward")),
        )

        if blended:
            conf = compose.confidence(
                tracks=blended["tracks"], features=feature_values, tech=tech,
                volatility=risk_metrics["volatility_252d"], model_accuracy=accuracy,
                peer_scope=(val or {}).get("peer_scope"),
            )
            blended["confidence"] = conf["score"]
            blended["confidence_factors"] = conf["factors"]
            blended["confidence_weights"] = conf["weights"]
            blended = compose.sanity_check(
                blended, price=price, bars=bars,
                annual_vol=risk_metrics["volatility_60d"],
            )
        else:
            blended = {"predictable": False, "confidence": 0.0,
                       "alerts": ["缺少足夠資料，無法產生目標價"],
                       "confidence_factors": {}, "tracks": {}}

        prev_close = float(series["close"].iloc[-2]) if bars >= 2 else price
        upcoming = dividends[
            (dividends["symbol"] == symbol) & (dividends["ex_date"] >= now.strftime("%Y-%m-%d"))
        ]

        results.append({
            "symbol": symbol, "code": meta["code"], "market": meta["market"],
            "name": meta.get("name") or meta["code"],
            "name_en": meta.get("name_en"),
            "industry": meta.get("industry") or meta.get("sector"),
            "currency": meta.get("currency"),
            "listed_date": meta.get("listed_date"),
            "price": price,
            "change_pct": (price / prev_close - 1) if prev_close else 0.0,
            "bars": bars,
            "as_of": series["date"].iloc[-1],
            "trend": compose.classify_trend(feature_values),
            "trend_label": compose.TREND_LABELS[compose.classify_trend(feature_values)],
            "fundamentals": {
                "pe": fund.get("pe"), "forward_pe": fund.get("forward_pe"),
                "pb": fund.get("pb"), "dividend_yield": fund.get("dividend_yield"),
                "revenue_yoy": fund.get("revenue_yoy"), "roe": fund.get("roe"),
                "net_margin": fund.get("net_margin"), "gross_margin": fund.get("gross_margin"),
                "market_cap": fund.get("market_cap"), "eps_forward": fund.get("eps_forward"),
            },
            "valuation": val,
            "analyst": analyst_by.get(symbol),
            "chips": {
                "foreign_net": (insti_by.get(symbol) or {}).get("foreign_net"),
                "trust_net": (insti_by.get(symbol) or {}).get("trust_net"),
                "dealer_net": (insti_by.get(symbol) or {}).get("dealer_net"),
                "chip_date": (insti_by.get(symbol) or {}).get("date"),
                "margin_balance": (margin_by.get(symbol) or {}).get("margin_balance"),
                "margin_change": _delta(margin_by.get(symbol), "margin_balance",
                                        "margin_balance_prev"),
                "short_balance": (margin_by.get(symbol) or {}).get("short_balance"),
                "big_holder_pct": (holders_by.get(symbol) or {}).get("big_holder_pct"),
                "short_percent_float": fund.get("short_percent_float"),
            },
            "risk": risk_metrics,
            "technical": {c: _clean(row.get(c)) for c in features.FEATURE_COLUMNS},
            "news": mood,
            "prediction": blended,
            "dividends": upcoming.to_dict("records"),
            "chart": _chart_payload(series),
        })

    _attach_risk_levels(results, volatilities)
    return {
        "stocks": results,
        "report": report,
        "boards": build_boards(results, now, edge_fires=edge_fires),
        "generated_at": now.isoformat(timespec="seconds"),
        "horizon_days": config.HORIZON_DAYS,
    }


def _delta(record: dict | None, current: str, previous: str) -> float | None:
    if not record:
        return None
    now_value, prev_value = record.get(current), record.get(previous)
    if now_value is None or prev_value is None:
        return None
    return float(now_value - prev_value)


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float | np.floating):
        return None if (np.isnan(value) or np.isinf(value)) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    return value


def _chart_payload(series: pd.DataFrame) -> dict:
    tail = series.tail(CHART_DAYS)
    candles = series.tail(CANDLE_DAYS)
    return {
        "dates": tail["date"].tolist(),
        "adjclose": [round(float(v), 2) for v in tail["adjclose"]],
        "volume": [int(v) if pd.notna(v) else 0 for v in tail["volume"]],
        "candles": [
            {"d": r.date, "o": round(float(r.open), 2), "h": round(float(r.high), 2),
             "l": round(float(r.low), 2), "c": round(float(r.close), 2)}
            for r in candles.itertuples()
        ],
    }


def _attach_risk_levels(results: list[dict], volatilities: list[float]) -> None:
    turnovers = [r["risk"]["turnover_ma20"] for r in results]
    for item in results:
        vol = item["risk"]["volatility_252d"]
        item["risk"]["volatility_level"] = risk.quintile_level(vol, volatilities)
        item["risk"]["volatility_percentile"] = risk.percentile_rank(vol, volatilities)
        item["risk"]["liquidity_level"] = risk.quintile_level(
            item["risk"]["turnover_ma20"], turnovers
        )


def build_boards(results: list[dict], now: datetime, *, edge_fires=()) -> dict:
    """四個榜單（spec 7.4）。純粹是依客觀條件過濾與排序，不是推薦。

    **看多榜改由有統計優勢的訊號驅動**（2026-09-10，spec §4.2）。

    原本以 `prediction.upside_pct` 排序。2026-08-31 留存的 88 筆預測在 9/10
    回頭比對，證明那個欄位不是報酬預測器：預測漲幅前 12 名的信心度全部
    都是 40（三軌分歧上限與顯示門檻相等所致），信心度 > 50 的沒有一筆漲幅
    超過 +0.6%。以它排序的榜單由結構保證只從最不可信的那一格挑股票 ——
    而那 88 筆的方向命中率正好是 48.9%。

    現在只有「今天觸發了 verdict == edge 訊號」的標的能進榜。順序沿用
    `edge_fires` 的順序，不再由任何預測值重排。
    """
    predictable = [r for r in results if r["prediction"].get("predictable")]

    fired_order: list[str] = []
    for fire in edge_fires:
        if fire.symbol not in fired_order:
            fired_order.append(fire.symbol)
    by_symbol = {r["symbol"]: r for r in results}
    bullish = [by_symbol[s] for s in fired_order if s in by_symbol][:30]

    # 榜單空的時候要說得出原因，否則使用者無從判斷是市場如此還是程式壞了。
    # 這裡的原因通常不是「今天沒人漲」，而是「沒有任何訊號有統計優勢」——
    # 兩者完全不同，說錯會讓人以為再等幾天就會有。
    bullish_note = None
    if not bullish:
        bullish_note = (
            "沒有任何訊號通過統計優勢的門檻，因此看多榜為空。"
            "這不是「今天沒有標的觸發訊號」，而是十個技術面訊號在三年歷史上"
            "都沒有可證實的預測力（見 python run.py --baserates）。"
            f"（{len(results)} 檔中 {len(results) - len(predictable)} 檔"
            "另因資料或訊號品質不予預測）"
        )

    # 排除偏離過大的估值：同業相對法在樣本結構特殊時會產生極端值，
    # 那多半反映樣本問題而非真實的低估空間（spec 7.5 同一門檻）。
    undervalued = sorted(
        [r for r in results
         if r["valuation"] and r["price"] < (r["valuation"].get("low") or 0)
         and (r["fundamentals"].get("pe") or 0) > 0
         and (r["fundamentals"].get("revenue_yoy") or 0) > 0
         and abs(r["valuation"]["upside_pct"]) <= compose.DEVIATION_ALERT],
        key=lambda r: r["valuation"]["upside_pct"], reverse=True,
    )[:30]

    chips = sorted(
        [r for r in results if _chip_signal(r) is not None],
        key=lambda r: _chip_signal(r), reverse=True,
    )[:30]

    today = now.strftime("%Y-%m-%d")
    horizon = (now + pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    events = sorted(
        [
            {**r, "event_date": d["ex_date"], "event_type": f"除{d.get('type') or '息'}",
             "event_detail": d.get("cash_dividend")}
            for r in results for d in r["dividends"]
            if today <= d["ex_date"] <= horizon
        ],
        key=lambda r: r["event_date"],
    )[:30]

    return {
        "bullish": [_board_row(r) for r in bullish],
        "bullish_note": bullish_note,
        "undervalued": [_board_row(r) for r in undervalued],
        "chips": [_board_row(r) for r in chips],
        "events": [{**_board_row(r), "event_date": r["event_date"],
                    "event_type": r["event_type"], "event_detail": r["event_detail"]}
                   for r in events],
    }


def _chip_signal(item: dict) -> float | None:
    """籌碼異動綜合訊號。台股看外資+投信買超與融資減少，美股看放空比與法人持股。"""
    chips = item["chips"]
    if item["market"] == "TW":
        foreign, trust = chips.get("foreign_net"), chips.get("trust_net")
        if foreign is None and trust is None:
            return None
        score = ((foreign or 0) + (trust or 0)) / 1e6
        change = chips.get("margin_change")
        if change is not None and change < 0:
            score += 1.0  # 融資減少代表籌碼由散戶轉出
        return float(score)
    short_pct = chips.get("short_percent_float")
    if short_pct is None:
        return None
    return float(short_pct * 100)


def _board_row(item: dict) -> dict:
    return {
        "symbol": item["symbol"], "name": item["name"], "market": item["market"],
        "industry": item["industry"], "price": item["price"],
        "change_pct": item["change_pct"], "trend": item["trend"],
        "trend_label": item["trend_label"],
        "target_mid": item["prediction"].get("target_mid"),
        "upside_pct": item["prediction"].get("upside_pct"),
        "confidence": item["prediction"].get("confidence"),
        "pe": item["fundamentals"].get("pe"),
        "dividend_yield": item["fundamentals"].get("dividend_yield"),
        "revenue_yoy": item["fundamentals"].get("revenue_yoy"),
        "foreign_net": item["chips"].get("foreign_net"),
    }


def read_conn():
    return store.connect(config.DB_PATH)
