"""SQLite 快取層（spec 4）。

增量更新是本專案選擇分層架構的主因：改模型參數時不必重抓資料。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

import config

TABLES = (
    "stocks", "prices", "fundamentals", "analyst", "institutional",
    "margin", "dividends", "holders", "news", "predictions", "meta",
    "signal_stats",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    symbol TEXT PRIMARY KEY, market TEXT NOT NULL, code TEXT NOT NULL,
    name TEXT, name_en TEXT, sector TEXT, industry TEXT,
    currency TEXT, listed_date TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, adjclose REAL, volume INTEGER,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);
CREATE TABLE IF NOT EXISTS fundamentals (
    symbol TEXT NOT NULL, asof TEXT NOT NULL,
    pe REAL, forward_pe REAL, pb REAL, dividend_yield REAL, market_cap REAL,
    eps_ttm REAL, eps_forward REAL, revenue_yoy REAL, revenue_mom REAL,
    gross_margin REAL, op_margin REAL, net_margin REAL, roe REAL, roa REAL,
    debt_to_equity REAL, current_ratio REAL, free_cashflow REAL,
    source TEXT, PRIMARY KEY (symbol, asof)
);
CREATE TABLE IF NOT EXISTS analyst (
    symbol TEXT NOT NULL, asof TEXT NOT NULL,
    target_mean REAL, target_high REAL, target_low REAL,
    recommendation TEXT, n_analysts INTEGER, PRIMARY KEY (symbol, asof)
);
CREATE TABLE IF NOT EXISTS institutional (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    foreign_net INTEGER, trust_net INTEGER, dealer_net INTEGER,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS margin (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    margin_balance INTEGER, margin_balance_prev INTEGER,
    short_balance INTEGER, short_balance_prev INTEGER,
    margin_util REAL, PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS dividends (
    symbol TEXT NOT NULL, ex_date TEXT NOT NULL,
    type TEXT, cash_dividend REAL, stock_dividend REAL, pay_date TEXT,
    PRIMARY KEY (symbol, ex_date)
);
CREATE TABLE IF NOT EXISTS holders (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    big_holder_pct REAL, PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS news (
    link_hash TEXT PRIMARY KEY, symbol TEXT NOT NULL, published_at TEXT,
    title TEXT, link TEXT, source TEXT, sentiment REAL
);
CREATE INDEX IF NOT EXISTS idx_news_symbol ON news(symbol, published_at);
CREATE TABLE IF NOT EXISTS predictions (
    symbol TEXT NOT NULL, run_date TEXT NOT NULL,
    close REAL, target_low REAL, target_mid REAL, target_high REAL,
    upside_pct REAL, confidence REAL, trend TEXT,
    tech_q10 REAL, tech_q50 REAL, tech_q90 REAL,
    valuation_fair REAL, sentiment REAL,
    PRIMARY KEY (symbol, run_date)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS signal_stats (
    signal_key TEXT NOT NULL, market TEXT NOT NULL, horizon INTEGER NOT NULL,
    n_fires INTEGER, n_effective INTEGER,
    hit_rate REAL, baseline_hit_rate REAL, median_excess REAL,
    ci_low REAL, ci_high REAL, p_value REAL, p_adjusted REAL,
    first_half_excess REAL, second_half_excess REAL,
    first_half_median REAL, second_half_median REAL,
    verdict TEXT NOT NULL, computed_at TEXT,
    PRIMARY KEY (signal_key, market, horizon)
);
"""


def connect(path: Path | str = config.DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


_DATE_COLUMNS = {"date", "ex_date", "asof", "run_date", "published_at"}


def upsert(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    """以 INSERT OR REPLACE 寫入。table 需為 TABLES 白名單成員（避免 SQL 注入）。"""
    if table not in TABLES:
        raise ValueError(f"未知的表：{table}")
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ",".join("?" for _ in columns)
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(row[c] for c in columns) for row in rows])
    conn.commit()
    return len(rows)


def get_last_date(
    conn: sqlite3.Connection, table: str, symbol: str, column: str = "date"
) -> str | None:
    """增量更新用：回傳該 symbol 已有的最大日期。"""
    if table not in TABLES:
        raise ValueError(f"未知的表：{table}")
    if column not in _DATE_COLUMNS:
        raise ValueError(f"未知的日期欄位：{column}")
    row = conn.execute(
        f"SELECT MAX({column}) AS d FROM {table} WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row["d"] if row and row["d"] else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", (key, value))
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def read_prices(conn: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT date, open, high, low, close, adjclose, volume"
        " FROM prices WHERE symbol = ? ORDER BY date ASC",
        conn,
        params=(symbol,),
    )
