"""每日日報（spec §7）。

**為什麼不用 index.html**

現有的 `index.html` 是 1.7 MB，貼不進外部工具。日報是另一種形態：
精簡、結構化、同時出 Markdown 與 JSON。

**內容順序是刻意的，測試把它當契約**

準度計分卡放最上面，因為它決定讀者該多信下面的東西。這個專案的第一課
就是：一個看起來權威的數字，如果沒人知道它的 track record，就會被當成
知識使用。所以 track record 排在數字前面。

**空區塊必須明說**

沒有任何 edge 訊號時，不能只留空白 —— 空白會被讀成「今天沒觸發」，
而實際意思是「沒有任何訊號有統計優勢」。完全不同的兩件事。
"""
from __future__ import annotations

import json
from pathlib import Path

from src.numeric import is_finite

SIZE_BUDGET_BYTES = 50 * 1024
STALE_AFTER_DAYS = 3

VERDICT_LABELS = {
    "edge": "有統計優勢",
    "no_edge": "無統計優勢",
    "unstable": "跨期不穩定",
    "insufficient": "樣本不足",
}

HORIZON_FOR_HEADLINE = 20


def _days_between(later: str, earlier: str) -> int | None:
    try:
        from datetime import date
        a = date.fromisoformat(later)
        b = date.fromisoformat(earlier)
    except (TypeError, ValueError):
        return None
    return (a - b).days


def build(payload: dict) -> dict:
    """把分析輸出整理成日報的資料結構（可直接序列化成 JSON）。"""
    rates = {
        (r.signal_key, r.market, r.horizon): r for r in payload.get("rates", [])
    }
    # 以 20 日為代表期間掛在訊號上；其他期間放進 all_horizons
    by_key: dict[tuple[str, str], dict] = {}
    for (key, market, horizon), rate in rates.items():
        slot = by_key.setdefault((key, market), {"headline": None, "horizons": {}})
        slot["horizons"][horizon] = rate
        if horizon == HORIZON_FOR_HEADLINE:
            slot["headline"] = rate

    buckets: dict[str, list[dict]] = {k: [] for k in VERDICT_LABELS}
    unrated: list[dict] = []
    for fire in payload.get("fires", []):
        slot = by_key.get((fire.signal_key, fire.market))
        rate = (slot or {}).get("headline")
        if rate is None:
            unrated.append({"symbol": fire.symbol, "signal": fire.signal_key})
            continue
        buckets[rate.verdict].append({
            "symbol": fire.symbol,
            "market": fire.market,
            "signal": fire.signal_key,
            "price": fire.price,
            "hit_rate": rate.hit_rate,
            "baseline_hit_rate": rate.baseline_hit_rate,
            "median_excess": rate.median_excess,
            "n_effective": rate.n_effective,
            "first_half_median": rate.first_half_median,
            "second_half_median": rate.second_half_median,
            "p_adjusted": rate.p_adjusted,
            "horizons": {
                h: {"hit_rate": r.hit_rate, "median_excess": r.median_excess,
                    "verdict": r.verdict}
                for h, r in sorted((slot or {}).get("horizons", {}).items())
            },
        })

    last_success = payload.get("last_success")
    lag = _days_between(payload.get("as_of", ""), last_success or "")

    return {
        "as_of": payload.get("as_of"),
        "last_success": last_success,
        "fetch_lag_days": lag,
        "stale": bool(lag is not None and lag > STALE_AFTER_DAYS),
        "accuracy_lines": list(payload.get("accuracy_lines", [])),
        "signals": buckets,
        "unrated": unrated,
        "bands": list(payload.get("bands", [])),
        "plan": payload.get("plan"),
        "degraded": list(payload.get("degraded", [])),
    }


def _pct(value, digits=1) -> str:
    return f"{value:.{digits}%}" if is_finite(value) else "—"


def _signal_line(row: dict) -> str:
    return (
        f"- **{row['symbol']}**（{row['signal']}）"
        f"｜勝率 {_pct(row['hit_rate'])} vs 基準 {_pct(row['baseline_hit_rate'])}"
        f"｜中位超額 {_pct(row['median_excess'], 2)}"
        f"｜有效樣本 {row['n_effective']}"
    )


