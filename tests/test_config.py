from pathlib import Path

import config
from src import constants


def test_config_has_required_values():
    assert config.TW_COUNT == 300
    assert config.US_COUNT == 200
    assert config.HISTORY_YEARS == 3
    assert config.HORIZON_DAYS == 20
    assert config.TW_BENCHMARK == "0050.TW"
    assert config.US_BENCHMARK == "SPY"
    assert isinstance(config.DB_PATH, Path)


def test_sec_email_defaults_empty_to_disable_fallback():
    """spec 2.6：留空時自動停用 SEC 備援，不得填假信箱。"""
    assert config.SEC_CONTACT_EMAIL == ""


def test_twse_chinese_field_names_are_exact():
    """spec 9.3：TWSE 中文欄位鍵須集中定義。"""
    assert constants.REV_PUBLISH_DATE == "出表日期"
    assert constants.REV_YM == "資料年月"
    assert constants.REV_CODE == "公司代號"
    assert constants.REV_MONTH_REVENUE == "營業收入-當月營收"
    assert constants.REV_YOY == "營業收入-去年同月增減(%)"
    assert constants.MARGIN_CODE == "股票代號"
    assert constants.MARGIN_BALANCE_TODAY == "融資今日餘額"
    assert constants.SHORT_BALANCE_TODAY == "融券今日餘額"
