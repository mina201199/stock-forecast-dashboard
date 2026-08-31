import sqlite3

import pytest

from src import store


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    store.init_schema(c)
    yield c
    c.close()


def test_creates_all_eleven_tables(conn):
    """spec 4：11 張表。"""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r["name"] for r in rows}
    expected = {
        "stocks", "prices", "fundamentals", "analyst", "institutional",
        "margin", "dividends", "holders", "news", "predictions", "meta",
    }
    assert expected <= names
    assert set(store.TABLES) == expected
    assert len(store.TABLES) == 11


def test_init_schema_is_idempotent(conn):
    store.init_schema(conn)
    store.init_schema(conn)
    assert conn.execute("SELECT COUNT(*) c FROM stocks").fetchone()["c"] == 0


def test_prices_has_adjclose_and_composite_key(conn):
    """spec 2.3：報酬一律用 adjclose，故必須存。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(prices)")}
    assert {"symbol", "date", "open", "high", "low", "close", "adjclose", "volume"} <= cols

    conn.execute(
        "INSERT INTO prices (symbol,date,open,high,low,close,adjclose,volume)"
        " VALUES ('2330.TW','2026-06-11',2250,2260,2240,2250,2250,30000)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prices (symbol,date,open,high,low,close,adjclose,volume)"
            " VALUES ('2330.TW','2026-06-11',1,1,1,1,1,1)"
        )


def test_rows_are_dict_like(conn):
    conn.execute("INSERT INTO meta (key,value) VALUES ('k','v')")
    row = conn.execute("SELECT * FROM meta").fetchone()
    assert row["key"] == "k" and row["value"] == "v"


def test_connect_creates_parent_directory(tmp_path):
    target = tmp_path / "nested" / "deep" / "cache.db"
    c = store.connect(target)
    store.init_schema(c)
    c.close()
    assert target.exists()
