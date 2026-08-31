from src.parsing import parse_num


def test_parses_plain_numbers():
    assert parse_num("21.40") == 21.4
    assert parse_num("31234567", int) == 31234567


def test_strips_thousand_separators():
    assert parse_num("13,836,606", int) == 13836606


def test_blank_and_dash_become_none():
    """TWSE 以空字串表示無資料（虧損公司的 PE），MIS 以 '-' 表示無成交。"""
    for value in ["", "  ", "-", "--", None]:
        assert parse_num(value) is None


def test_unparseable_returns_none_not_raises():
    assert parse_num("N/A") is None
    assert parse_num("約 3.5") is None


def test_zero_is_preserved_not_confused_with_missing():
    assert parse_num("0") == 0.0
    assert parse_num("0.00") == 0.0
