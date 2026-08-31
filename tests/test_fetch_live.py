import json
from pathlib import Path

import pytest

from src.fetch import live

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def payload():
    return json.loads((FIXTURES / "mis_response.json").read_text(encoding="utf-8"))


def test_fixture_reproduces_real_dash_price(payload):
    """實測 2330 於 11:53:58 的成交價欄位就是字串 "-"。"""
    assert payload["msgArray"][0]["z"] == "-"


def test_build_ex_ch_maps_market_prefix():
    assert live.build_ex_ch(["2330.TW", "6488.TWO"]) == "tse_2330.tw|otc_6488.tw"


def test_parse_handles_dash_price_by_falling_back_to_prev_close(payload):
    """spec 9.3：無成交時 z 為字串 "-"，直接 float() 會 ValueError。"""
    rows = {r["code"]: r for r in live.parse_mis(payload)}
    tsmc = rows["2330"]
    assert tsmc["price"] == 2420.0       # fallback 至昨收 y
    assert tsmc["prev_close"] == 2420.0
    assert tsmc["is_stale"] is True      # 標記為非成交價


def test_parse_uses_actual_price_when_available(payload):
    rows = {r["code"]: r for r in live.parse_mis(payload)}
    assert rows["2317"]["price"] == 238.5
    assert rows["2317"]["is_stale"] is False


def test_parse_returns_empty_when_market_closed():
    """spec 9.2：rtcode 非 0000 或 msgArray 空 -> 視為休市。"""
    assert live.parse_mis({"rtcode": "5001", "msgArray": []}) == []
    assert live.parse_mis({"rtcode": "0000", "msgArray": []}) == []


def test_row_without_any_price_is_skipped():
    """z 與 y 都缺時不得產生 price=None 的列。"""
    assert live.parse_mis({"rtcode": "0000", "msgArray": [{"c": "X", "z": "-", "y": "-"}]}) == []


def test_fetch_mis_primes_session_cookie_first(monkeypatch, payload):
    """spec 9.3：須先 GET index.jsp 建立 session，否則 API 被拒。"""
    calls = []

    class _S:
        def get(self, url, **kw):
            calls.append(url)

            class _R:
                status_code = 200

                def raise_for_status(self):
                    pass

            return _R()

    monkeypatch.setattr(live, "fetch_json", lambda s, url, **kw: payload)
    live.fetch_mis(_S(), ["2330.TW"])
    assert any("index.jsp" in u for u in calls)
