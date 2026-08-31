import numpy as np
import pandas as pd
import pytest

from src.models import valuation as V


def _peers(values):
    return list(values)


# --- 方法選擇：spec 6.2 的核心規則 ------------------------------------


def test_negative_eps_switches_from_pe_to_pb():
    """spec 6.2：EPS <= 0 時 PE 法失效，必須改用 PB 法。"""
    weights = V.select_weights(eps_positive=False, financial=False, dividend_yield=None)
    assert weights["PE"] == 0.0
    assert weights["PB"] == pytest.approx(1.0)


def test_financial_industry_uses_pb_as_primary():
    weights = V.select_weights(eps_positive=True, financial=True, dividend_yield=None)
    assert weights["PB"] > weights["PE"]


def test_normal_stock_leads_with_pe():
    weights = V.select_weights(eps_positive=True, financial=False, dividend_yield=None)
    assert weights["PE"] > weights["PB"]
    assert weights["YIELD"] == 0.0


def test_high_yield_brings_yield_method_into_the_mix():
    weights = V.select_weights(eps_positive=True, financial=False, dividend_yield=5.5)
    assert weights["YIELD"] > 0
    assert sum(weights.values()) == pytest.approx(1.0)


def test_low_yield_does_not_trigger_yield_method():
    weights = V.select_weights(eps_positive=True, financial=False, dividend_yield=1.2)
    assert weights["YIELD"] == 0.0


def test_weights_always_sum_to_one():
    for eps in (True, False):
        for fin in (True, False):
            for dy in (None, 1.0, 6.0):
                w = V.select_weights(eps_positive=eps, financial=fin, dividend_yield=dy)
                assert sum(w.values()) == pytest.approx(1.0)


def test_financial_industry_detection():
    assert V.is_financial("金融保險業") is True
    assert V.is_financial("Financial Services") is True
    assert V.is_financial("半導體業") is False
    assert V.is_financial(None) is False


# --- 各估值法 ---------------------------------------------------------


def test_pe_band_prices_stock_at_industry_median():
    """該檔 PE 等於同業中位數時，合理價應等於現價。"""
    peers = _peers([10, 15, 20, 25, 30, 20, 20])
    median = float(np.quantile([p for p in peers], 0.5))
    out = V.pe_band(price=100.0, pe=median, peer_pes=peers)
    assert out["fair"] == pytest.approx(100.0)
    assert out["low"] < out["fair"] < out["high"]


def test_pe_band_marks_expensive_stock_below_current_price():
    """該檔 PE 遠高於同業時，合理價應低於現價。"""
    out = V.pe_band(price=100.0, pe=50.0, peer_pes=_peers([10, 12, 14, 16, 18, 20]))
    assert out["fair"] < 100.0


def test_pe_band_none_for_nonpositive_pe():
    assert V.pe_band(100.0, None, _peers([10, 12, 14, 16, 18])) is None
    assert V.pe_band(100.0, -5.0, _peers([10, 12, 14, 16, 18])) is None
    assert V.pe_band(100.0, 0.0, _peers([10, 12, 14, 16, 18])) is None


def test_bands_none_when_too_few_peers():
    """同業樣本太少時分位數沒有意義。"""
    assert V.pe_band(100.0, 15.0, _peers([12, 14])) is None
    assert V.pb_band(100.0, 1.5, _peers([1.2, 1.4])) is None


def test_pb_band_works_when_pe_is_unavailable():
    out = V.pb_band(price=50.0, pb=1.0, peer_pbs=_peers([0.8, 1.0, 1.2, 1.5, 2.0, 1.0]))
    assert out is not None and out["method"] == "PB"
    assert out["low"] < out["high"]


def test_yield_band_inverts_direction():
    """殖利率與價格反向：高殖利率百分位對應低價。"""
    out = V.yield_band(price=100.0, dividend_yield=5.0,
                       peer_yields=_peers([2.0, 3.0, 4.0, 5.0, 6.0, 7.0]))
    assert out["low"] < out["fair"] < out["high"]


