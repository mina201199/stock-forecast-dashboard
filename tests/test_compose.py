import pytest

from src import compose as C


def _tech(q10=-0.05, q50=0.05, q90=0.15):
    return {"tech_q10": q10, "tech_q50": q50, "tech_q90": q90}


def _blend(**overrides):
    kwargs = dict(
        price=100.0, tech=_tech(), valuation={"low": 95, "fair": 105, "high": 115},
        analyst=None, sentiment=None, market="TW", bars=750, has_forward_eps=False,
    )
    kwargs.update(overrides)
    return C.blend(**kwargs)


# --- 權重隨資料品質變動 -----------------------------------------------


def test_short_history_downweights_technical_track():
    full = C.resolve_weights(market="TW", bars=750, has_valuation=True,
                             has_forward_eps=False, n_analysts=0)
    short = C.resolve_weights(market="TW", bars=100, has_valuation=True,
                              has_forward_eps=False, n_analysts=0)
    assert short["tech"] < full["tech"]


def test_taiwan_has_no_analyst_weight_and_it_is_redistributed():
    weights = C.resolve_weights(market="TW", bars=750, has_valuation=True,
                                has_forward_eps=False, n_analysts=0)
    assert weights["analyst"] == 0.0
    assert sum(weights.values()) == pytest.approx(1.0)


def test_us_with_many_analysts_keeps_full_analyst_weight():
    weights = C.resolve_weights(market="US", bars=750, has_valuation=True,
                                has_forward_eps=True, n_analysts=30)
    assert weights["analyst"] > 0.15


def test_few_analysts_are_downweighted():
    many = C.resolve_weights(market="US", bars=750, has_valuation=True,
                             has_forward_eps=True, n_analysts=30)
    few = C.resolve_weights(market="US", bars=750, has_valuation=True,
                            has_forward_eps=True, n_analysts=2)
    assert few["analyst"] < many["analyst"]


def test_missing_valuation_is_downweighted():
    with_val = C.resolve_weights(market="TW", bars=750, has_valuation=True,
                                 has_forward_eps=False, n_analysts=0)
    without = C.resolve_weights(market="TW", bars=750, has_valuation=False,
                                has_forward_eps=False, n_analysts=0)
    assert without["valuation"] < with_val["valuation"]


def test_weights_always_normalize_to_one():
    for bars in (50, 300, 750):
        for analysts in (0, 3, 30):
            for has_val in (True, False):
                w = C.resolve_weights(market="US", bars=bars, has_valuation=has_val,
                                      has_forward_eps=True, n_analysts=analysts)
                assert sum(w.values()) == pytest.approx(1.0)


# --- 目標價合成 -------------------------------------------------------


def test_blend_orders_low_mid_high():
    out = _blend()
    assert out["target_low"] <= out["target_mid"] <= out["target_high"]


def test_blend_upside_is_relative_to_current_price():
    out = _blend(tech=_tech(0.0, 0.20, 0.40), valuation={"low": 118, "fair": 120, "high": 122})
    assert out["upside_pct"] == pytest.approx(out["target_mid"] / 100.0 - 1)
    assert out["upside_pct"] > 0.15


def test_blend_returns_none_when_no_track_available():
    assert _blend(tech=None, valuation=None, analyst=None) is None


def test_blend_works_with_only_one_track():
    out = _blend(tech=None, valuation={"low": 90, "fair": 100, "high": 110})
    assert out["target_mid"] == pytest.approx(100.0)
    assert list(out["tracks"]) == ["valuation"]


def test_technical_track_assumes_flat_market():
    """超額報酬換價格時大盤預期取 0；此前提須可從結果反推驗證。"""
    out = _blend(valuation=None, analyst=None, tech=_tech(0.0, 0.10, 0.20))
    assert out["tracks"]["tech"]["fair"] == pytest.approx(110.0)


def test_analyst_track_used_when_present():
    out = _blend(market="US", has_forward_eps=True,
                 analyst={"target_low": 130, "target_mean": 140,
                          "target_high": 150, "n_analysts": 25})
    assert "analyst" in out["tracks"]
    assert out["tracks"]["analyst"]["fair"] == 140.0


# --- 新聞情緒只做封頂微調 ---------------------------------------------


def test_sentiment_adjustment_is_capped_at_five_percent():
    """一則極端利多不得把目標價拉飛。"""
    neutral = _blend(sentiment=None)["target_mid"]
    extreme = _blend(sentiment=1.0)["target_mid"]
    assert extreme / neutral - 1 == pytest.approx(C.SENTIMENT_CAP, abs=1e-9)


