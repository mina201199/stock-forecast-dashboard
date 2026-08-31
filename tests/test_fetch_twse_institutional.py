import json
from pathlib import Path

import pytest

from src.fetch import twse

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def patched(monkeypatch):
    payload = json.loads((FIXTURES / "twse_t86.json").read_text(encoding="utf-8"))
    captured = {}

    def fake(session, url, params=None, **kw):
        captured["params"] = params or {}
        return payload

    monkeypatch.setattr(twse, "fetch_json", fake)
    return captured


def test_request_carries_compact_date_and_all_type(patched):
    twse.fetch_institutional(session=None, date="2026-08-28")
    assert patched["params"]["date"] == "20260828"
    assert patched["params"]["selectType"] == "ALL"


def test_parses_net_positions_stripping_thousand_separators(patched):
    rows = {r["code"]: r for r in twse.fetch_institutional(session=None, date="2026-08-28")}
    tsmc = rows["2330"]
    assert tsmc["foreign_net"] == 18_000_000
    assert tsmc["trust_net"] == 4_000_000
    assert tsmc["dealer_net"] == -500_000


def test_handles_negative_values(patched):
    rows = {r["code"]: r for r in twse.fetch_institutional(session=None, date="2026-08-28")}
    assert rows["1101"]["foreign_net"] == -2_000_000
    assert rows["1101"]["trust_net"] == -500_000


def test_non_ok_stat_returns_empty(monkeypatch):
    """非交易日 TWSE 回 stat != OK，須視為休市而非錯誤。"""
    monkeypatch.setattr(twse, "fetch_json", lambda *a, **k: {"stat": "查詢日期小於93年1月1日"})
    assert twse.fetch_institutional(session=None, date="2026-08-30") == []


def test_columns_located_by_field_name_not_fixed_index(patched):
    """欄位順序若變動，以名稱定位才不會靜默取到錯的數字。"""
    payload = json.loads((FIXTURES / "twse_t86.json").read_text(encoding="utf-8"))
    idx = payload["fields"].index("投信買賣超股數")
    assert idx == 10  # 目前位置；程式不得寫死此值
    assert twse.INSTI_TRUST_FIELD == "投信買賣超股數"
