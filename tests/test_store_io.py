import pandas as pd
import pytest

from src import store


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    store.init_schema(c)
    yield c
    c.close()


def _price(symbol, date, close):
    return {
        "symbol": symbol, "date": date, "open": close, "high": close,
        "low": close, "close": close, "adjclose": close, "volume": 100,
    }


def test_upsert_inserts_and_replaces(conn):
    assert store.upsert(conn, "prices", [_price("2330.TW", "2026-08-28", 2380)]) == 1
    store.upsert(conn, "prices", [_price("2330.TW", "2026-08-28", 2400)])
    rows = conn.execute("SELECT close FROM prices").fetchall()
    assert len(rows) == 1 and rows[0]["close"] == 2400


def test_upsert_empty_list_is_noop(conn):
    assert store.upsert(conn, "prices", []) == 0


def test_get_last_date_returns_none_when_empty(conn):
    assert store.get_last_date(conn, "prices", "2330.TW") is None


def test_get_last_date_returns_max_for_symbol(conn):
    store.upsert(conn, "prices", [
        _price("2330.TW", "2026-08-26", 1), _price("2330.TW", "2026-08-28", 2),
        _price("AAPL", "2026-08-29", 3),
    ])
    assert store.get_last_date(conn, "prices", "2330.TW") == "2026-08-28"
    assert store.get_last_date(conn, "prices", "AAPL") == "2026-08-29"


def test_get_last_date_supports_alternate_column(conn):
    store.upsert(conn, "dividends", [
        {"symbol": "2330.TW", "ex_date": "2026-06-11", "type": "息",
         "cash_dividend": 6.0, "stock_dividend": 0.0, "pay_date": None},
    ])
    assert store.get_last_date(conn, "dividends", "2330.TW", column="ex_date") == "2026-06-11"


def test_meta_roundtrip(conn):
    assert store.get_meta(conn, "missing") is None
    store.set_meta(conn, "last_run", "2026-08-31")
    store.set_meta(conn, "last_run", "2026-09-01")
    assert store.get_meta(conn, "last_run") == "2026-09-01"


def test_read_prices_returns_sorted_dataframe(conn):
    store.upsert(conn, "prices", [
        _price("2330.TW", "2026-08-28", 2380), _price("2330.TW", "2026-08-26", 2350),
    ])
    df = store.read_prices(conn, "2330.TW")
    assert isinstance(df, pd.DataFrame)
    assert list(df["date"]) == ["2026-08-26", "2026-08-28"]
    assert list(df["adjclose"]) == [2350.0, 2380.0]


def test_upsert_rejects_unknown_table(conn):
    with pytest.raises(ValueError, match="未知的表"):
        store.upsert(conn, "drop_me", [{"a": 1}])


def test_get_last_date_rejects_unknown_column(conn):
    """欄位名直接進 SQL，必須白名單防注入。"""
    with pytest.raises(ValueError, match="未知的日期欄位"):
        store.get_last_date(conn, "prices", "X", column="date; DROP TABLE prices--")
