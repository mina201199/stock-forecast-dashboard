"""進入點（spec 12）。

第一階段只實作資料抓取；--no-fetch / --serve / --backtest 需等後續階段。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import config
from src import (
    accuracy,
    analysis,
    baserate,
    digest,
    render,
    risk,
    signals,
    store,
)
from src.http import RateLimiter, YahooClient, make_session
from src.pipeline import fetch_all

_NOT_YET = "此模式屬後續階段，尚未實作：{name}"


def _make_console_forgiving() -> None:
    """避免因為一個裝飾字元而崩潰。

    Windows 主控台預設是 cp950，遇到 U+26A0（警告符號）之類的字元會
    直接 UnicodeEncodeError —— 實測 `--accuracy` 就是這樣掛掉的。
    改成 errors="replace"：cp950 本來就能表示繁體中文，只有少數符號
    退化成 ?，訊息內容仍然完整。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="台股／美股預測分析平台")
    parser.add_argument("--full", action="store_true", help="強制全量重抓")
    parser.add_argument("--no-fetch", action="store_true", help="只重算模型與頁面")
    parser.add_argument("--fetch-only", action="store_true", help="只抓資料，不算模型")
    parser.add_argument("--only", metavar="SYMBOL", help="單檔除錯")
    parser.add_argument("--serve", action="store_true", help="啟動本地即時模式")
    parser.add_argument("--backtest", action="store_true", help="產出回測驗證報告")
    parser.add_argument("--accuracy", action="store_true",
                        help="比對已留存的預測與實際走勢")
    parser.add_argument("--baserates", action="store_true",
                        help="重算全部訊號的歷史基準率（走全歷史，慢）")
    parser.add_argument("--digest", action="store_true",
                        help="產出每日日報（Markdown + JSON）")
    parser.add_argument("--boot", type=int, default=baserate.N_BOOT,
                        help="bootstrap 重抽次數")
    parser.add_argument("--tw", type=int, default=config.TW_COUNT, help="台股檔數")
    parser.add_argument("--us", type=int, default=config.US_COUNT, help="美股檔數")
    return parser


