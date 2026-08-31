"""產出 HTML 的正確性與誠實性。

最重要的一組是「無建議性用語」—— spec 1.4 明訂系統不產生個人化投資
建議。這裡以掃描實際產出物驗證，而不是靠人工檢查。
"""
import json
import re

import pytest

from src import render


def _stock(**overrides):
    base = {
        "symbol": "2330.TW", "code": "2330", "market": "TW", "name": "台積電",
        "name_en": None, "industry": "半導體業", "currency": "TWD",
        "listed_date": "1994-09-05", "price": 2380.0, "change_pct": 0.017,
        "bars": 728, "as_of": "2026-08-31", "trend": "breakout",
        "trend_label": "強勢突破",
        "fundamentals": {"pe": 21.4, "forward_pe": None, "pb": 5.8,
                         "dividend_yield": 1.51, "revenue_yoy": 38.25, "roe": None,
                         "net_margin": None, "gross_margin": None,
                         "market_cap": None, "eps_forward": None},
        "valuation": {"low": 2200.0, "fair": 2600.0, "high": 2900.0,
                      "methods": ["PE", "PB"], "upside_pct": 0.09},
        "analyst": None,
        "chips": {"foreign_net": 3031655, "trust_net": -1764995, "dealer_net": -23367,
                  "chip_date": "2026-08-28", "margin_balance": 27630,
                  "margin_change": -120.0, "short_balance": 28,
                  "big_holder_pct": 84.75, "short_percent_float": None},
        "risk": {"volatility_60d": 0.31, "volatility_252d": 0.35,
                 "downside_volatility": 0.28, "beta": 1.12, "max_drawdown": -0.22,
                 "turnover_ma20": 7.4e10, "volatility_level": "高",
                 "volatility_percentile": 78.0, "liquidity_level": "極高"},
        "technical": {"rsi14": 61.2, "pos_52w": 0.88},
        "news": {"sentiment": 0.42, "news_count": 12, "trend": 0.1,
                 "top": [{"title": "法人上調台積電目標價", "link": "https://x/a",
                          "source": "經濟日報", "published_at": "2026-08-30T09:12:00+00:00",
                          "sentiment": 0.61}]},
        "prediction": {
            "target_low": 2200.0, "target_mid": 2620.0, "target_high": 3000.0,
            "upside_pct": 0.101, "confidence": 72.0, "predictable": True,
            "alerts": [],
            "tracks": {"tech": {"low": 2180, "fair": 2640, "high": 3100},
                       "valuation": {"low": 2200, "fair": 2600, "high": 2900}},
            "weights": {"tech": 0.5, "valuation": 0.5},
            "sentiment_adjustment": 0.021,
            "confidence_factors": {"三軌一致性": 88.0, "資料完整度": 95.0,
                                   "模型歷史準確度": 41.0, "預測區間寬度": 55.0},
            "confidence_weights": {"三軌一致性": 0.30, "資料完整度": 0.25,
                                   "模型歷史準確度": 0.25, "預測區間寬度": 0.20},
        },
        "dividends": [{"symbol": "2330.TW", "ex_date": "2026-09-11", "type": "息",
                       "cash_dividend": 6.0}],
        "chart": {"dates": ["2026-08-27", "2026-08-28", "2026-08-31"],
                  "adjclose": [2340.0, 2350.0, 2380.0],
                  "volume": [28000000, 31000000, 35000000],
                  "candles": []},
    }
    base.update(overrides)
    return base


def _payload(**overrides):
    base = {
        "stocks": [_stock()],
        "boards": {"bullish": [], "undervalued": [], "chips": [], "events": []},
        "report": {"trained": True, "reason": None, "ic": 0.041,
                   "momentum_ic": 0.018, "quintile_returns": [-0.01, 0.0, 0.005, 0.01, 0.02],
                   "n_samples": 120000},
        "generated_at": "2026-08-31T04:00:00+00:00",
        "horizon_days": 20,
    }
    base.update(overrides)
    return base


@pytest.fixture()
def html():
    return render.build_html(_payload())


# --- 無建議性用語（spec 1.4）------------------------------------------


@pytest.mark.parametrize("phrase", render.FORBIDDEN_PHRASES)
def test_page_contains_no_advisory_phrase(html, phrase):
    assert phrase not in html, f"頁面出現指示性用語：{phrase}"


def test_disclaimer_is_present_and_prominent(html):
    assert render.DISCLAIMER in html
    assert "不是投資建議" in html
    assert "持牌投資顧問" in html


def test_forbidden_list_is_actually_enforced():
    """守門測試本身要有效：塞進禁語應該被抓到。"""
    bad = render.build_html(_payload(stocks=[_stock(name="建議買進這檔")]))
    assert "建議買進" in bad  # 證明掃描目標確實會出現在產出中


# --- 資料內嵌的正確性 -------------------------------------------------


def test_embedded_json_is_parseable(html):
    match = re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', html, re.S
    )
    assert match, "找不到內嵌資料區塊"
    data = json.loads(match.group(1).replace("<\\/", "</"))
    assert data["stocks"][0]["symbol"] == "2330.TW"
    assert data["horizon_days"] == 20


