import pytest

from src import pipeline, store


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    store.init_schema(c)
    yield c
    c.close()


@pytest.fixture()
def stubs(monkeypatch):
    """把所有 fetch 函式換成固定回傳，驗證 pipeline 的組裝與寫入。"""
    monkeypatch.setattr(pipeline.twse, "fetch_day_all", lambda *a, **k: [
        {"code": "2330", "name": "台積電", "open": 2370.0, "high": 2395.0, "low": 2360.0,
         "close": 2380.0, "volume": 31234567, "trade_value": 74_000_000_000.0}])
    monkeypatch.setattr(pipeline.tpex, "fetch_quotes", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.twse, "fetch_valuation", lambda *a, **k: [
        {"code": "2330", "pe": 21.4, "dividend_yield": 1.51, "pb": 5.8}])
    monkeypatch.setattr(pipeline.twse, "fetch_monthly_revenue", lambda *a, **k: [
        {"code": "2330", "publish_date": "2026-08-10", "period": "2026-07",
         "industry": "半導體業", "revenue": 3.0e8, "yoy": 38.25, "mom": 7.14}])
    monkeypatch.setattr(pipeline.twse, "fetch_exright", lambda *a, **k: [
        {"code": "2330", "ex_date": "2026-09-11", "type": "息",
         "cash_dividend": 6.0, "stock_dividend": None}])
    monkeypatch.setattr(pipeline.twse, "fetch_margin", lambda *a, **k: [
        {"code": "2330", "margin_balance": 45250, "margin_balance_prev": 45000,
         "short_balance": 1210, "short_balance_prev": 1200}])
    monkeypatch.setattr(pipeline.tpex, "fetch_institutional", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.twse, "fetch_institutional", lambda *a, **k: [
        {"code": "2330", "date": "2026-08-28", "foreign_net": 18_000_000,
         "trust_net": 4_000_000, "dealer_net": -500_000}])
    monkeypatch.setattr(pipeline.twse, "fetch_company_profiles", lambda *a, **k: [
        {"code": "2330", "short_name": "台積電", "industry_code": "24",
         "listed_date": "1994-09-05"}])
    monkeypatch.setattr(pipeline.tpex, "fetch_company_profiles", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.yahoo, "fetch_dividend_events", lambda session, symbol, **k: [
        {"symbol": symbol, "ex_date": "2026-06-11", "cash_dividend": 6.0}])
    monkeypatch.setattr(pipeline.tdcc, "fetch_big_holders", lambda *a, **k: [
        {"code": "2330", "date": "2026-08-28", "big_holder_pct": 71.34}])
    monkeypatch.setattr(pipeline.universe, "parse_sp500", lambda html: [
        {"code": "AAPL", "name": "Apple Inc.", "sector": "Information Technology"}])
    monkeypatch.setattr(pipeline, "fetch_text", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(pipeline.yahoo, "fetch_chart", lambda session, symbol, **k: [
        {"symbol": symbol, "date": "2026-08-28", "open": 1.0, "high": 1.0,
         "low": 1.0, "close": 1.0, "adjclose": 1.0, "volume": 10}])
    monkeypatch.setattr(pipeline.yahoo, "fetch_us_fundamentals", lambda client, symbol: {
        "symbol": symbol, "pe": 36.6, "forward_pe": 33.5, "pb": 43.4, "dividend_yield": 0.0034,
        "market_cap": 4.6e12, "beta": 1.09, "revenue_yoy": 0.164, "gross_margin": 0.468,
        "op_margin": 0.321, "net_margin": 0.276, "roe": 1.49, "roa": 0.245,
        "debt_to_equity": 78.4, "current_ratio": 0.92, "free_cashflow": 9.8e10,
        "eps_forward": 9.42, "sector": "Technology", "industry": "Consumer Electronics",
        "ex_dividend_date": "2026-08-10", "earnings_date": "2026-10-29",
        "short_percent_float": 0.008, "held_percent_institutions": 0.664})
    monkeypatch.setattr(pipeline.yahoo, "fetch_us_analyst", lambda client, symbol: {
        "symbol": symbol, "target_mean": 324.45, "target_high": 400.0,
        "target_low": 215.0, "recommendation": "buy", "n_analysts": 39})
    monkeypatch.setattr(pipeline.news, "fetch_news", lambda *a, **k: [])


def _run(conn):
    return pipeline.fetch_all(conn, session=None, tw_limit=1, us_limit=1, yahoo_client=object())


def test_fetch_all_populates_stocks_for_both_markets(conn, stubs):
    _run(conn)
    rows = conn.execute("SELECT symbol, market FROM stocks ORDER BY symbol").fetchall()
    assert [(r["symbol"], r["market"]) for r in rows] == [("2330.TW", "TW"), ("AAPL", "US")]


def test_fetch_all_writes_prices_with_adjclose(conn, stubs):
    _run(conn)
    row = conn.execute("SELECT adjclose FROM prices WHERE symbol='2330.TW'").fetchone()
    assert row["adjclose"] == 1.0


def test_fetch_all_writes_dividends_and_margin(conn, stubs):
    _run(conn)
    dates = [r["ex_date"] for r in
             conn.execute("SELECT ex_date FROM dividends WHERE symbol='2330.TW' ORDER BY ex_date")]
    # 未來除權息來自 TWSE 預告表，歷史配息來自 Yahoo events —— 兩者都要有
    assert dates == ["2026-06-11", "2026-09-11"]
    mgn = conn.execute("SELECT * FROM margin WHERE symbol='2330.TW'").fetchone()
    assert mgn["margin_balance"] == 45250


def test_margin_date_uses_taipei_calendar_day(conn, stubs):
    """spec 9.3 延伸：台股籌碼資料的日期必須是台北日，不能是 UTC 日。"""
    from src.dates import market_date
    _run(conn)
    row = conn.execute("SELECT date FROM margin WHERE symbol='2330.TW'").fetchone()
    assert row["date"] == market_date("TW")


def test_fundamentals_asof_uses_revenue_publish_date(conn, stubs):
    """spec 6.1 R3：基本面的有效日期是公布日，不是所屬期間。"""
    _run(conn)
    row = conn.execute("SELECT asof FROM fundamentals WHERE symbol='2330.TW'").fetchone()
    assert row["asof"] == "2026-08-10"


def test_fetch_all_writes_us_analyst_targets(conn, stubs):
    _run(conn)
    row = conn.execute("SELECT * FROM analyst WHERE symbol='AAPL'").fetchone()
    assert row["target_mean"] == 324.45 and row["n_analysts"] == 39


def test_fetch_all_records_run_timestamp(conn, stubs):
    _run(conn)
    assert store.get_meta(conn, "last_fetch") is not None


def test_single_symbol_failure_does_not_abort_run(conn, stubs, monkeypatch):
    """spec 9.1：任何單一來源失效都不得讓整份報表產不出來。"""
    def boom(session, symbol, **kw):
        if symbol == "AAPL":
            raise RuntimeError("Yahoo 429")
        return [{"symbol": symbol, "date": "2026-08-28", "open": 1.0, "high": 1.0,
                 "low": 1.0, "close": 1.0, "adjclose": 1.0, "volume": 10}]

    monkeypatch.setattr(pipeline.yahoo, "fetch_chart", boom)
    stats = _run(conn)
    assert conn.execute(
        "SELECT COUNT(*) c FROM prices WHERE symbol='2330.TW'").fetchone()["c"] == 1
    assert "AAPL" in stats["failed"]


def test_batch_source_failure_does_not_abort_run(conn, stubs, monkeypatch):
    """整個批次來源掛掉（例如 TDCC 逾時）也必須續跑。"""
    def boom(*a, **k):
        raise RuntimeError("TDCC timeout")

    monkeypatch.setattr(pipeline.tdcc, "fetch_big_holders", boom)
    stats = _run(conn)
    assert conn.execute("SELECT COUNT(*) c FROM stocks").fetchone()["c"] == 2
    assert any("holders" in s for s in stats["degraded"])


def test_taiwan_industry_is_populated_from_revenue_text(conn, stubs):
    """使用者明確要求的產業分類欄位不得為空。"""
    _run(conn)
    row = conn.execute("SELECT sector, industry FROM stocks WHERE symbol='2330.TW'").fetchone()
    assert row["sector"] == "半導體業"
    assert row["industry"] == "半導體業"


def test_listed_date_recorded_for_new_listing_filter(conn, stubs):
    """spec 7.5：上市未滿 120 日不預測，需要上市日期才能判斷。"""
    _run(conn)
    row = conn.execute("SELECT listed_date FROM stocks WHERE symbol='2330.TW'").fetchone()
    assert row["listed_date"] == "1994-09-05"


def test_listed_institutional_flows_are_written(conn, stubs):
    """上市三大法人走 T86；先前只做上櫃，導致這張表全空。"""
    _run(conn)
    row = conn.execute("SELECT * FROM institutional WHERE symbol='2330.TW'").fetchone()
    assert row is not None
    assert row["foreign_net"] == 18_000_000
    assert row["trust_net"] == 4_000_000
    assert row["dealer_net"] == -500_000
    # 標註的是來源實際資料日，不是執行當天
    assert row["date"] == "2026-08-28"
