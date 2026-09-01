"""進入點（spec 12）。

第一階段只實作資料抓取；--no-fetch / --serve / --backtest 需等後續階段。
"""
from __future__ import annotations

import argparse
import sys

import config
from src import accuracy, analysis, render, store
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


def main(argv: list[str] | None = None) -> int:
    _make_console_forgiving()
    args = build_parser().parse_args(argv)

    if args.serve:
        return _run_server()

    if args.accuracy:
        return _report_accuracy()

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
        payload = analysis.analyse(conn)
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
