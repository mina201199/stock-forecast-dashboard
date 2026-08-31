import json
from pathlib import Path

import pytest

from src.fetch import tpex

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def patched(monkeypatch):
    def fake(session, url, **kw):
        if "daily_close_quotes" in url:
            return _load("tpex_quotes.json")
        if "3insti" in url:
            return _load("tpex_3insti.json")
        raise AssertionError(f"未預期的 URL: {url}")

    monkeypatch.setattr(tpex, "fetch_json", fake)


def test_fixture_really_contains_inconsistent_spacing():
    """測資必須忠實重現實測到的欄位名怪癖，否則這個測試沒有意義。"""
    raw = _load("tpex_3insti.json")[0]
    assert any(k.startswith(" ") for k in raw), "fixture 應含前導空格的欄位名"
    assert "Dealers -TotalSell" in raw, "fixture 應含中間多空格的欄位名"


def test_to_symbol_uses_two_suffix():
    assert tpex.to_symbol("6488") == "6488.TWO"


def test_normalize_key_strips_all_whitespace():
    """spec 9.3：TPEx 欄位名含不一致空格。"""
    a = tpex.normalize_key(
        " Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell"
    )
    b = tpex.normalize_key(
        "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell"
    )
    assert a == b
    assert tpex.normalize_key("Dealers -TotalSell") == tpex.normalize_key("Dealers-TotalSell")


def test_fetch_quotes_parses(patched):
    row = tpex.fetch_quotes(session=None)[0]
    assert row["code"] == "6488"
    assert row["close"] == 620.0
    assert row["trade_value"] == 1_980_000_000.0
    assert row["name"] == "環球晶"


def test_fetch_institutional_survives_inconsistent_spacing(patched):
    """欄位名帶前導/中間空格時仍須正確取值，不得 KeyError。"""
    row = tpex.fetch_institutional(session=None)[0]
    assert row["code"] == "6488"
    assert row["foreign_net"] == 171000
    assert row["trust_net"] == 52000
    assert row["dealer_net"] == -8000


def test_fixture_contains_real_colliding_keys():
    """實測回應同時有 'Dealers-TotalSell' 與 'Dealers -TotalSell'。

    兩者正規化後會撞成同一個鍵，測資必須重現這個狀況。
    """
    raw = _load("tpex_3insti.json")[0]
    assert "Dealers-TotalSell" in raw and "Dealers -TotalSell" in raw
    assert tpex.normalize_key("Dealers-TotalSell") == tpex.normalize_key("Dealers -TotalSell")


def test_colliding_keys_are_detected_not_silently_overwritten():
    """碰撞須被記錄下來，而不是讓後者靜默蓋掉前者。"""
    row = {"Dealers-TotalSell": "19000", "Dealers -TotalSell": "3000", "A": "1"}
    normalized, collisions = tpex.normalize_row(row)
    assert collisions == {"dealers-totalsell"}
    assert normalized["dealers-totalsell"] == "19000"  # 保留第一個出現的
    assert normalized["a"] == "1"


def test_difference_fields_unaffected_by_collision(patched):
    """碰撞發生在 TotalSell，不得波及我們實際使用的 Difference 欄位。"""
    row = tpex.fetch_institutional(session=None)[0]
    assert row["dealer_net"] == -8000
    assert row["foreign_net"] == 171000
