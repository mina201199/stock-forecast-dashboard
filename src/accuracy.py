"""預測留存與事後準度驗證。

**為什麼需要這個模組**

`predictions` 表在 schema 裡從第一天就存在，註解也寫著「供事後檢驗模型
實際準確度」，但實際上沒有任何程式寫入它。結果是：想回答「昨天的預測
準不準」時，只能從舊的 index.html 反解內嵌 JSON 硬湊 —— 檔案一被覆蓋
就永遠比不了。

留存預測是這個專案唯一能誠實回答「它到底準不準」的方式。回測（spec 6.1）
用的是歷史資料，會受到選樣與參數調整的影響；留存下來的預測是**事前**
寫死的，事後無從修改。

**比較的是超額報酬**，因為模型預測的就是超額報酬：
    實際超額 = 個股報酬 − 同市場基準同期報酬
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config
from src import store
from src.numeric import is_finite

BENCHMARKS = {"TW": config.TW_BENCHMARK, "US": config.US_BENCHMARK}


def snapshot_rows(payload: dict) -> list[dict]:
    """把一次分析的輸出轉成 predictions 表的列。

    只留存有預測值的個股。不可預測的個股沒有可驗證的主張，
    留下來只會在事後統計裡製造分母噪音。
    """
    rows = []
    for stock in payload.get("stocks", []):
        pred = stock.get("prediction") or {}
        if not pred.get("predictable"):
            continue
        tracks = pred.get("tracks") or {}
        rows.append({
            "symbol": stock["symbol"],
            # 用資料基準日而非執行日：同一天重跑兩次不應產生兩筆，
            # 而盤後跑與隔天早上跑的預測其實基於同一份收盤資料。
            "run_date": stock["as_of"],
            "close": stock["price"],
            "target_low": pred.get("target_low"),
            "target_mid": pred.get("target_mid"),
            "target_high": pred.get("target_high"),
            "upside_pct": pred.get("upside_pct"),
            "confidence": pred.get("confidence"),
            "trend": stock.get("trend"),
            "tech_q10": (stock.get("technical") or {}).get("tech_q10"),
            "tech_q50": tracks.get("tech", {}).get("fair"),
            "tech_q90": (stock.get("technical") or {}).get("tech_q90"),
            "valuation_fair": (stock.get("valuation") or {}).get("fair"),
            "sentiment": (stock.get("news") or {}).get("sentiment"),
        })
    return rows


def save_snapshot(conn, payload: dict) -> int:
    return store.upsert(conn, "predictions", snapshot_rows(payload))


@dataclass(frozen=True)
class Scored:
    frame: pd.DataFrame
    horizon_days: int


def _series(conn, symbol: str) -> pd.Series:
    frame = store.read_prices(conn, symbol)
    if frame.empty:
        return pd.Series(dtype=float)
    return frame.set_index("date")["adjclose"]


def score(conn, horizon_days: int = config.HORIZON_DAYS) -> Scored:
    """把每一筆留存的預測與其後的實際走勢對上。

    對每筆預測取「基準日之後最新可得的收盤」，同時記錄已經過了幾個
    交易日 —— 沒走完 horizon 的預測仍列出，但必須標明尚未到期，
    否則會拿半途的結果當成模型的最終表現。
    """
    stored = pd.read_sql_query("SELECT * FROM predictions", conn)
    if stored.empty:
        return Scored(pd.DataFrame(), horizon_days)

    markets = pd.read_sql_query("SELECT symbol, market, name FROM stocks", conn)
    stored = stored.merge(markets, on="symbol", how="left")

    price_cache: dict[str, pd.Series] = {}
    bench_cache = {m: _series(conn, sym) for m, sym in BENCHMARKS.items()}

    rows = []
    for record in stored.to_dict("records"):
        symbol, base = record["symbol"], record["run_date"]
        market = record.get("market")
        if market not in BENCHMARKS:
            continue
        if symbol not in price_cache:
            price_cache[symbol] = _series(conn, symbol)
        prices, bench = price_cache[symbol], bench_cache[market]
        if base not in prices.index or base not in bench.index:
            continue

        later = [d for d in prices.index if d > base and d in bench.index]
        if not later:
            continue
        end = later[-1]
        ret = float(prices[end]) / float(prices[base]) - 1
        bench_ret = float(bench[end]) / float(bench[base]) - 1
        rows.append({
            **record,
            "end_date": end,
            "elapsed_days": len(later),
            "matured": len(later) >= horizon_days,
            "actual_return": ret,
            "benchmark_return": bench_ret,
            "actual_excess": ret - bench_ret,
        })

    return Scored(pd.DataFrame(rows), horizon_days)


def summarise(scored: Scored, matured_only: bool = False) -> dict:
    """準度摘要。

    命中率與 IC 一律附上 p 值。單看命中率 55% 會誤以為模型有效，
    但在 30 檔的樣本下那與擲硬幣無法區分。
    """
    frame = scored.frame
    if frame.empty:
        return {"n": 0, "reason": "沒有可比對的預測紀錄"}
    if matured_only:
        frame = frame[frame["matured"]]
    frame = frame[frame["upside_pct"].map(is_finite)
                  & frame["actual_excess"].map(is_finite)]
    if len(frame) < 5:
        return {"n": len(frame), "reason": f"可比對樣本僅 {len(frame)} 筆，不足以統計"}

    from scipy import stats

    hits = (frame["upside_pct"] > 0) == (frame["actual_excess"] > 0)
    binom = stats.binomtest(int(hits.sum()), len(frame), 0.5)

    # 任一邊為常數時秩相關無定義。讓它靜靜回 NaN 會在報表上顯示成
    # 「IC nan」，看起來像計算失敗；明確回 None 才說得清楚是無定義。
    if frame["upside_pct"].nunique() < 2 or frame["actual_excess"].nunique() < 2:
        ic, ic_p = None, None
    else:
        ic, ic_p = stats.spearmanr(frame["upside_pct"], frame["actual_excess"])

    result = {
        "n": int(len(frame)),
        "matured_n": int(frame["matured"].sum()),
        "elapsed_min": int(frame["elapsed_days"].min()),
        "elapsed_max": int(frame["elapsed_days"].max()),
        "horizon_days": scored.horizon_days,
        "hit_rate": float(hits.mean()),
        "hit_pvalue": float(binom.pvalue),
        "ic": float(ic) if is_finite(ic) else None,
        "ic_pvalue": float(ic_p) if is_finite(ic_p) else None,
        "ic_undefined": ic is None,
        "median_excess": float(frame["actual_excess"].median()),
    }

    up, down = frame[frame["upside_pct"] > 0], frame[frame["upside_pct"] <= 0]
    if len(up) >= 3 and len(down) >= 3:
        tstat, tp = stats.ttest_ind(up["actual_excess"], down["actual_excess"],
                                    equal_var=False)
        result["long_excess"] = float(up["actual_excess"].mean())
        result["short_excess"] = float(down["actual_excess"].mean())
        result["spread"] = result["long_excess"] - result["short_excess"]
        result["spread_pvalue"] = float(tp) if is_finite(tp) else None
    return result


def format_summary(summary: dict) -> list[str]:
    if summary.get("n", 0) == 0 or "reason" in summary:
        return [f"  {summary.get('reason', '沒有資料')}"]

    def verdict(p):
        if p is None:
            return ""
        return "  <- 與隨機無法區分" if p > 0.05 else "  <- 統計顯著"

    lines = [
        f"  可比對筆數        {summary['n']}"
        f"（其中 {summary['matured_n']} 筆已走滿 {summary['horizon_days']} 個交易日）",
        f"  已經過交易日      {summary['elapsed_min']} ~ {summary['elapsed_max']} 日",
        f"  實際超額 中位數    {summary['median_excess']:+.2%}",
        f"  方向命中率        {summary['hit_rate']:.1%}"
        f"  (p={summary['hit_pvalue']:.3f}){verdict(summary['hit_pvalue'])}",
    ]
    if summary.get("ic") is not None:
        lines.append(f"  IC 秩相關         {summary['ic']:+.4f}"
                     f"  (p={summary['ic_pvalue']:.3f}){verdict(summary['ic_pvalue'])}")
    elif summary.get("ic_undefined"):
        lines.append("  IC 秩相關         無定義（預測值或實際值缺乏變異）")
    if "spread" in summary:
        lines.append(f"  預測看多組 超額    {summary['long_excess']:+.2%}")
        lines.append(f"  預測看空組 超額    {summary['short_excess']:+.2%}")
        lines.append(f"  多空價差          {summary['spread']:+.2%}"
                     f"  (p={summary['spread_pvalue']:.3f})"
                     f"{verdict(summary['spread_pvalue'])}")
    if summary["matured_n"] < summary["n"]:
        lines.append(
            f"  [!] {summary['n'] - summary['matured_n']} 筆尚未走滿預測期間，"
            "此結果為中途快照，不代表模型的最終表現"
        )
    return lines


def by_market(scored: Scored) -> dict[str, dict]:
    if scored.frame.empty:
        return {}
    return {
        market: summarise(Scored(group, scored.horizon_days))
        for market, group in scored.frame.groupby("market")
    }


def worst_misses(scored: Scored, limit: int = 6) -> pd.DataFrame:
    if scored.frame.empty:
        return scored.frame
    frame = scored.frame.copy()
    frame["error"] = (frame["upside_pct"] - frame["actual_excess"]).abs()
    return frame.sort_values("error", ascending=False).head(limit)