def test_extreme_negative_sentiment_is_also_capped():
    neutral = _blend(sentiment=None)["target_mid"]
    extreme = _blend(sentiment=-1.0)["target_mid"]
    assert 1 - extreme / neutral == pytest.approx(C.SENTIMENT_CAP, abs=1e-9)


def test_sentiment_never_creates_a_target_on_its_own():
    """沒有任何價格軌道時，情緒再正也不得產出目標價。"""
    assert _blend(tech=None, valuation=None, analyst=None, sentiment=1.0) is None


# --- 信心度 -----------------------------------------------------------


def _conf(tracks, **overrides):
    kwargs = dict(tracks=tracks, features={"a": 1, "b": 2}, tech=_tech(),
                  volatility=0.30, model_accuracy=60.0)
    kwargs.update(overrides)
    return C.confidence(**kwargs)


def test_agreeing_tracks_score_higher_than_conflicting():
    agree = _conf({"tech": {"fair": 100.0}, "valuation": {"fair": 101.0}})
    conflict = _conf({"tech": {"fair": 130.0}, "valuation": {"fair": 70.0}})
    assert agree["score"] > conflict["score"]
    assert agree["factors"]["三軌一致性"] > conflict["factors"]["三軌一致性"]


def test_confidence_exposes_all_four_factors():
    out = _conf({"tech": {"fair": 100.0}, "valuation": {"fair": 102.0}})
    assert set(out["factors"]) == {
        "三軌一致性", "資料完整度", "模型歷史準確度", "預測區間寬度",
    }


def test_agreement_is_the_heaviest_factor():
    out = _conf({"tech": {"fair": 100.0}, "valuation": {"fair": 102.0}})
    assert out["weights"]["三軌一致性"] == max(out["weights"].values())


def test_missing_data_lowers_completeness_factor():
    full = _conf({"tech": {"fair": 100.0}}, features={"a": 1, "b": 2, "c": 3})
    sparse = _conf({"tech": {"fair": 100.0}}, features={"a": 1, "b": None, "c": None})
    assert full["factors"]["資料完整度"] > sparse["factors"]["資料完整度"]


def test_wider_prediction_interval_lowers_confidence():
    narrow = _conf({"tech": {"fair": 100.0}}, tech=_tech(-0.01, 0.0, 0.01))
    wide = _conf({"tech": {"fair": 100.0}}, tech=_tech(-0.40, 0.0, 0.40))
    assert narrow["factors"]["預測區間寬度"] > wide["factors"]["預測區間寬度"]


def test_single_track_has_no_agreement_score():
    out = _conf({"tech": {"fair": 100.0}})
    assert out["factors"]["三軌一致性"] is None
    assert out["score"] > 0  # 其他因子仍可計分


def test_confidence_stays_within_bounds():
    out = _conf({"tech": {"fair": 100.0}, "valuation": {"fair": 100.0}},
                model_accuracy=1000.0)
    assert 0.0 <= out["score"] <= 100.0


# --- 趨勢分級 ---------------------------------------------------------


def test_breakout_requires_stack_volume_and_high_position():
    assert C.classify_trend({
        "bullish_stack": 1.0, "volume_ratio": 2.0, "pos_52w": 0.9, "bias_ma20": 0.08,
    }) == "breakout"


def test_stack_without_volume_surge_is_uptrend_not_breakout():
    assert C.classify_trend({
        "bullish_stack": 1.0, "volume_ratio": 1.0, "pos_52w": 0.65, "bias_ma20": 0.08,
    }) == "uptrend"


def test_tight_range_around_ma20_is_range():
    assert C.classify_trend({
        "bullish_stack": 0.0, "volume_ratio": 1.0, "pos_52w": 0.5, "bias_ma20": 0.01,
    }) == "range"


def test_below_ma20_with_short_term_weakness_is_weakening():
    assert C.classify_trend({
        "bullish_stack": 0.0, "volume_ratio": 1.2, "pos_52w": 0.4,
        "bias_ma20": -0.10, "ma5_over_ma20": -0.05,
    }) == "weakening"


def test_bottom_of_range_is_basing():
    assert C.classify_trend({
        "bullish_stack": 0.0, "volume_ratio": 0.7, "pos_52w": 0.1,
        "bias_ma20": -0.20, "ma5_over_ma20": 0.02,
    }) == "basing"


def test_every_label_has_chinese_text():
    for key in ("breakout", "uptrend", "range", "weakening", "basing"):
        assert key in C.TREND_LABELS and C.TREND_LABELS[key]


def test_classify_never_crashes_on_missing_features():
    assert C.classify_trend({}) in C.TREND_LABELS


