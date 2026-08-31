"""市場營業日換算。

台股資料的日期必須以台北時間為準，美股以紐約時間為準。
用 UTC 日期會在台北時間早上 08:00 前把資料標成前一天
（UTC+8，UTC 尚未跨日），之後與價格表 join 就會錯位一天。
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import config

_MARKET_TZ = {
    "TW": ZoneInfo(config.TAIPEI_TZ),
    "US": ZoneInfo(config.NY_TZ),
}


def market_date(market: str, now: datetime | None = None) -> str:
    """該市場當地的日曆日（ISO）。"""
    tz = _MARKET_TZ.get(market.upper())
    if tz is None:
        raise ValueError(f"未知的市場：{market}")
    moment = now or datetime.now(UTC)
    return moment.astimezone(tz).strftime("%Y-%m-%d")


def utc_now_iso() -> str:
    """UTC 時戳，秒精度。用於 meta 表的更新時間，與市場日期無關。"""
    return datetime.now(UTC).isoformat(timespec="seconds")
