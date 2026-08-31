from datetime import UTC, datetime

import pytest

from src.dates import market_date, utc_now_iso


def test_taiwan_date_uses_taipei_not_utc():
    """台北 UTC+8：UTC 前一日 23:30 在台北已是隔天早上 07:30。

    用 UTC 日期會把當天的融資融券資料標成前一天，之後跟價格 join 就會錯位。
    """
    moment = datetime(2026, 8, 30, 23, 30, tzinfo=UTC)
    assert market_date("TW", moment) == "2026-08-31"
    assert moment.strftime("%Y-%m-%d") == "2026-08-30"  # 證明 UTC 會給出不同答案


def test_us_date_uses_new_york():
    """紐約 UTC-4/5：UTC 當日 02:00 在紐約還是前一天。"""
    moment = datetime(2026, 8, 31, 2, 0, tzinfo=UTC)
    assert market_date("US", moment) == "2026-08-30"


def test_same_instant_can_differ_between_markets():
    moment = datetime(2026, 8, 31, 2, 0, tzinfo=UTC)
    assert market_date("TW", moment) == "2026-08-31"
    assert market_date("US", moment) == "2026-08-30"


def test_unknown_market_raises():
    with pytest.raises(ValueError, match="未知的市場"):
        market_date("JP")


def test_defaults_to_now_and_returns_iso_shape():
    assert len(market_date("TW")) == 10
    assert market_date("TW")[4] == "-"


def test_utc_now_iso_has_second_precision():
    value = utc_now_iso()
    assert value.endswith("+00:00")
    assert "." not in value
