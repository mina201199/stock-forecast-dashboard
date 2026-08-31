from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from src.fetch import news

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def xml_text():
    return (FIXTURES / "google_news.xml").read_text(encoding="utf-8")


def test_build_query_url_uses_traditional_chinese_for_tw():
    qs = parse_qs(urlparse(news.build_query_url("台積電", "TW")).query)
    assert qs["hl"] == ["zh-TW"]
    assert qs["ceid"] == ["TW:zh-Hant"]
    assert qs["q"] == ["台積電"]


def test_build_query_url_uses_english_for_us():
    qs = parse_qs(urlparse(news.build_query_url("Apple Inc", "US")).query)
    assert qs["hl"] == ["en-US"]
    assert qs["ceid"] == ["US:en"]


def test_parse_rss_extracts_items(xml_text):
    items = news.parse_rss(xml_text, "2330.TW")
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "法人上調台積電目標價至 2800 元"
    assert first["source"] == "經濟日報"
    assert first["symbol"] == "2330.TW"
    assert first["published_at"] == "2026-08-30T09:12:00+00:00"
    assert first["sentiment"] is None


def test_link_hash_is_stable_and_unique(xml_text):
    items = news.parse_rss(xml_text, "2330.TW")
    again = news.parse_rss(xml_text, "2330.TW")
    assert items[0]["link_hash"] == again[0]["link_hash"]
    assert items[0]["link_hash"] != items[1]["link_hash"]
    assert len(items[0]["link_hash"]) == 40  # SHA1 hex


def test_malformed_xml_returns_empty_list():
    assert news.parse_rss("<not-xml", "X") == []


def test_item_without_link_is_skipped():
    xml = ('<?xml version="1.0"?><rss><channel>'
           "<item><title>no link</title></item>"
           "<item><title>ok</title><link>https://a/b</link></item>"
           "</channel></rss>")
    assert len(news.parse_rss(xml, "X")) == 1
