import json
from pathlib import Path

import pytest

from src.fetch import yahoo

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def patched(monkeypatch):
    payload = json.loads((FIXTURES / "yahoo_chart_2330.json").read_text(encoding="utf-8"))
    captured = {}

    def fake(session, url, params=None, **kw):
        captured["url"] = url
        captured["params"] = params or {}
        return payload

    monkeypatch.setattr(yahoo, "fetch_json", fake)
    return captured


def test_chart_request_includes_events_param(patched):
    """spec 2.3：不帶 events 就不會回傳 adjclose。"""
    yahoo.fetch_chart(session=None, symbol="2330.TW")
    assert patched["params"].get("events") == "div,split"
    assert patched["params"].get("interval") == "1d"


def test_fixture_reflects_real_ex_dividend_adjustment():
    """實測值：2330 於 2026-06-11 除息 6 元，前一日 close 2255 / adjclose 2249。"""
    payload = json.loads((FIXTURES / "yahoo_chart_2330.json").read_text(encoding="utf-8"))
    ind = payload["chart"]["result"][0]["indicators"]
    assert ind["quote"][0]["close"][0] - ind["adjclose"][0]["adjclose"][0] == 6.0


def test_chart_rows_include_adjclose_distinct_from_close(patched):
    rows = yahoo.fetch_chart(session=None, symbol="2330.TW")
    assert len(rows) == 3
    first = rows[0]
    assert first["close"] == 2255.0
    assert first["adjclose"] == 2249.0  # 除息還原後低 6 元
    assert first["symbol"] == "2330.TW"


def test_chart_dates_are_iso_and_ascending(patched):
    rows = yahoo.fetch_chart(session=None, symbol="2330.TW")
    assert [r["date"] for r in rows] == ["2026-06-10", "2026-06-11", "2026-06-12"]


def test_dividend_events_extracted(patched):
    events = yahoo.fetch_dividend_events(session=None, symbol="2330.TW")
    assert len(events) == 1
    assert round(events[0]["cash_dividend"], 2) == 6.0
    assert events[0]["ex_date"] == "2026-06-11"


def test_missing_bars_are_skipped(monkeypatch):
    """Yahoo 對停牌日會回傳 null，該列必須跳過而非寫入 None 價格。"""
    payload = {"chart": {"result": [{"meta": {"symbol": "X"}, "timestamp": [1781053200, 1781139600],
        "indicators": {"quote": [{"open": [1.0, None], "high": [1.0, None], "low": [1.0, None],
        "close": [1.0, None], "volume": [10, None]}], "adjclose": [{"adjclose": [1.0, None]}]}}]}}
    monkeypatch.setattr(yahoo, "fetch_json", lambda *a, **k: payload)
    rows = yahoo.fetch_chart(session=None, symbol="X")
    assert len(rows) == 1


def test_empty_result_raises(monkeypatch):
    monkeypatch.setattr(yahoo, "fetch_json", lambda *a, **k: {"chart": {"result": []}})
    with pytest.raises(RuntimeError, match="無結果"):
        yahoo.fetch_chart(session=None, symbol="X")