# --- 出貨前檢查 -------------------------------------------------------


def test_insufficient_history_blocks_prediction():
    """spec 7.5：可用日線不足 120 根不預測。"""
    out = C.sanity_check({"upside_pct": 0.1, "confidence": 80.0}, price=100.0, bars=60)
    assert out["predictable"] is False
    assert out["confidence"] == 0.0
    assert any("不足" in a for a in out["alerts"])


def test_extreme_deviation_raises_alert_and_caps_confidence():
    out = C.sanity_check(
        {"upside_pct": 1.2, "confidence": 90.0, "tracks": {}}, price=100.0, bars=750
    )
    assert any("偏離現價" in a for a in out["alerts"])
    assert out["confidence"] <= 50.0


def test_conflicting_track_directions_cap_confidence():
    out = C.sanity_check(
        {"upside_pct": 0.02, "confidence": 95.0,
         "tracks": {"tech": {"fair": 130.0}, "valuation": {"fair": 70.0}}},
        price=100.0, bars=750,
    )
    assert any("分歧" in a for a in out["alerts"])
    assert out["confidence"] <= C.CONFLICT_CONFIDENCE_CAP


def test_low_confidence_is_not_predictable():
    out = C.sanity_check({"upside_pct": 0.05, "confidence": 30.0, "tracks": {}},
                         price=100.0, bars=750)
    assert out["predictable"] is False


def test_clean_case_passes_without_alerts():
    # annual_vol 是必要輸入：沒有波動度就量不出幅度合理性，而「未知」
    # 一律不通過（忘記傳參數要 fail closed，不能靜默跳過閘門）。
    out = C.sanity_check(
        {"upside_pct": 0.08, "confidence": 75.0,
         "tracks": {"tech": {"fair": 108.0}, "valuation": {"fair": 106.0}}},
        price=100.0, bars=750, annual_vol=0.30,
    )
    assert out["predictable"] is True
    assert out["alerts"] == []


# --- 因子涵蓋率折價 ---------------------------------------------------


def test_single_available_factor_cannot_reach_full_confidence():
    """只有「資料完整度」可算時不得給 100 分。

    沒有反證不等於有正面證據 —— 三軌一致性、模型準確度、區間寬度
    都無法計算，代表我們對這檔的了解其實很薄。
    """
    out = C.confidence(
        tracks={"valuation": {"fair": 100.0}},   # 單軌 -> 一致性 None
        features={"a": 1, "b": 2},               # 完整度 100
        tech=None,                               # 區間寬度 None
        volatility=None,
        model_accuracy=None,                     # 模型準確度 None
    )
    assert out["factors"]["資料完整度"] == 100.0
    assert out["score"] < 70.0
    assert out["coverage"] < 0.3


def test_full_factor_coverage_is_not_discounted():
    out = C.confidence(
        tracks={"tech": {"fair": 100.0}, "valuation": {"fair": 100.0}},
        features={"a": 1, "b": 2}, tech=_tech(-0.01, 0.0, 0.01),
        volatility=0.30, model_accuracy=100.0,
    )
    assert out["coverage"] == pytest.approx(1.0)
    assert out["score"] > 90.0


def test_empty_features_score_zero_but_are_still_measurable():
    """features={} 時「資料完整度」是可計算的 0 分，不是無法計算 ——
    兩者意義不同：前者是「量到了，很差」，後者是「量不到」。
    """
    out = C.confidence(tracks={}, features={}, tech=None, volatility=None,
                       model_accuracy=None)
    assert out["score"] == 0.0
    assert out["factors"]["資料完整度"] == 0.0
    assert out["coverage"] == pytest.approx(0.25)


def test_coverage_is_zero_only_when_no_factor_is_measurable():
    out = C.confidence(tracks={}, features={"a": 1}, tech=None, volatility=None,
                       model_accuracy=None)
    # 完整度可算 -> 涵蓋率 > 0
    assert out["coverage"] > 0.0


def test_more_factors_beats_fewer_at_equal_quality():
    few = C.confidence(tracks={"valuation": {"fair": 100.0}}, features={"a": 1},
                       tech=None, volatility=None, model_accuracy=None)
    many = C.confidence(
        tracks={"tech": {"fair": 100.0}, "valuation": {"fair": 100.0}},
        features={"a": 1}, tech=_tech(-0.05, 0.0, 0.05), volatility=0.30,
        model_accuracy=100.0,
    )
    assert many["score"] > few["score"]


# --- 估值樣本層級折價 -------------------------------------------------


