"""Google News RSS 擷取。

去重以 link 的 SHA1 為主鍵（spec 4），因此同一則新聞重複抓取不會產生重複列。
情緒分數在此階段留空，由第二階段的 models/sentiment.py 回填。
"""
from __future__ import annotations

import hashlib
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from lxml import etree

from src.http import fetch_text

BASE = "https://news.google.com/rss/search"

_LOCALE = {
    "TW": {"hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"},
    "US": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
}


def build_query_url(name: str, market: str) -> str:
    locale = _LOCALE.get(market.upper(), _LOCALE["TW"])
    return f"{BASE}?{urlencode({'q': name, **locale})}"


def _iso(pub_date: str | None) -> str | None:
    if not pub_date:
        return None
    try:
        return parsedate_to_datetime(pub_date).isoformat()
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str, symbol: str) -> list[dict]:
    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except etree.XMLSyntaxError:
        return []

    out = []
    for item in root.iterfind(".//item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        out.append(
            {
                "link_hash": hashlib.sha1(link.encode("utf-8")).hexdigest(),
                "symbol": symbol,
                "published_at": _iso(item.findtext("pubDate")),
                "title": (item.findtext("title") or "").strip(),
                "link": link,
                "source": (item.findtext("source") or "").strip() or None,
                "sentiment": None,
            }
        )
    return out


def fetch_news(session, symbol: str, name: str, market: str, limiter=None) -> list[dict]:
    xml_text = fetch_text(session, build_query_url(name, market), limiter=limiter)
    return parse_rss(xml_text, symbol)
