from pathlib import Path

import pytest

from src.fetch import tdcc

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def csv_text():
    return (FIXTURES / "tdcc_sample.csv").read_text(encoding="utf-8-sig")


def test_fixture_actually_has_bom():
    """spec 9.3：TDCC 回傳帶 BOM，測資必須忠實重現。"""
    assert (FIXTURES / "tdcc_sample.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_plain_utf8_decode_would_corrupt_header():
    """證明為何一定要用 utf-8-sig：用 utf-8 解會讓第一個欄位名帶不可見字元。"""
    raw = (FIXTURES / "tdcc_sample.csv").read_bytes()
    first_header = raw.decode("utf-8").split(",")[0]
    assert first_header != "資料日期"          # 前面黏了 U+FEFF
    assert first_header.lstrip("\ufeff") == "資料日期"


def test_parse_keeps_only_level_15(csv_text):
    """持股分級 15 = 400 張以上，即大戶。"""
    rows = {r["code"]: r for r in tdcc.parse_big_holders(csv_text)}
    assert set(rows) == {"2330", "1101"}
    assert rows["2330"]["big_holder_pct"] == 71.34
    assert rows["1101"]["big_holder_pct"] == 42.10


def test_parse_converts_date(csv_text):
    assert tdcc.parse_big_holders(csv_text)[0]["date"] == "2026-08-28"


def test_fetch_requests_utf8_sig(monkeypatch, csv_text):
    seen = {}

    def fake(session, url, **kw):
        seen.update(kw)
        return csv_text

    monkeypatch.setattr(tdcc, "fetch_text", fake)
    rows = tdcc.fetch_big_holders(session=None)
    assert seen.get("encoding") == "utf-8-sig"
    assert len(rows) == 2


def test_malformed_rows_are_skipped():
    text = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n20260828,2330,15,28,1,\n"
    assert tdcc.parse_big_holders(text) == []
