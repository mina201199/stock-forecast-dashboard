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

import html as html_mod
import json
import re
from pathlib import Path
from typing import NamedTuple

from src.numeric import is_finite

SIZE_BUDGET_BYTES = 50 * 1024
# HTML 內嵌樣式並為每一檔畫區間條，必然比 markdown 大。貼外部工具用的是
# markdown，這份是拿來看和列印的，所以上限放寬但仍然是上限。
HTML_SIZE_BUDGET_BYTES = 400 * 1024
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

    # 按 (訊號, 市場) 分組，不是逐檔展開。
    #
    # 基準率掛在訊號上，不是掛在個股上 —— 同一個訊號的每一檔，勝率、
    # 中位超額、有效樣本完全相同。實跑 500 檔選股池時日報出現 12 行
    # near_high_strength，數字一模一樣；181 筆觸發讓訊號區佔掉 21.4 KB
    # （約 121 bytes/筆），400 筆就會擦到 50 KB 上限。分組後統計值只印
    # 一次，標的列成一行。
    groups: dict[tuple[str, str], dict] = {}
    unrated: list[dict] = []
    for fire in payload.get("fires", []):
        slot = by_key.get((fire.signal_key, fire.market))
        rate = (slot or {}).get("headline")
        if rate is None:
            unrated.append({"symbol": fire.symbol, "signal": fire.signal_key})
            continue
        ident = (fire.signal_key, fire.market)
        group = groups.get(ident)
        if group is None:
            group = {
                "signal": fire.signal_key,
                "signal_name": _signal_name(fire.signal_key),
                "market": fire.market,
                "verdict": rate.verdict,
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
                "symbols": [],
            }
            groups[ident] = group
        group["symbols"].append({"symbol": fire.symbol, "price": fire.price})

    buckets: dict[str, list[dict]] = {k: [] for k in VERDICT_LABELS}
    for group in groups.values():
        buckets[group["verdict"]].append(group)

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


def _signal_name(key: str) -> str:
    """訊號的顯示名稱。不在 registry 裡（測試用的假訊號）就用 key。"""
    try:
        from src import signals
    except ImportError:                                       # pragma: no cover
        return key
    signal = signals.BY_KEY.get(key)
    return signal.name if signal else key


def _signal_lines(group: dict) -> list[str]:
    """一個訊號一段：統計值一行，觸發的標的一行。"""
    head = (
        f"- **{group['signal_name']}**（{group['signal']}／{group['market']}）"
        f"｜勝率 {_pct(group['hit_rate'])}"
        f" vs 基準 {_pct(group['baseline_hit_rate'])}"
        f"｜中位超額 {_pct(group['median_excess'], 2)}"
        f"｜有效樣本 {group['n_effective']}"
    )
    symbols = [entry["symbol"] for entry in group["symbols"]]
    return [head, f"    {'、'.join(symbols)}（{len(symbols)} 檔）"]


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
        for group in edges:
            lines.extend(_signal_lines(group))
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
        for group in rows:
            body = _signal_lines(group)
            if verdict == "unstable":
                body[0] += (
                    f"｜前半 {_pct(group['first_half_median'], 1)}"
                    f"／後半 {_pct(group['second_half_median'], 1)}"
                )
            lines.extend(body)
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



# --- 微型圖表（內嵌 SVG，無 JS、無 library）---------------------------
#
# 為什麼不用圖表庫：這份 HTML 必須自我包含 —— 可列印、可存檔、可寄給
# 別人，三年後打開還是能顯示。任何 CDN 依賴都會讓存檔版本壞掉。
#
# 配色的關鍵決定：本專案沿用台股慣例（紅漲綠跌），而 #d92b3a ↔ #10893e
# 的 deutan ΔE 實測只有 5.0，低於 6–8 的最低容許帶 —— 色盲讀者分不出來。
# 在單一頁面裡改成相反慣例會比色盲問題更誤導，所以顏色保留、但一律降為
# 冗余資訊：極性由位置（零線左右）與正負號各自獨立表達。

BAR_W = 132
BAR_H = 8
EXCESS_FULL_SCALE = 0.10   # 雙向長條的滿格值（±10%）


