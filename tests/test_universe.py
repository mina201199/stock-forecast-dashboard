from pathlib import Path

from src.fetch import universe

FIXTURES = Path(__file__).parent / "fixtures"


def test_excludes_non_common_stock_codes():
    """ETF（0050、00400A）、權證、特別股不進選股池 —— 它們沒有基本面可估值。"""
    assert universe.is_common_stock("2330") is True
    assert universe.is_common_stock("6488") is True
    assert universe.is_common_stock("0050") is False    # ETF
    assert universe.is_common_stock("00400A") is False  # 主動式 ETF
    assert universe.is_common_stock("03001P") is False  # 權證
    assert universe.is_common_stock("2887B") is False   # 特別股
    assert universe.is_common_stock("910322") is False  # TDR
    assert universe.is_common_stock("") is False


def test_select_taiwan_ranks_by_trade_value_and_tags_market():
    twse_rows = [
        {"code": "2330", "name": "台積電", "trade_value": 74_000_000_000.0},
        {"code": "1101", "name": "台泥", "trade_value": 400_000_000.0},
        {"code": "0050", "name": "元大台灣50", "trade_value": 99_000_000_000.0},
    ]
    tpex_rows = [{"code": "6488", "name": "環球晶", "trade_value": 1_980_000_000.0}]

    picked = universe.select_taiwan(twse_rows, tpex_rows, limit=2)
    assert [p["symbol"] for p in picked] == ["2330.TW", "6488.TWO"]
    assert all(p["market"] == "TW" for p in picked)


def test_select_taiwan_puts_otc_symbols_in_two_suffix():
    rows = [{"code": "6488", "name": "X", "trade_value": 1.0}]
    picked = universe.select_taiwan([], rows, limit=5)
    assert picked[0]["symbol"] == "6488.TWO"


def test_select_taiwan_respects_limit():
    rows = [{"code": f"1{i:03d}", "name": str(i), "trade_value": float(i)} for i in range(1, 20)]
    assert len(universe.select_taiwan(rows, [], limit=5)) == 5


def test_select_taiwan_treats_missing_trade_value_as_zero():
    rows = [{"code": "2330", "name": "a", "trade_value": None},
            {"code": "2317", "name": "b", "trade_value": 5.0}]
    assert [p["code"] for p in universe.select_taiwan(rows, [], limit=2)] == ["2317", "2330"]


def test_parse_sp500_extracts_symbols_and_normalizes_dots():
    html = (FIXTURES / "sp500_wiki.html").read_text(encoding="utf-8")
    rows = universe.parse_sp500(html)
    codes = [r["code"] for r in rows]
    assert codes[:2] == ["AAPL", "MSFT"]
    assert "BRK-B" in codes  # Yahoo 用 - 而非 .
    assert rows[0]["sector"] == "Information Technology"


def test_select_us_applies_limit_and_symbol():
    rows = [{"code": "AAPL", "name": "Apple", "sector": "IT"},
            {"code": "MSFT", "name": "Microsoft", "sector": "IT"}]
    picked = universe.select_us(rows, limit=1)
    assert picked == [{"symbol": "AAPL", "code": "AAPL", "market": "US",
                       "name": "Apple", "sector": "IT"}]