def _run_server() -> int:
    """啟動本地即時模式（spec 8.6）。僅綁 127.0.0.1。"""
    from src.serve import serve as make_server

    if not config.OUT_HTML.exists():
        print("index.html 不存在，請先執行一次 python run.py", file=sys.stderr)
        return 1

    server = make_server()
    host, port = server.server_address[0], server.server_address[1]
    print(f"即時模式已啟動： http://{host}:{port}")
    print("  台股報價為 TWSE MIS 即時值；美股經 Yahoo，延遲約 15 分鐘。")
    print("  按 Ctrl+C 結束。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


def _report_accuracy() -> int:
    """比對留存的預測與實際走勢（見 src/accuracy.py）。"""
    conn = store.connect(config.DB_PATH)
    store.init_schema(conn)
    try:
        scored = accuracy.score(conn)
        if scored.frame.empty:
            print("沒有可比對的預測紀錄。先執行一次 python run.py 留存預測，"
                  "隔一個交易日之後再回來看。", file=sys.stderr)
            return 1

        print(f"預測期間 {scored.horizon_days} 個交易日"
              f"｜留存筆數 {len(scored.frame)}")
        for market, summary in sorted(accuracy.by_market(scored).items()):
            print(f"\n[{market}]")
            for line in accuracy.format_summary(summary):
                print(line)
        print("\n[全體]")
        for line in accuracy.format_summary(accuracy.summarise(scored)):
            print(line)

        misses = accuracy.worst_misses(scored)
        if not misses.empty:
            print("\n誤差最大的幾檔：")
            for _, r in misses.iterrows():
                print(f"  {r['symbol']:<10} {str(r.get('name') or '')[:10]:<12}"
                      f"預測 {r['upside_pct']:+7.1%}"
                      f"  實際超額 {r['actual_excess']:+7.2%}"
                      f"  信心 {r['confidence']:.0f}"
                      f"  ({int(r['elapsed_days'])} 日)")
    finally:
        conn.close()
    return 0


def _edge_fires(conn) -> tuple:
    """今天觸發、且該訊號有統計優勢的清單 —— 看多榜的唯一來源。

    沒有任何訊號判為 edge 時直接跳過掃描。掃描要對全部個股再算一次指標，
    而在沒有 edge 訊號的情況下結果必然是空的 —— 算了是純浪費。
    這也是目前的實際狀況：60 次檢定裡 0 個 edge。
    """
    rates = baserate.load_stats(conn)
    edge_keys = {
        (r.signal_key, r.market) for r in rates if r.verdict == "edge"
    }
    if not edge_keys:
        return ()

    frames, _ = baserate.load_frames(conn)
    usable = [s for s in signals.REGISTRY
              if any((s.key, m) in edge_keys for m in s.markets)]
    fires = baserate.scan_latest(usable, frames)
    return tuple(f for f in fires if (f.signal_key, f.market) in edge_keys)


def _run_baserates(n_boot: int) -> int:
    """重算並留存全部訊號的基準率（spec §3.6）。"""
    conn = store.connect(config.DB_PATH)
    store.init_schema(conn)
    try:
        frames, benchmarks = baserate.load_frames(conn)
        if not frames or not benchmarks:
            print("資料庫沒有足夠的價格資料。先執行一次 python run.py", file=sys.stderr)
            return 1

        tw = sum(1 for f in frames if f.market == "TW")
        n_tests = len(signals.REGISTRY) * 2 * len(baserate.HORIZONS)
        print(f"載入 {len(frames)} 檔（TW {tw}／US {len(frames) - tw}）"
              f"｜{len(frames[0].df)} 根 bar｜最多 {n_tests} 次檢定")

        rates = baserate.evaluate_all(
            signals.REGISTRY, frames, benchmarks, n_boot=n_boot, seed=42,
        )
        baserate.save_stats(conn, rates)

        counts: dict[str, int] = {}
        for r in rates:
            counts[r.verdict] = counts.get(r.verdict, 0) + 1
        summary = "｜".join(
            f"{digest.VERDICT_LABELS.get(k, k)} {v}" for k, v in sorted(counts.items())
        )
        print(f"\n完成 {len(rates)} 次量測並留存：{summary}")

        edges = [r for r in rates if r.verdict == "edge"]
        if not edges:
            print("\n沒有任何訊號通過統計優勢門檻。")
            print("  這不是失敗，是這份資料上的誠實答案 —— 沒有可證實的預測力，")
            print("  所以日報不會把任何標的稱為買點。")
        else:
            print(f"\n有統計優勢（{len(edges)} 組）：")
            for r in edges:
                print(f"  {r.signal_key} / {r.market} / {r.horizon}日"
                      f"｜勝率 {r.hit_rate:.1%} vs 基準 {r.baseline_hit_rate:.1%}"
                      f"｜有效樣本 {r.n_effective}｜p校正 {r.p_adjusted:.4f}")
        return 0
    finally:
        conn.close()


def _accuracy_lines(conn) -> list[str]:
    """給日報用的準度摘要。沒有留存紀錄時要明說，不得靜默省略。"""
    scored = accuracy.score(conn)
    if scored.frame.empty:
        return ["尚無可比對的留存預測 —— 下面的數字沒有任何 track record。"]
    lines = [f"留存 {len(scored.frame)} 筆預測，預測期間 {scored.horizon_days} 個交易日"]
    lines.extend(accuracy.format_summary(accuracy.summarise(scored)))
    return lines


def _plan_lines(path: Path) -> list[str]:
    """讀 portfolio.toml，把規則換算成後果。不挑股票、不建議比例。"""
    import tomllib

    from src import allocate

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    rules = allocate.Rules(
        capital=float(raw["本金"]),
        max_per_position=float(raw["單檔上限"]),
        max_positions=int(raw["最多持有檔數"]),
        tranches=tuple(float(x) for x in raw["每批比例"]),
        cash_reserve=float(raw.get("保留現金", 0.0)),
        max_drawdown_tolerance=raw.get("可承受最大回撤"),
        fee_discount=float(raw.get("手續費折扣", 1.0)),
    )
    symbols = list(raw.get("標的", []))
    if not symbols:
        return ["portfolio.toml 沒有列出「標的」。候選必須由你指定 ——"
                "挑哪幾檔是投資決策，程式不代為篩選。"]

    conn = store.connect(config.DB_PATH)
    try:
        market_of = pd.read_sql_query(
            "SELECT symbol, market FROM stocks", conn
        ).set_index("symbol")["market"].to_dict()
        history = pd.read_sql_query(
            "SELECT symbol, date, adjclose FROM prices ORDER BY symbol, date", conn
        )
    finally:
        conn.close()

    last_price = history.groupby("symbol")["adjclose"].last().to_dict()
    candidates = [
        allocate.Candidate(
            s, market_of.get(s, "TW"), float(last_price.get(s, float("nan"))),
        )
        for s in symbols
    ]
    plan = allocate.build_plan(rules, candidates)

    lines = [
        f"本金 {rules.capital:,.0f}｜保留現金 {rules.cash_reserve:.0%}"
        f"｜單檔上限 {rules.max_per_position:.0%}"
        f"｜分 {len(rules.tranches)} 批",
        "",
    ]
    if plan.rejected_reason:
        lines.append(f"**{plan.rejected_reason}**")
        return lines

    for pos in plan.positions:
        lots = (f"（{pos.lots} 整股 + {pos.odd_shares} 零股）"
                if pos.market == "TW" else "")
        lines.append(
            f"- **{pos.symbol}** @ {pos.price:,.2f}｜共 {pos.total_shares:,} 股{lots}"
            f"｜成本 {pos.total_cost:,.0f}｜手續費 {pos.total_fee:,.0f}"
            f"｜未用預算 {pos.shortfall:,.0f}｜佔本金 {pos.weight_of_capital:.1%}"
        )
        for t in pos.tranches:
            lines.append(
                f"    - 第 {t.index} 批（{t.ratio:.0%}）：{t.shares:,} 股，"
                f"成本 {t.cost:,.0f}，手續費 {t.fee:,.0f}"
            )

    lines.append("")
    lines.append(f"合計投入 {plan.invested:,.0f}｜剩餘現金 {plan.cash_left:,.0f}")

    if plan.positions and rules.max_drawdown_tolerance is not None:
        weights = {p.symbol: p.total_cost for p in plan.positions}
        stats = allocate.portfolio_stats(
            weights, history, tolerance=float(rules.max_drawdown_tolerance),
        )
        dd, vol = stats["max_drawdown"], stats["volatility"]
        lines.append("")
        lines.append(
            f"組合歷史最大回撤 {dd:.1%}"
            if dd is not None else "組合歷史最大回撤：資料不足"
        )
        if vol is not None:
            lines.append(f"組合歷史年化波動度 {vol:.1%}")
        if stats["avg_correlation"] is not None:
            lines.append(f"標的間平均相關係數 {stats['avg_correlation']:.2f}")
        if stats["exceeds_tolerance"] is True:
            lines.append(
                f"**超過你設定的可承受回撤 {float(rules.max_drawdown_tolerance):.0%}。**"
                "（僅回報事實，不建議換股）"
            )
        elif stats["exceeds_tolerance"] is False:
            lines.append(
                f"未超過你設定的可承受回撤 "
                f"{float(rules.max_drawdown_tolerance):.0%}。"
            )

    for note in plan.notes:
        lines.append(f"- 註：{note}")
    lines.append("")
    lines.append("> 以上是你設定的規則的算術後果，不是建議。歷史波動與回撤都是"
                 "**過去**的數字 —— 這 3 年沒發生過的情境不會出現在裡面。")
    return lines


def _run_digest(out_dir: Path) -> int:
    """產出每日日報（spec §7）。"""
    conn = store.connect(config.DB_PATH)
    store.init_schema(conn)
    try:
        frames, _ = baserate.load_frames(conn)
        if not frames:
            print("資料庫沒有價格資料。先執行一次 python run.py", file=sys.stderr)
            return 1

        rates = baserate.load_stats(conn)
        if not rates:
            print("尚未量測基準率。請先執行 python run.py --baserates",
                  file=sys.stderr)
            return 1

        fires = baserate.scan_latest(signals.REGISTRY, frames)
        as_of = max(str(f.df["date"].iloc[-1]) for f in frames)

        fired = {f.symbol for f in fires}
        bands = []
        for frame in frames:
            if frame.symbol not in fired:
                continue
            adj = frame.df["adjclose"]
            vol = risk.annualized_volatility(adj, 60)
            band = risk.volatility_band(
                price=float(adj.iloc[-1]),
                annual_vol=vol if vol is not None else float("nan"),
                horizon_days=digest.HORIZON_FOR_HEADLINE,
            )
            if band:
                bands.append({
                    "symbol": frame.symbol, "price": float(adj.iloc[-1]),
                    "low": band["low"], "high": band["high"],
                })

        plan_path = config.ROOT / "portfolio.toml"
        plan = None
        if plan_path.exists():
            try:
                plan = _plan_lines(plan_path)
            except Exception as exc:                              # noqa: BLE001
                plan = [f"portfolio.toml 讀取失敗：{exc}"]

        data = digest.build({
            "as_of": as_of,
            "last_success": store.get_meta(conn, "last_success"),
            "accuracy_lines": _accuracy_lines(conn),
            "fires": fires,
            "rates": rates,
            "bands": bands,
            "plan": plan,
            "degraded": [],
        })
        md_path, json_path = digest.write(data, out_dir)
        counts = {k: len(v) for k, v in data["signals"].items() if v}
        breakdown = "｜".join(
            f"{digest.VERDICT_LABELS.get(k, k)} {v}" for k, v in sorted(counts.items())
        )
        print(f"已產出 {md_path}（{md_path.stat().st_size / 1024:.1f} KB）"
              f"與 {json_path.name}")
        print(f"  今日觸發 {len(fires)} 筆：{breakdown or '無'}")
        if not data["signals"].get("edge"):
            print("  沒有任何訊號有統計優勢，所以日報不指出任何買點。")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    _make_console_forgiving()
    args = build_parser().parse_args(argv)

    if args.serve:
        return _run_server()

    if args.accuracy:
        return _report_accuracy()

    if args.baserates:
        return _run_baserates(args.boot)

    if args.digest:
        return _run_digest(config.ROOT / "out")

    conn = store.connect(config.DB_PATH)
    store.init_schema(conn)
    try:
        if not args.no_fetch:
            session = make_session()
            client = YahooClient(session, RateLimiter(config.YAHOO_MIN_INTERVAL))
            stats = fetch_all(
                conn,
                session,
                tw_limit=args.tw,
                us_limit=args.us,
                yahoo_client=client,
                progress=lambda m: print(m, flush=True),
            )
            # 排程器的任務會靜默失敗，而不可回補的資料（新聞、分析師目標價）
            # 每漏一天就永久少一天。日報標頭顯示這個日期並在落後時亮警告。
            store.set_meta(conn, "last_success",
                           pd.Timestamp.now().strftime("%Y-%m-%d"))
            print(
                f"抓取完成：選股池 {stats['universe']} 檔"
                f"｜個股失敗 {len(stats['failed'])}"
                f"｜降級來源 {len(stats['degraded'])}"
            )
            for line in stats["degraded"]:
                print(f"  降級：{line}")

        if args.fetch_only:
            return 0

        print("計算特徵、訓練模型、合成目標價…", flush=True)
        payload = analysis.analyse(conn, edge_fires=_edge_fires(conn))
        if not payload["stocks"]:
            print("資料庫沒有可分析的個股。先執行一次抓取。", file=sys.stderr)
            return 1

        report = payload["report"]
        if report["trained"]:
            ic = report.get("ic")
            momentum = report.get("momentum_ic")
            print(f"模型已訓練：樣本 {report['n_samples']:,} 筆"
                  f"｜測試集 IC {ic:.4f}" if ic is not None else "模型已訓練")
            if ic is not None and momentum is not None:
                verdict = "勝過" if ic > momentum else "未勝過"
                print(f"  對照動能基準 IC {momentum:.4f} -> {verdict}基準")
        else:
            print(f"技術面模型未訓練：{report['reason']}")

        # 留存預測：唯一能誠實回答「它到底準不準」的方式。回測用歷史資料，
        # 會受選樣與調參影響；留存的預測是事前寫死的，事後無從修改。
        saved = accuracy.save_snapshot(conn, payload)

        target = render.write_html(payload)
        size_mb = target.stat().st_size / 1024 / 1024
        predictable = sum(
            1 for s in payload["stocks"] if s["prediction"].get("predictable")
        )
        print(f"已產生 {target}（{size_mb:.2f} MB）")
        print(f"  {len(payload['stocks'])} 檔中 {predictable} 檔提供預測值，"
              f"其餘因資料或訊號品質不足而不給預測")
        print(f"  已留存 {saved} 筆預測供事後驗證（python run.py --accuracy）")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
