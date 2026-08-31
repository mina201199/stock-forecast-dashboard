import json
from pathlib import Path

import pytest

from src.fetch import twse

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def patched(monkeypatch):
    def fake(session, url, **kw):
        if "t187ap05_L" in url:
            return _load("twse_revenue.json")
        if "TWT48U_ALL" in url:
            return _load("twse_exright.json")
        if "MI_MARGN" in url:
            return _load("twse_margin.json")
        raise AssertionError(f"未預期的 URL: {url}")

    monkeypatch.setattr(twse, "fetch_json", fake)


def test_roc_to_iso_converts_roc_year():
    assert twse.roc_to_iso("1150817") == "2026-08-17"
    assert twse.roc_to_iso("1150911") == "2026-09-11"


def test_roc_to_iso_returns_none_for_blank():
    assert twse.roc_to_iso("") is None
    assert twse.roc_to_iso(None) is None
    assert twse.roc_to_iso("abc") is None


def test_monthly_revenue_uses_publish_date_not_period(patched):
    """spec 6.1 R3：必須以公布日對齊，7 月營收是 8 月才公布。"""
    rows = {r["code"]: r for r in twse.fetch_monthly_revenue(session=None)}
    tsmc = rows["2330"]
    assert tsmc["period"] == "2026-07"           # 所屬期間
    assert tsmc["publish_date"] == "2026-08-10"  # 公布日，晚於所屬期間
    assert tsmc["publish_date"] > tsmc["period"]
    assert tsmc["yoy"] == 38.25
    assert tsmc["industry"] == "半導體業"


def test_exright_parses_ex_date_and_cash(patched):
    rows = {r["code"]: r for r in twse.fetch_exright(session=None)}
    assert rows["2330"]["ex_date"] == "2026-09-11"
    assert rows["2330"]["type"] == "息"
    assert rows["2330"]["cash_dividend"] == 6.0
    assert rows["00400A"]["cash_dividend"] is None  # 空字串不得變成 0


def test_margin_parses_balances(patched):
    row = twse.fetch_margin(session=None)[0]
    assert row["code"] == "2330"
    assert row["margin_balance"] == 45250
    assert row["margin_balance_prev"] == 45000
    assert row["short_balance"] == 1210
