import json
from pathlib import Path

import pytest

from src.fetch import yahoo

FIXTURES = Path(__file__).parent / "fixtures"


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.modules_seen = None

    def quote_summary(self, symbol, modules):
        self.modules_seen = modules
        return self.payload


@pytest.fixture()
def client():
    return _Client(json.loads((FIXTURES / "yahoo_summary_aapl.json").read_text(encoding="utf-8")))


def test_requests_all_required_modules(client):
    yahoo.fetch_us_fundamentals(client, "AAPL")
    assert set(client.modules_seen) >= {
        "summaryDetail", "defaultKeyStatistics", "financialData",
        "summaryProfile", "calendarEvents", "earningsTrend",
    }


def test_fundamentals_unwrap_raw_values(client):
    out = yahoo.fetch_us_fundamentals(client, "AAPL")
    assert out["pe"] == 36.62
    assert out["forward_pe"] == 33.52
    assert out["pb"] == 43.4375
    assert out["net_margin"] == 0.27618
    assert out["sector"] == "Technology"
    assert out["industry"] == "Consumer Electronics"
    assert out["short_percent_float"] == 0.008


def test_forward_eps_taken_from_plus_one_year(client):
    """spec 6.2：前瞻 EPS 取 earningsTrend 的 +1y，不是 0q。"""
    assert yahoo.fetch_us_fundamentals(client, "AAPL")["eps_forward"] == 9.42


def test_event_dates_converted_to_iso_matching_yahoo_fmt(client):
    """換算結果必須與 Yahoo 自己的 fmt 欄位一致。"""
    payload = client.payload
    out = yahoo.fetch_us_fundamentals(client, "AAPL")
    assert out["ex_dividend_date"] == payload["summaryDetail"]["exDividendDate"]["fmt"]
    assert out["earnings_date"] == \
        payload["calendarEvents"]["earnings"]["earningsDate"][0]["fmt"]


def test_analyst_targets(client):
    out = yahoo.fetch_us_analyst(client, "AAPL")
    assert out["target_mean"] == 324.45
    assert out["target_high"] == 400.0
    assert out["target_low"] == 215.0
    assert out["n_analysts"] == 39
    assert out["recommendation"] == "buy"


def test_missing_fields_become_none():
    out = yahoo.fetch_us_fundamentals(_Client({"summaryDetail": {}}), "X")
    assert out["pe"] is None
    assert out["sector"] is None
    assert out["eps_forward"] is None
    assert out["earnings_date"] is None