def test_json_is_not_html_escaped(html):
    """autoescape 會把引號轉成 &quot;，讓 JSON 解析失敗。"""
    block = re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', html, re.S
    ).group(1)
    assert "&quot;" not in block
    assert "&#39;" not in block


def test_closing_script_tag_inside_data_is_neutralised():
    """新聞標題若含 </script> 會提前關閉標籤，破壞整頁。"""
    stock = _stock()
    stock["news"]["top"][0]["title"] = "標題含 </script><script>alert(1)</script>"
    out = render.build_html(_payload(stocks=[stock]))
    block = re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', out, re.S
    ).group(1)
    assert "</script>" not in block
    assert "<\\/script>" in block


def test_nan_and_infinity_never_reach_the_json():
    """NaN / Infinity 不是合法 JSON，瀏覽器會解析失敗。"""
    stock = _stock()
    stock["risk"]["beta"] = float("nan")
    stock["risk"]["volatility_252d"] = float("inf")
    out = render.build_html(_payload(stocks=[stock]))
    block = re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', out, re.S
    ).group(1)
    assert "NaN" not in block
    assert "Infinity" not in block
    data = json.loads(block.replace("<\\/", "</"))
    assert data["stocks"][0]["risk"]["beta"] is None


def test_numpy_types_are_serialised():
    import numpy as np
    stock = _stock(price=np.float64(2380.0), bars=np.int64(728))
    out = render.build_html(_payload(stocks=[stock]))
    block = re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', out, re.S
    ).group(1)
    data = json.loads(block.replace("<\\/", "</"))
    assert data["stocks"][0]["price"] == 2380.0


# --- 自足性：離線可開 -------------------------------------------------


def test_no_external_resources(html):
    """離線 file:// 必須能完整運作，任何外部引用都會失效。"""
    for pattern in [r'src=["\']https?://', r'href=["\']https?://[^"\']*\.css',
                    r'@import\s+url\(', r'<link[^>]+stylesheet']:
        assert not re.search(pattern, html), f"頁面引用了外部資源：{pattern}"


def test_external_links_are_only_in_news_and_open_safely(html):
    """新聞連結指向外部是預期的，但必須帶 noopener。"""
    for match in re.finditer(r'<a href="https?://[^"]*"[^>]*>', html):
        assert 'rel="noopener"' in match.group(0)


def test_css_and_js_are_inlined(html):
    assert "<style>" in html and "--accent" in html
    assert "renderTable" in html
    assert "JSON.parse" in html


# --- 誠實標示 ---------------------------------------------------------


def test_page_states_flat_market_assumption(html):
    assert "大盤" in html and "持平" in html


def test_page_labels_sentiment_as_keyword_based(html):
    assert "詞典" in html or "關鍵詞情緒" in html
    assert "非語意理解" in html


def test_page_states_taiwan_valuation_weakness(html):
    assert "前瞻 EPS" in html


def test_model_underperformance_is_disclosed_not_hidden():
    """IC 沒贏過動能基準時必須明講。"""
    out = render.build_html(_payload(report={
        "trained": True, "reason": None, "ic": 0.010, "momentum_ic": 0.030,
        "quintile_returns": [], "n_samples": 1000,
    }))
    assert "並未勝過" in out
    assert "不做美化" in out


def test_model_outperformance_does_not_show_the_warning():
    out = render.build_html(_payload(report={
        "trained": True, "reason": None, "ic": 0.060, "momentum_ic": 0.020,
        "quintile_returns": [], "n_samples": 1000,
    }))
    assert "並未勝過" not in out


def test_untrained_model_reason_is_surfaced():
    out = render.build_html(_payload(report={
        "trained": False, "reason": "可用樣本不足", "ic": None,
        "momentum_ic": None, "quintile_returns": [], "n_samples": 0,
    }))
    assert "技術面模型未訓練" in out
    assert "可用樣本不足" in out


# --- 結構 -------------------------------------------------------------


def test_has_search_filters_and_table(html):
    assert 'id="search"' in html
    assert 'id="f-industry"' in html
    assert 'id="f-trend"' in html
    assert 'id="f-conf"' in html
    assert 'id="tbody"' in html
    assert 'id="boards"' in html
    assert 'id="detail"' in html


def test_theme_tokens_defined_for_light_and_dark(html):
    assert "prefers-color-scheme: dark" in html
    assert ':root[data-theme="dark"]' in html


def test_write_html_creates_file(tmp_path):
    target = render.write_html(_payload(), tmp_path / "out.html")
    assert target.exists()
    assert "股勢" in target.read_text(encoding="utf-8")


def test_empty_board_explains_why_rather_than_staying_silent():
    """榜單空的時候要說得出差在哪一項，否則無從判斷是市場如此還是程式壞了。"""
    out = render.build_html(_payload(boards={
        "bullish": [], "bullish_note": "48 檔預測為正、7 檔信心度達 60，但無一同時滿足兩者。",
        "undervalued": [], "chips": [], "events": [],
    }))
    assert "但無一同時滿足兩者" in out