def test_market_scope_valuation_is_discounted():
    """估值只用「同市場全體」而非「同市場同產業」時較粗略，信心度須折價。"""
    tracks = {"tech": {"fair": 100.0}, "valuation": {"fair": 101.0}}
    kwargs = dict(tracks=tracks, features={"a": 1}, tech=_tech(),
                  volatility=0.30, model_accuracy=60.0)
    industry = C.confidence(peer_scope="industry", **kwargs)
    market = C.confidence(peer_scope="market", **kwargs)
    assert market["score"] < industry["score"]
    assert market["peer_scope"] == "market"


def test_scope_penalty_only_applies_when_valuation_is_a_track():
    """沒有用到估值軌道時，估值樣本層級不該影響信心度。"""
    tracks = {"tech": {"fair": 100.0}, "analyst": {"fair": 101.0}}
    kwargs = dict(tracks=tracks, features={"a": 1}, tech=_tech(),
                  volatility=0.30, model_accuracy=60.0)
    assert C.confidence(peer_scope="industry", **kwargs)["score"] == \
        C.confidence(peer_scope="market", **kwargs)["score"]


# --- 估值極端值排除 ---------------------------------------------------


def test_extreme_valuation_is_excluded_from_the_blend():
    """實測案例：金融股 AIG 的 PB 被拿去與資產管理商比較，算出四倍合理價。
    那不是資訊，不該加權進合成。
    """
    out = _blend(price=76.63, tech=_tech(0.0, 0.02, 0.05),
                 valuation={"low": 200.0, "fair": 305.77, "high": 400.0})
    assert "valuation" not in out["tracks"]
    assert out["excluded_tracks"]
    assert "已排除" in out["excluded_tracks"][0]


def test_reasonable_valuation_is_kept():
    out = _blend(price=100.0, valuation={"low": 95.0, "fair": 112.0, "high": 130.0})
    assert "valuation" in out["tracks"]
    assert out["excluded_tracks"] == []


def test_extreme_downside_valuation_is_also_excluded():
    out = _blend(price=100.0, valuation={"low": 1.0, "fair": 2.0, "high": 3.0})
    assert "valuation" not in out["tracks"]


def test_exclusion_reason_reaches_the_alerts():
    blended = _blend(price=76.63, tech=_tech(0.0, 0.02, 0.05),
                     valuation={"low": 200.0, "fair": 305.77, "high": 400.0})
    blended["confidence"] = 80.0
    out = C.sanity_check(blended, price=76.63, bars=750)
    assert any("已排除" in a for a in out["alerts"])


def test_blend_returns_none_when_only_track_was_an_outlier():
    """唯一的軌道被排除後就沒有依據可給目標價。"""
    assert _blend(price=100.0, tech=None, analyst=None,
                  valuation={"low": 300.0, "fair": 400.0, "high": 500.0}) is None


def test_outlier_threshold_is_symmetric_in_ratio_space():
    """用百分比偏離會不對稱：下檔最多 -100%，門檻 100% 永遠不觸發。"""
    # 2.5 倍 -> 排除
    assert "valuation" not in _blend(
        price=100.0, valuation={"low": 200.0, "fair": 250.0, "high": 300.0})["tracks"]
    # 1/2.5 倍 -> 也要排除
    assert "valuation" not in _blend(
        price=100.0, valuation={"low": 30.0, "fair": 40.0, "high": 50.0})["tracks"]
    # 1.8 倍與 1/1.8 倍 -> 都保留
    assert "valuation" in _blend(
        price=100.0, valuation={"low": 150.0, "fair": 180.0, "high": 200.0})["tracks"]
    assert "valuation" in _blend(
        price=100.0, valuation={"low": 50.0, "fair": 56.0, "high": 60.0})["tracks"]


# --- §4.1 分歧時不可預測（2026-09-10 修復）-----------------------------


def test_conflicting_track_directions_make_it_unpredictable():
    """三軌方向分歧 -> predictable=False，不是「壓到剛好可以顯示」。

    修復前：分歧把信心度壓到 CONFLICT_CONFIDENCE_CAP=40，而顯示門檻
    MIN_CONFIDENCE_TO_SHOW 也是 40 —— 兩個常數相等，所以「三軌吵架」
    和「剛好可以顯示」是同一個數字。實測 88 筆留存預測裡，預測漲幅
    前 12 名的信心度全部都是 40，而信心度 > 50 的沒有一筆漲幅超過
    +0.6%：upside_pct 排序其實是分歧偵測器，不是報酬預測器。

    方向分歧代表我們沒有觀點，硬給一個合成中位數是杜撰。
    """
    out = C.sanity_check(
        {"upside_pct": 0.37, "confidence": 95.0,
         "tracks": {"tech": {"fair": 137.0}, "valuation": {"fair": 70.0}}},
        price=100.0, bars=750,
    )
    assert any("分歧" in a for a in out["alerts"])
    assert out["predictable"] is False


