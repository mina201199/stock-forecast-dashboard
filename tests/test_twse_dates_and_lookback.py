"""兩個實跑才發現的問題。

1. t187ap03_L 在同一份 payload 裡混用兩種日期格式：
   '出表日期'='1150830'（7 碼民國）、'上市日期'='19620209'（8 碼西元）。
   用民國解析器讀西元欄位會靜默得到 None。
2. T86 在收盤前不會有當日資料（回「很抱歉，沒有符合條件的資料!」），
   若不往回找就會在交易日盤中完全拿不到籌碼資料。
"""
import pytest

from src.fetch import twse


def test_gregorian_and_roc_parsers_are_distinct():
    assert twse.roc_to_iso("1150830") == "2026-08-30"     # 7 碼民國
    assert twse.gregorian_to_iso("19620209") == "1962-02-09"  # 8 碼西元


def test_roc_parser_rejects_gregorian_input():
    """關鍵：不能讓 8 碼西元被民國解析器吃掉而變成 None 或錯誤日期。"""
    assert twse.roc_to_iso("19620209") is None


def test_gregorian_parser_rejects_roc_input():
    assert twse.gregorian_to_iso("1150830") is None
    assert twse.gregorian_to_iso("") is None
    assert twse.gregorian_to_iso(None) is None


def test_company_profile_uses_gregorian_for_listing_date(monkeypatch):
    monkeypatch.setattr(twse, "fetch_json", lambda *a, **k: [{
        "公司代號": "1101", "公司簡稱": "台泥", "產業別": "01",
        "出表日期": "1150830", "成立日期": "19501229", "上市日期": "19620209",
    }])
    row = twse.fetch_company_profiles(session=None)[0]
    assert row["listed_date"] == "1962-02-09"
    assert row["industry_code"] == "01"


def test_institutional_walks_back_to_last_published_day(monkeypatch):
    """收盤前查當日會落空，須自動往回找最近一個有資料的交易日。"""
    seen = []
    ok_payload = {
        "stat": "OK",
        "fields": ["證券代號", "外陸資買賣超股數(不含外資自營商)",
                   "投信買賣超股數", "自營商買賣超股數"],
        "data": [["2330", "18,000,000", "4,000,000", "-500,000"]],
    }

    def fake(session, url, params=None, **kw):
        seen.append(params["date"])
        if params["date"] == "20260828":
            return ok_payload
        return {"stat": "很抱歉，沒有符合條件的資料!"}

    monkeypatch.setattr(twse, "fetch_json", fake)
    rows = twse.fetch_institutional(session=None, date="2026-08-31")
    assert seen == ["20260831", "20260830", "20260829", "20260828"]
    assert rows[0]["foreign_net"] == 18_000_000
    assert rows[0]["date"] == "2026-08-28"  # 標註實際資料日，不能謊報成今天


def test_institutional_gives_up_after_lookback_limit(monkeypatch):
    monkeypatch.setattr(twse, "fetch_json", lambda *a, **k: {"stat": "no data"})
    rows = twse.fetch_institutional(session=None, date="2026-08-31", lookback_days=3)
    assert rows == []


def test_institutional_lookback_is_bounded(monkeypatch):
    calls = []

    def fake(session, url, params=None, **kw):
        calls.append(params["date"])
        return {"stat": "no data"}

    monkeypatch.setattr(twse, "fetch_json", fake)
    twse.fetch_institutional(session=None, date="2026-08-31", lookback_days=5)
    assert len(calls) == 6  # 當日 + 往回 5 天


@pytest.mark.parametrize("stat", ["OK", "ok"])
def test_stat_check_is_case_insensitive(monkeypatch, stat):
    monkeypatch.setattr(twse, "fetch_json", lambda *a, **k: {
        "stat": stat, "fields": ["證券代號", "外陸資買賣超股數(不含外資自營商)",
                                 "投信買賣超股數", "自營商買賣超股數"],
        "data": [["2330", "1", "2", "3"]]})
    assert len(twse.fetch_institutional(session=None, date="2026-08-31")) == 1