def render_markdown(data: dict) -> str:
    lines: list[str] = [f"# 每日訊號日報　{data.get('as_of')}", ""]

    # --- 1. 準度計分卡（必須第一）---
    lines.append("## 1. 準度計分卡")
    lines.append("")
    lines.append(f"- 上次成功抓取：{data.get('last_success') or '未知'}")
    if data.get("stale"):
        lines.append(
            f"- **[警告] 資料已過期：距今 {data.get('fetch_lag_days')} 天未成功抓取。**"
            f"排程可能已靜默失敗，下面所有數字都可能是舊的。"
        )
    for line in data.get("accuracy_lines", []):
        lines.append(f"- {line}")
    lines.append("")
    lines.append(
        "> 這一節放最上面，是因為它決定你該多信下面的東西。"
    )
    lines.append("")

    # --- 2. 訊號 ---
    lines.append("## 2. 今日觸發的訊號")
    lines.append("")
    buckets = data.get("signals", {})

    edges = buckets.get("edge", [])
    lines.append("### 有統計優勢")
    lines.append("")
    if edges:
        lines.extend(_signal_line(r) for r in edges)
    else:
        lines.append(
            "**沒有任何訊號通過統計優勢的門檻。** 這不是「今天沒觸發」，"
            "而是十個訊號在三年歷史上都沒有可證實的預測力 —— "
            "所以今天沒有任何標的能被稱為買點。"
        )
    lines.append("")

    for verdict in ("unstable", "no_edge", "insufficient"):
        rows = buckets.get(verdict, [])
        if not rows:
            continue
        lines.append(f"### {VERDICT_LABELS[verdict]}（僅供參考，不是買點）")
        lines.append("")
        if verdict == "unstable":
            lines.append(
                "全期看起來顯著，但分前後半期後效果反向 —— "
                "多半是把一段特定行情冒充成預測力。"
            )
            lines.append("")
        for row in rows:
            suffix = ""
            if verdict == "unstable":
                suffix = (
                    f"｜前半 {_pct(row['first_half_median'], 1)}"
                    f"／後半 {_pct(row['second_half_median'], 1)}"
                )
            lines.append(_signal_line(row) + suffix)
        lines.append("")

    if data.get("unrated"):
        lines.append("### 沒有基準率的訊號（不得作為買點）")
        lines.append("")
        for row in data["unrated"]:
            lines.append(f"- {row['symbol']}（{row['signal']}）：尚未量測歷史勝率")
        lines.append("")

    # --- 3. 波動區間 ---
    lines.append("## 3. 波動區間")
    lines.append("")
    if data.get("bands"):
        lines.append("| 標的 | 現價 | 區間下緣 | 區間上緣 |")
        lines.append("|---|---|---|---|")
        for band in data["bands"]:
            lines.append(
                f"| {band['symbol']} | {band['price']:,.2f} |"
                f" {band['low']:,.2f} | {band['high']:,.2f} |"
            )
        lines.append("")
    else:
        lines.append("（無）")
        lines.append("")
    lines.append(
        "> 這是以歷史波動度推出的描述性區間，**不是方向預測**。"
        "中位數不宣稱漲跌。常態假設下約對應 10/90 百分位；"
        "股價報酬有厚尾，實際覆蓋率會低於 80%。"
    )
    lines.append("")

    # --- 4. 資金算術 ---
    lines.append("## 4. 資金算術")
    lines.append("")
    plan = data.get("plan")
    if not plan:
        lines.append("（未提供 portfolio.toml，略）")
    else:
        lines.extend(plan if isinstance(plan, list) else [str(plan)])
    lines.append("")

    # --- 5. 資料缺口 ---
    if data.get("degraded"):
        lines.append("## 5. 資料缺口與降級來源")
        lines.append("")
        for item in data["degraded"]:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def write(data: dict, out_dir: Path | str) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = data.get("as_of") or "unknown"

    md_path = out / f"daily-{stamp}.md"
    json_path = out / f"daily-{stamp}.json"
    md_path.write_text(render_markdown(data), encoding="utf-8")
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return md_path, json_path