def _hit_bar(hit: float, baseline: float) -> str:
    """勝率長條 + 基準刻度。

    刻意**不用**極性色：這裡的問題是「贏不贏過基準」，答案靠刻度與
    長條末端的相對位置就讀得出來，不需要顏色參與。
    """
    if not is_finite(hit):
        return ""
    width = max(0.0, min(1.0, float(hit))) * BAR_W
    title = f"勝率 {hit:.1%}"
    if is_finite(baseline):
        title += f"，同市場未觸發基準 {baseline:.1%}"
    parts = [
        f'<svg class="hit-bar" width="{BAR_W}" height="{BAR_H}" '
        f'viewBox="0 0 {BAR_W} {BAR_H}" role="img">',
        f"<title>{html_mod.escape(title)}</title>",
        f'<rect class="track" x="0" y="0" width="{BAR_W}" height="{BAR_H}" '
        f'rx="{BAR_H / 2}"/>',
        f'<rect class="bar-fill" x="0" y="0" width="{width:.1f}" '
        f'height="{BAR_H}" rx="{BAR_H / 2}"/>',
    ]
    if is_finite(baseline):
        x = max(0.0, min(1.0, float(baseline))) * BAR_W
        parts.append(
            f'<rect class="baseline-tick" x="{x - 1:.1f}" y="-2" '
            f'width="2" height="{BAR_H + 4}"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _excess_bar(value: float) -> str:
    """前後半超額的雙向長條。

    `data-sign` 讓極性在 DOM 層就有結構性表達，不只存在於 fill 顏色裡。
    """
    if not is_finite(value):
        return ""
    half = BAR_W / 2
    frac = max(-1.0, min(1.0, float(value) / EXCESS_FULL_SCALE))
    width = max(abs(frac) * half, 1.5)
    sign = "pos" if value > 0 else ("neg" if value < 0 else "zero")
    x = half if frac >= 0 else half - width
    return (
        f'<svg class="excess-bar" width="{BAR_W}" height="{BAR_H}" '
        f'viewBox="0 0 {BAR_W} {BAR_H}" role="img">'
        f"<title>{value:+.2%}</title>"
        f'<rect class="track" x="0" y="0" width="{BAR_W}" height="{BAR_H}" '
        f'rx="{BAR_H / 2}"/>'
        f'<rect class="{sign}" data-sign="{sign}" x="{x:.1f}" y="0" '
        f'width="{width:.1f}" height="{BAR_H}" rx="4"/>'
        f'<rect class="zero-rule" x="{half - 0.5}" y="-2" width="1" '
        f'height="{BAR_H + 4}"/>'
        "</svg>"
    )


def _spread_bar(spread: float, max_spread: float) -> str:
    """波動區間的**寬度**量值條。

    刻意不畫「區間 + 現價標記」：區間是現價 × (1 ± spread)，現價恆在
    正中央 —— 標記畫出來每一列都在中點，等於沒有編碼任何資訊。
    真正有變化的是寬度，所以畫寬度。
    """
    if not is_finite(spread) or not is_finite(max_spread) or max_spread <= 0:
        return ""
    width = max(min(float(spread) / float(max_spread), 1.0), 0.0) * (BAR_W * 0.55)
    return (
        f'<svg class="spread-bar" width="{BAR_W * 0.55:.0f}" height="{BAR_H - 2}" '
        f'viewBox="0 0 {BAR_W * 0.55:.0f} {BAR_H - 2}" role="img">'
        f"<title>±{spread:.1%}</title>"
        f'<rect class="track" x="0" y="0" width="{BAR_W * 0.55:.0f}" '
        f'height="{BAR_H - 2}" rx="{(BAR_H - 2) / 2}"/>'
        f'<rect class="spread-fill" x="0" y="0" width="{width:.1f}" '
        f'height="{BAR_H - 2}" rx="{(BAR_H - 2) / 2}"/>'
        "</svg>"
    )


# --- 為 template 預先格式化 -------------------------------------------


def _prepare_groups(data: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for verdict, rows in data.get("signals", {}).items():
        prepared = []
        for g in rows:
            symbols = [e["symbol"] for e in g["symbols"]]
            prepared.append({
                **g,
                "hit_txt": _pct(g["hit_rate"]),
                "baseline_txt": _pct(g["baseline_hit_rate"]),
                "median_txt": _pct(g["median_excess"], 2),
                "p_txt": (f"{g['p_adjusted']:.3f}"
                          if is_finite(g.get("p_adjusted")) else ""),
                "first_txt": _signed(g.get("first_half_median")),
                "second_txt": _signed(g.get("second_half_median")),
                "hit_svg": _hit_bar(g["hit_rate"], g["baseline_hit_rate"]),
                "first_svg": _excess_bar(g.get("first_half_median")),
                "second_svg": _excess_bar(g.get("second_half_median")),
                "symbols_txt": "、".join(symbols),
                "n_symbols": len(symbols),
            })
        out[verdict] = prepared
    return out


def _signed(value) -> str:
    return f"{value:+.1%}" if is_finite(value) else "—"


def _prepare_bands(data: dict) -> list[dict]:
    bands = data.get("bands", [])
    spreads = []
    for b in bands:
        price, high = b.get("price"), b.get("high")
        spreads.append(
            (float(high) / float(price) - 1.0)
            if is_finite(price) and is_finite(high) and float(price) > 0
            else float("nan")
        )
    usable = [s for s in spreads if is_finite(s)]
    peak = max(usable) if usable else float("nan")

    rows = []
    for b, spread in zip(bands, spreads, strict=True):
        rows.append({
            "symbol": b["symbol"],
            "price": f"{b['price']:,.2f}",
            "low": f"{b['low']:,.2f}",
            "high": f"{b['high']:,.2f}",
            "spread": f"±{spread:.1%}" if is_finite(spread) else "—",
            "svg": _spread_bar(spread, peak),
        })
    return rows


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _prepare_plan(data: dict) -> list[dict]:
    """把資金算術的行轉成 HTML。

    這裡是兩個 renderer 唯一共用「已格式化文字」的地方，而那是個 wart ——
    `run.py._plan_lines` 產出的是 markdown。比較乾淨的形狀是讓它回傳
    結構化資料、兩邊各自格式化。目前用一個範圍明確的最小子集轉換
    （只有 `**粗體**` 與清單縮排），因為那些行是我們自己的程式產生的，
    不是任意 markdown。
    """
    plan = data.get("plan")
    if not plan:
        return []
    lines = plan if isinstance(plan, list) else [str(plan)]

    rows = []
    for raw in lines:
        text = str(raw)
        if not text.strip():
            continue
        kind = ""
        if text.startswith("    - "):
            kind, text = "sub", text[6:]
        elif text.startswith("- "):
            text = text[2:]
        elif text.startswith("> "):
            kind, text = "note", text[2:]
        elif text.startswith("合計"):
            kind = "total"
        if "超過你設定" in text:
            kind = "flag"
        escaped = html_mod.escape(text)
        rows.append({"kind": kind, "html": _BOLD.sub(r"<strong>\1</strong>", escaped)})
    return rows


_ACCURACY_FLAG = re.compile(r"\s*<-\s*(.+)$")


def _prepare_accuracy(data: dict) -> list[dict]:
    """把準度摘要的字串切成 label / value / flag。

    這是在解析我們自己 `accuracy.format_summary()` 的輸出，範圍明確但
    確實脆弱。解不開時整行當 label —— 退化的結果仍然可讀，不會掉資料。
    """
    rows = []
    for raw in data.get("accuracy_lines", []):
        text = str(raw).rstrip()
        if not text:
            continue
        flag = ""
        match = _ACCURACY_FLAG.search(text)
        if match:
            flag = match.group(1).strip()
            text = text[: match.start()].rstrip()
        if text.lstrip().startswith("[!]"):
            rows.append({"label": text.strip(), "value": "", "flag": flag})
            continue
        parts = re.split(r"\s{2,}", text.strip(), maxsplit=1)
        if len(parts) == 2:
            rows.append({"label": parts[0], "value": parts[1], "flag": flag})
        else:
            rows.append({"label": text.strip(), "value": "", "flag": flag})
    return rows


def render_html(data: dict) -> str:
    """單一自我包含的 HTML 頁面。與 `render_markdown` 吃同一個 build() 輸出，
    所以兩種格式在內容上不可能不一致 —— 它們是同一份資料的兩個視圖。"""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    from src import render

    template_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("digest.html.j2").render(
        data=data,
        labels=VERDICT_LABELS,
        groups=_prepare_groups(data),
        band_rows=_prepare_bands(data),
        plan_rows=_prepare_plan(data),
        accuracy_rows=_prepare_accuracy(data),
        disclaimer=render.DISCLAIMER,
        style=(template_dir / "digest.css").read_text(encoding="utf-8"),
    )


class DigestFiles(NamedTuple):
    markdown: Path
    json: Path
    html: Path


def write(data: dict, out_dir: Path | str) -> DigestFiles:
    """三種格式，同一份資料。

    markdown 是貼進外部工具的那一份（12 KB 量級），HTML 是拿來看和列印的，
    JSON 是給程式讀的。三者都由 `build()` 的同一個輸出產生。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = data.get("as_of") or "unknown"

    md_path = out / f"daily-{stamp}.md"
    json_path = out / f"daily-{stamp}.json"
    html_path = out / f"daily-{stamp}.html"

    md_path.write_text(render_markdown(data), encoding="utf-8")
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    html_path.write_text(render_html(data), encoding="utf-8")
    return DigestFiles(md_path, json_path, html_path)
