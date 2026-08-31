from datetime import UTC, datetime, timedelta

import pytest

from src.models import sentiment as S

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _item(title, days_ago=0.0, **extra):
    return {
        "title": title,
        "published_at": (NOW - timedelta(days=days_ago)).isoformat(),
        **extra,
    }


# --- 方向性 -----------------------------------------------------------


def test_positive_chinese_headline_scores_positive():
    assert S.analyse_item("法人上調台積電目標價，獲利創高") > 0.4


def test_negative_chinese_headline_scores_negative():
    assert S.analyse_item("大客戶砍單，公司下修全年展望") < -0.4


def test_neutral_headline_scores_near_zero():
    assert S.analyse_item("公司將於下週召開法人說明會") == pytest.approx(0.0, abs=0.05)


def test_positive_english_headline_scores_positive():
    assert S.analyse_item("Apple beats estimates as revenue growth accelerates") > 0.3


def test_negative_english_headline_scores_negative():
    assert S.analyse_item("Shares plunge after company warned of weak demand") < -0.3


# --- 否定處理 ---------------------------------------------------------


def test_chinese_negation_flips_polarity():
    positive = S.analyse_item("獲利成長超乎預期")
    negated = S.analyse_item("獲利不如預期")
    assert positive > 0
    assert negated < 0


def test_chinese_negator_before_positive_word():
    assert S.analyse_item("擴產計畫暫停擴產") < S.analyse_item("宣布擴產")


def test_english_negation_flips_polarity():
    assert S.analyse_item("Company beats guidance") > 0
    assert S.analyse_item("Company fails to beat guidance") < 0


# --- 財務中性詞不得誤判（Loughran-McDonald 的重點）-------------------


@pytest.mark.parametrize("word", ["liability", "liabilities", "cost", "costs",
                                  "depreciation", "tax", "capital", "expense"])
def test_financially_neutral_english_words_are_not_negative(word):
    """通用情緒詞典會把這些字判為負面，LM 詞典不會。"""
    assert S.analyse_item(f"Company reports {word} details in filing") == pytest.approx(
        0.0, abs=0.05
    )


# --- 範圍與壓縮 -------------------------------------------------------


def test_score_stays_within_bounds_even_for_extreme_text():
    piled = "漲停 創新高 獲利創高 調升目標價 供不應求 產能滿載 訂單暢旺 " * 10
    assert -1.0 <= S.analyse_item(piled) <= 1.0
    assert S.analyse_item(piled) > 0.9


def test_title_weighs_more_than_summary():
    title_positive = S.analyse_item("獲利創高", "營收衰退")
    summary_positive = S.analyse_item("營收衰退", "獲利創高")
    assert title_positive > summary_positive


# --- 時間衰減與彙總 ---------------------------------------------------


def test_recent_news_outweighs_old_news():
    fresh_good = S.analyse_batch(
        [_item("獲利創高", 0.1), _item("獲利衰退", 6.5)], NOW
    )["sentiment"]
    stale_good = S.analyse_batch(
        [_item("獲利創高", 6.5), _item("獲利衰退", 0.1)], NOW
    )["sentiment"]
    assert fresh_good > 0
    assert stale_good < 0


def test_news_older_than_lookback_is_excluded():
    out = S.analyse_batch([_item("獲利創高", 30.0)], NOW)
    assert out["news_count"] == 0
    assert out["sentiment"] is None


def test_empty_input_returns_none_not_zero():
    """沒有新聞不等於情緒中性 —— 必須區分。"""
    out = S.analyse_batch([], NOW)
    assert out["sentiment"] is None
    assert out["news_count"] == 0
    assert out["top"] == []


def test_trend_positive_when_mood_improves():
    out = S.analyse_batch(
        [_item("砍單 下修", 6.0), _item("砍單 下修", 5.0),
         _item("獲利創高 調升目標價", 0.5), _item("訂單暢旺", 1.0)],
        NOW,
    )
    assert out["trend"] is not None and out["trend"] > 0


def test_trend_none_when_only_recent_news_exists():
    out = S.analyse_batch([_item("獲利創高", 0.2)], NOW)
    assert out["trend"] is None


def test_top_items_are_the_most_extreme_and_capped_at_three():
    items = [
        _item("公司召開法說會"),
        _item("獲利創高 調升目標價 供不應求"),
        _item("砍單 轉虧 認列虧損"),
        _item("營收成長"),
        _item("外資賣超"),
    ]
    out = S.analyse_batch(items, NOW)
    assert len(out["top"]) == 3
    magnitudes = [abs(t["sentiment"]) for t in out["top"]]
    assert magnitudes == sorted(magnitudes, reverse=True)
    assert "公司召開法說會" not in [t["title"] for t in out["top"]]


def test_top_items_carry_link_and_source_for_display():
    out = S.analyse_batch(
        [_item("獲利創高", 0.1, link="https://x/a", source="經濟日報")], NOW
    )
    assert out["top"][0]["link"] == "https://x/a"
    assert out["top"][0]["source"] == "經濟日報"


def test_malformed_timestamp_is_downweighted_not_crashing():
    out = S.analyse_batch([{"title": "獲利創高", "published_at": "not-a-date"}], NOW)
    assert out["news_count"] == 1
    assert out["sentiment"] > 0


def test_missing_timestamp_is_handled():
    out = S.analyse_batch([{"title": "砍單"}], NOW)
    assert out["news_count"] == 1
    assert out["sentiment"] < 0