def test_agreeing_tracks_stay_predictable():
    out = C.sanity_check(
        {"upside_pct": 0.06, "confidence": 75.0,
         "tracks": {"tech": {"fair": 108.0}, "valuation": {"fair": 104.0}}},
        price=100.0, bars=750, annual_vol=0.30,
    )
    assert out["predictable"] is True


def test_conflict_cap_is_no_longer_equal_to_the_display_floor():
    """兩個常數相等就是那個 bug 的成因。用測試把它們釘開。"""
    assert C.CONFLICT_CONFIDENCE_CAP != C.MIN_CONFIDENCE_TO_SHOW


# --- §4.4 情緒未計算 ≠ 情緒中性 ---------------------------------------


def test_missing_sentiment_is_reported_as_unavailable_not_neutral():
    """news.sentiment 全為 NULL 時，不得回報 adjustment=0.0 了事。

    0.0 讀起來是「情緒中性」，實際意思是「情緒從未計算」——
    與 spec 9.4 那個 NaN 變成滿分信心同一類錯誤：把「沒有資料」
    呈現成一個有意義的值。
    """
    out = C.blend(
        price=100.0,
        tech={"tech_q10": -0.05, "tech_q50": 0.02, "tech_q90": 0.09},
        valuation=None, analyst=None, sentiment=None,
        market="TW", bars=750, has_forward_eps=False,
    )
    assert out["sentiment_adjustment"] is None
    assert out["sentiment_available"] is False


def test_present_sentiment_is_applied_and_marked_available():
    out = C.blend(
        price=100.0,
        tech={"tech_q10": -0.05, "tech_q50": 0.02, "tech_q90": 0.09},
        valuation=None, analyst=None, sentiment=0.5,
        market="TW", bars=750, has_forward_eps=False,
    )
    assert out["sentiment_available"] is True
    assert out["sentiment_adjustment"] == pytest.approx(0.5 * C.SENTIMENT_CAP)


# --- §4.3 幅度合理性：預測不得超出該檔自身的波動區間 -------------------


def test_forecast_beyond_the_stock_own_volatility_band_is_unpredictable():
    """中位預測落在該檔自身 20 日波動區間之外 -> 不可預測。

    分歧閘門抓不到這一類：實測 ADBE 三軌方向**一致**地喊高，
    合成中位數 +39.5%、信心度 46，照樣出貨並被留存。一個月 +39.5%
    對 Adobe 不是預測，是假象 —— 它主張這檔會走得比自身波動度所能
    支撐的還遠。

    波動區間（realized vol，spec §4.3）就是那把尺：年化 30% 的個股，
    20 個交易日的合理範圍約 ±10.8%。
    """
    out = C.sanity_check(
        {"upside_pct": 0.395, "confidence": 46.0,
         "tracks": {"tech": {"fair": 139.5}, "valuation": {"fair": 140.0}}},
        price=100.0, bars=750, annual_vol=0.30,
    )
    assert any("波動" in a for a in out["alerts"])
    assert out["predictable"] is False


def test_forecast_inside_the_band_stays_predictable():
    out = C.sanity_check(
        {"upside_pct": 0.05, "confidence": 70.0,
         "tracks": {"tech": {"fair": 105.0}, "valuation": {"fair": 106.0}}},
        price=100.0, bars=750, annual_vol=0.30,
    )
    assert out["predictable"] is True


def test_high_volatility_stock_gets_a_wider_allowance():
    """區間依個股自身波動度而定，不是一個固定的百分比門檻。"""
    forecast = {"upside_pct": 0.25, "confidence": 70.0,
                "tracks": {"tech": {"fair": 125.0}, "valuation": {"fair": 126.0}}}
    calm = C.sanity_check(dict(forecast), price=100.0, bars=750, annual_vol=0.20)
    wild = C.sanity_check(dict(forecast), price=100.0, bars=750, annual_vol=1.20)
    assert calm["predictable"] is False
    assert wild["predictable"] is True


def test_missing_volatility_does_not_silently_pass_the_band_check():
    """沒有波動度就量不出區間。不得因此當成通過 —— 未知不是通過。"""
    out = C.sanity_check(
        {"upside_pct": 0.395, "confidence": 70.0,
         "tracks": {"tech": {"fair": 139.5}, "valuation": {"fair": 140.0}}},
        price=100.0, bars=750, annual_vol=None,
    )
    assert out["predictable"] is False
    assert any("波動" in a for a in out["alerts"])
