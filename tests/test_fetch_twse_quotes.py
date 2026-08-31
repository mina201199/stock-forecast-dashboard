import json
from pathlib import Path

import pytest

from src.fetch import twse

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def patched(monkeypatch):
    """把 fetch_json 換成依 URL 回 fixture 的假函式（不打真網路）。"""
    def fake(session, url, **kw):
        if "STOCK_DAY_ALL" in url:
            return _load("twse_stock_day_all.json")
        if "BWIBBU_ALL" in url:
            return _load("twse_bwibbu_all.json")
        raise AssertionError(f"未預期的 URL: {url}")

    monkeypatch.setattr(twse, "fetch_json", fake)


def test_to_symbol_appends_tw_suffix():
    assert twse.to_symbol("2330") == "2330.TW"


def test_fetch_day_all_parses_numbers(patched):
    rows = twse.fetch_day_all(session=None)
    tsmc = next(r for r in rows if r["code"] == "2330")
    assert tsmc["close"] == 2380.0
    assert tsmc["trade_value"] == 74_000_000_000.0
    assert tsmc["volume"] == 31_234_567
    assert tsmc["name"] == "台積電"


def test_fetch_valuation_blank_pe_becomes_none(patched):
    """台泥 PEratio 為空字串（虧損時 TWSE 就是給空字串），必須成為 None 而非 0。"""
    rows = {r["code"]: r for r in twse.fetch_valuation(session=None)}
    assert rows["2330"]["pe"] == 21.40
    assert rows["1101"]["pe"] is None
    assert rows["1101"]["dividend_yield"] == 3.29


def test_fetch_day_all_keeps_etf_codes(patched):
    """ETF（00400A）不在此層過濾，交由 universe 決定。"""
    codes = {r["code"] for r in twse.fetch_day_all(session=None)}
    assert "00400A" in codes
