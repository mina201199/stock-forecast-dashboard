"""進入點（spec 12）。

第一階段只實作資料抓取；--no-fetch / --serve / --backtest 需等後續階段。
"""
from __future__ import annotations

import argparse
import sys

import config
from src import store
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

    for flag, name in (("serve", "--serve"), ("backtest", "--backtest")):
        if getattr(args, flag):
            print(_NOT_YET.format(name=name), file=sys.stderr)
            return 2

    conn = store.connect(config.DB_PATH)
    store.init_schema(conn)
    try:
        if args.no_fetch:
            print(_NOT_YET.format(name="--no-fetch"), file=sys.stderr)
            return 2

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
            f"完成：選股池 {stats['universe']} 檔"
            f"｜個股失敗 {len(stats['failed'])}"
            f"｜降級來源 {len(stats['degraded'])}"
        )
        for line in stats["degraded"]:
            print(f"  降級：{line}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