def test_yield_band_fair_matches_manual_computation():
    peers = _peers([4.0, 4.0, 4.0, 4.0, 4.0, 4.0])
    out = V.yield_band(price=100.0, dividend_yield=5.0, peer_yields=peers)
    # 股利 = 100 * 5% = 5 元；同業中位殖利率 4% -> 合理價 = 5 / 4% = 125
    assert out["fair"] == pytest.approx(125.0)


# --- 合成 -------------------------------------------------------------


def test_combine_falls_back_when_preferred_method_unavailable():
    """偏好 PE 但只有 PB 算得出來時，不得回 None。"""
    estimates = [None, {"method": "PB", "low": 8, "fair": 10, "high": 12}, None]
    out = V.combine(estimates, {"PE": 1.0, "PB": 0.0, "YIELD": 0.0})
    assert out["fair"] == pytest.approx(10.0)
    assert out["methods"] == ["PB"]


def test_combine_returns_none_when_nothing_computable():
    assert V.combine([None, None, None], {"PE": 1.0}) is None


def test_combine_renormalizes_over_available_methods():
    estimates = [{"method": "PE", "low": 8, "fair": 10, "high": 12},
                 {"method": "PB", "low": 18, "fair": 20, "high": 22}]
    out = V.combine(estimates, {"PE": 0.7, "PB": 0.3, "YIELD": 0.0})
    assert out["fair"] == pytest.approx(0.7 * 10 + 0.3 * 20)


# --- 端到端 -----------------------------------------------------------


def test_estimate_returns_upside_relative_to_price():
    out = V.estimate(
        price=100.0, pe=10.0, pb=1.0, dividend_yield=1.0, industry="半導體業",
        peer_pes=_peers([14.0, 17.0, 20.0, 23.0, 26.0, 20.0, 19.0, 21.0]),
        peer_pbs=_peers([1.4, 1.7, 2.0, 2.3, 2.6, 2.0, 1.9, 2.1]),
        peer_yields=_peers([0.7, 0.9, 1.0, 1.1, 1.3, 1.0, 1.0, 1.2]),
    )
    # PE 10 vs 同業 20 -> 便宜一半，合理價應約為現價兩倍
    assert out["upside_pct"] > 0.5
    assert out["fair"] > out["low"]


def test_estimate_none_for_invalid_price():
    assert V.estimate(price=0.0, pe=10.0, pb=1.0, dividend_yield=None, industry=None,
                      peer_pes=[], peer_pbs=[], peer_yields=[]) is None


def test_estimate_survives_all_missing_fundamentals():
    assert V.estimate(price=100.0, pe=None, pb=None, dividend_yield=None,
                      industry=None, peer_pes=[], peer_pbs=[], peer_yields=[]) is None


def test_peer_values_fall_back_to_whole_market_when_industry_too_small():
    rows = pd.DataFrame({
        "industry": ["小產業"] + ["大產業"] * 9,
        "pe": [11.0] + [20.0] * 9,
    })
    peers = V.industry_peer_values(rows, "小產業", "pe")
    assert len(peers) == 10  # 同業不足 MIN_PEERS，退回全市場


def test_peer_values_use_industry_when_large_enough():
    rows = pd.DataFrame({
        "industry": ["半導體業"] * 6 + ["金融業"] * 4,
        "pe": [15.0] * 6 + [9.0] * 4,
    })
    peers = V.industry_peer_values(rows, "半導體業", "pe")
    assert peers == [15.0] * 6


def test_identical_peers_collapse_band_to_a_point():
    """同業值毫無離散度時，區間寬度為 0 是誠實結果，不是 bug ——
    這代表同業之間沒有估值差異可供參考。實務資料不會如此。
    """
    out = V.pe_band(price=100.0, pe=20.0, peer_pes=_peers([20.0] * 8))
    assert out["low"] == out["fair"] == out["high"]
