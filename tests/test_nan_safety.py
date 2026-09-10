"""NaN 傳播防護。

實跑時發現的嚴重 bug：SQLite 的 NULL 經 pandas 變成 NaN，而
  - `not nan` 是 False、`nan <= 0` 是 False  -> 數值守衛抓不到
  - `all([nan, nan])` 是 True                -> NaN 軌道通過完整性檢查
  - `max(0.0, min(100.0, nan))` 回傳 100.0   -> NaN 變成滿分

三者串起來的結果是「完全沒有資料」被顯示成「信心度 100」。
"""
import math

import pytest

from src import compose as C
from src.models import valuation as V
from src.numeric import clamp, is_finite


def test_python_min_max_really_does_turn_nan_into_the_bound():
    """先證明這個陷阱是真的，否則下面的防護看起來像多餘的。"""
    assert max(0.0, min(100.0, float("nan"))) == 100.0


def test_all_of_nans_is_truthy():
    assert all([float("nan"), float("nan")]) is True


def test_is_finite_rejects_nan_and_inf_and_none():
    assert is_finite(1.0) is True
    assert is_finite(0.0) is True
    assert is_finite(None) is False
    assert is_finite(float("nan")) is False
    assert is_finite(float("inf")) is False
    assert is_finite(float("-inf")) is False


def test_clamp_returns_none_for_nan_instead_of_a_bound():
    assert clamp(float("nan"), 0.0, 100.0) is None
    assert clamp(float("inf"), 0.0, 100.0) is None
    assert clamp(None, 0.0, 100.0) is None
    assert clamp(150.0, 0.0, 100.0) == 100.0
    assert clamp(-5.0, 0.0, 100.0) == 0.0
    assert clamp(42.0, 0.0, 100.0) == 42.0


# --- 估值層 -----------------------------------------------------------


def test_pe_band_rejects_nan_pe():
    assert V.pe_band(100.0, float("nan"), [10.0] * 8) is None


def test_pb_band_rejects_nan_pb():
    assert V.pb_band(100.0, float("nan"), [1.0] * 8) is None


def test_yield_band_rejects_nan_yield():
    assert V.yield_band(100.0, float("nan"), [3.0] * 8) is None


def test_estimate_with_all_nan_fundamentals_returns_none():
    nan = float("nan")
    assert V.estimate(price=100.0, pe=nan, pb=nan, dividend_yield=nan,
                      industry="半導體業", peer_pes=[15.0] * 8,
                      peer_pbs=[2.0] * 8, peer_yields=[3.0] * 8) is None


def test_peer_bands_ignore_nan_entries():
    band = V._peer_bands([10.0, float("nan"), 20.0, 30.0, float("inf"), 40.0, 25.0])
    assert band is not None
    assert all(math.isfinite(v) for v in band)


# --- 合成層 -----------------------------------------------------------


def test_nan_valuation_track_is_not_admitted():
    nan = float("nan")
    out = C.blend(price=100.0, tech={"tech_q10": -0.05, "tech_q50": 0.02, "tech_q90": 0.08},
                  valuation={"low": nan, "fair": nan, "high": nan}, analyst=None,
                  sentiment=None, market="TW", bars=750, has_forward_eps=False)
    assert "valuation" not in out["tracks"]
    assert math.isfinite(out["target_mid"])
    assert math.isfinite(out["upside_pct"])


def test_nan_tech_track_is_not_admitted():
    nan = float("nan")
    out = C.blend(price=100.0, tech={"tech_q10": nan, "tech_q50": nan, "tech_q90": nan},
                  valuation={"low": 95.0, "fair": 105.0, "high": 115.0}, analyst=None,
                  sentiment=None, market="TW", bars=750, has_forward_eps=False)
    assert "tech" not in out["tracks"]
    assert out["target_mid"] == pytest.approx(105.0)


def test_nan_analyst_target_is_not_admitted():
    nan = float("nan")
    out = C.blend(price=100.0, tech=None,
                  valuation={"low": 95.0, "fair": 105.0, "high": 115.0},
                  analyst={"target_mean": nan, "target_low": nan, "target_high": nan,
                           "n_analysts": 20},
                  sentiment=None, market="US", bars=750, has_forward_eps=True)
    assert "analyst" not in out["tracks"]


def test_nan_confidence_factor_never_becomes_full_marks():
    """這是原始 bug 的直接迴歸測試。"""
    nan = float("nan")
    out = C.confidence(
        tracks={"tech": {"fair": 100.0}, "valuation": {"fair": nan}},
        features={"a": 1}, tech={"tech_q10": -0.05, "tech_q50": 0.0, "tech_q90": 0.05},
        volatility=0.30, model_accuracy=29.7,
    )
    assert out["score"] < 80.0
    assert math.isfinite(out["score"])


def test_nan_sentiment_does_not_corrupt_target():
    """NaN 情緒不得汙染目標價，且必須標示為「未計算」而非「中性」。

    2026-09-10 之前這裡斷言 adjustment == 0.0。那個表示法把「沒有資料」
    呈現成一個有意義的值（情緒中性），與本檔案要防的 NaN 陷阱同類，
    所以改為 None + sentiment_available=False。
    """
    out = C.blend(price=100.0, tech={"tech_q10": -0.05, "tech_q50": 0.02, "tech_q90": 0.08},
                  valuation=None, analyst=None, sentiment=float("nan"),
                  market="TW", bars=750, has_forward_eps=False)
    assert math.isfinite(out["target_mid"])
    # 目標價未被微調：中位數就是技術面分位數乘現價
    assert out["target_mid"] == pytest.approx(102.0)
    assert out["sentiment_adjustment"] is None
    assert out["sentiment_available"] is False


def test_nan_volatility_does_not_produce_a_score():
    out = C.confidence(
        tracks={"tech": {"fair": 100.0}, "valuation": {"fair": 101.0}},
        features={"a": 1}, tech={"tech_q10": -0.05, "tech_q50": 0.0, "tech_q90": 0.05},
        volatility=float("nan"), model_accuracy=50.0,
    )
    assert out["factors"]["預測區間寬度"] is None
    assert math.isfinite(out["score"])


def test_agreement_score_ignores_nan_fairs():
    assert C._agreement_score(
        {"a": {"fair": 100.0}, "b": {"fair": float("nan")}}
    ) is None


# --- 讀取層 -----------------------------------------------------------


def test_read_layer_converts_pandas_nan_to_none():
    """從源頭斷掉 NaN 路徑：pandas 讀 SQLite NULL 會給 NaN。"""
    import pandas as pd

    from src.analysis import _nan_to_none

    frame = pd.DataFrame([
        {"symbol": "A", "pe": 21.4, "pb": None, "source": "twse"},
        {"symbol": "B", "pe": None, "pb": 1.2, "source": "yahoo"},
    ]).set_index("symbol")
    out = _nan_to_none(frame.to_dict("index"))

    assert out["A"]["pe"] == 21.4
    assert out["A"]["pb"] is None
    assert out["B"]["pe"] is None
    assert out["A"]["source"] == "twse"   # 字串不得被清成 None


def test_read_layer_keeps_zero_which_is_meaningful():
    import pandas as pd

    from src.analysis import _nan_to_none

    frame = pd.DataFrame([{"symbol": "A", "foreign_net": 0}]).set_index("symbol")
    assert _nan_to_none(frame.to_dict("index"))["A"]["foreign_net"] == 0
