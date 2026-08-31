"""進入點（spec 12）。

第一階段只實作資料抓取；--no-fetch / --serve / --backtest 需等後續階段。
"""
from __future__ import annotations

import argparse
import sys

import config
from src import analysis, render, store
from src.http import RateLimiter, YahooClient, make_session
from src.pipeline import fetch_all

_NOT_YET = "此模式屬後續階段，尚未實作：{name}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="台股／美股預測分析平台")
    parser.add_argument("--full", action="store_true", help="強制全量重抓")
    parser.add_argument("--no-fetch", action="store_true", help="只重算模型與頁面")
    parser.add_argument("--fetch-only", action="store_true", help="只抓資料，不算模型")
    parser.add_argument("--only", metavar="SYMBOL", help="單檔除錯")
    parser.add_argument("--serve", action="store_true", help="啟動本地即時模式")
    parser.add_argument("--backtest", action="store_true", help="產出回測驗證報告")
    parser.add_argument("--tw", type=int, default=config.TW_COUNT, help="台股檔數")
    parser.add_argument("--us", type=int, default=config.US_COUNT, help="美股檔數")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.serve:
        print(_NOT_YET.format(name="--serve"), file=sys.stderr)
        return 2

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

        target = render.write_html(payload)
        size_mb = target.stat().st_size / 1024 / 1024
        predictable = sum(
            1 for s in payload["stocks"] if s["prediction"].get("predictable")
        )
        print(f"已產生 {target}（{size_mb:.2f} MB）")
        print(f"  {len(payload['stocks'])} 檔中 {predictable} 檔提供預測值，"
              f"其餘因資料或訊號品質不足而不給預測")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
