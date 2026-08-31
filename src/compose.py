"""合成層（spec 7）。

三軌各自給出對「該檔未來一個月」的看法，這裡負責合成、標示信心度、
分級趨勢，並在出貨前擋掉明顯的垃圾。

兩個刻意的設計選擇：

1. **權重隨資料品質變動，不是固定值。** 台股沒有分析師目標價、部分
   個股缺基本面、新上市的個股歷史不足 —— 用固定權重會讓資料最差的
   標的和資料最完整的標的看起來一樣可信。

2. **新聞情緒不直接產生目標價，只做 ±5% 以內的微調。** 情緒分數沒有
   價格尺度，硬換算就是杜撰。它能表達的是「市場氣氛偏正/偏負」，
   封頂避免一則利多把目標價拉飛。
"""
from __future__ import annotations

import math

import numpy as np

from src.numeric import all_finite, clamp, is_finite, is_positive

HORIZON_DAYS = 20

BASE_WEIGHTS = {"tech": 0.40, "valuation": 0.40, "analyst": 0.20}
MIN_BARS_FOR_FULL_TECH = 250
MIN_BARS_TO_PREDICT = 120
MIN_ANALYSTS_FOR_FULL_WEIGHT = 5
TW_NO_FORWARD_EPS_PENALTY = 0.8

# 估值軌道被視為極端值而排除的門檻，以「合理價 / 現價」的比值判斷。
# 用百分比偏離會不對稱：上檔可以無限大，下檔最多 -100%，
# 因此 |deviation| > 1.0 在下檔永遠不會觸發。比值在對數尺度上對稱。
#
# 同業相對估值在樣本小、商業模式異質時會產生無意義的數字 ——
# 實測金融股 AIG（保險）被拿去與 Apollo（資產管理）比 PB，
# 算出「合理價是現價四倍」。那不是資訊，是雜訊，不該加權進合成。
VALUATION_OUTLIER_RATIO = 2.0

SENTIMENT_CAP = 0.05
DEVIATION_ALERT = 0.50
CONFLICT_CONFIDENCE_CAP = 40.0
# 可計算因子涵蓋率不足時的折價下限：涵蓋率 0 時保留 50%
COVERAGE_FLOOR = 0.5
MIN_CONFIDENCE_TO_SHOW = 40.0

TREND_LABELS = {
    "breakout": "強勢突破",
    "uptrend": "穩步上升",
    "range": "區間整理",
    "weakening": "轉弱",
    "basing": "弱勢築底",
}


# --- 目標價 -----------------------------------------------------------


def _tech_prices(price: float, tech: dict | None) -> dict | None:
    """把超額報酬換算成價格。

    大盤預期報酬取 0（假設大盤持平）—— 大盤是預測不到的，這個前提
    必須顯示在頁面上，不能藏起來。
    """
    if not tech or not is_positive(price):
        return None
    q10, q50, q90 = tech.get("tech_q10"), tech.get("tech_q50"), tech.get("tech_q90")
    if not all_finite((q10, q50, q90)):
        return None
    return {"low": price * (1 + q10), "fair": price * (1 + q50), "high": price * (1 + q90)}


def resolve_weights(
    *, market: str, bars: int, has_valuation: bool, has_forward_eps: bool,
    n_analysts: int | None,
) -> dict[str, float]:
    weights = dict(BASE_WEIGHTS)

    if bars < MIN_BARS_FOR_FULL_TECH:
        weights["tech"] = 0.15

    if not has_valuation:
        weights["valuation"] = 0.15
    elif market == "TW" and not has_forward_eps:
        # 台股沒有免費的分析師前瞻 EPS，估值本質上較弱（spec 2.8）
        weights["valuation"] *= TW_NO_FORWARD_EPS_PENALTY

    analysts = n_analysts or 0
    if analysts == 0:
        weights["analyst"] = 0.0
    elif analysts < MIN_ANALYSTS_FOR_FULL_WEIGHT:
        weights["analyst"] *= 0.5

    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()} if total else weights


def blend(
    *, price: float, tech: dict | None, valuation: dict | None, analyst: dict | None,
    sentiment: float | None, market: str, bars: int, has_forward_eps: bool,
) -> dict | None:
    """合成目標價區間。"""
    tracks: dict[str, dict] = {}

    tech_prices = _tech_prices(price, tech)
    if tech_prices:
        tracks["tech"] = tech_prices
    excluded: list[str] = []
    if valuation and all_finite(valuation.get(k) for k in ("low", "fair", "high")):
        ratio = float(valuation["fair"]) / price
        if ratio > VALUATION_OUTLIER_RATIO or ratio < 1 / VALUATION_OUTLIER_RATIO:
            excluded.append(
                f"基本面估值算出的合理價是現價的 {ratio:.1f} 倍（{ratio - 1:+.0%}），"
                "超出合理範圍，已排除不納入合成"
                "（同業樣本過小或商業模式差異過大時會出現此情形）"
            )
        else:
            tracks["valuation"] = {k: float(valuation[k]) for k in ("low", "fair", "high")}
    if analyst and is_finite(analyst.get("target_mean")):
        mean = float(analyst["target_mean"])
        low, high = analyst.get("target_low"), analyst.get("target_high")
        tracks["analyst"] = {
            "low": float(low) if is_finite(low) else mean,
            "fair": mean,
            "high": float(high) if is_finite(high) else mean,
        }

    if not tracks:
        return None

    weights = resolve_weights(
        market=market, bars=bars, has_valuation="valuation" in tracks,
        has_forward_eps=has_forward_eps,
        n_analysts=(analyst or {}).get("n_analysts"),
    )
    active = {name: w for name, w in weights.items() if name in tracks and w > 0}
    if not active:
        active = {name: 1.0 for name in tracks}
    total = sum(active.values())

    blended = {
        key: sum(tracks[name][key] * w for name, w in active.items()) / total
        for key in ("low", "fair", "high")
    }

    # 新聞情緒只做封頂微調，不主導
    adjustment = 0.0
    if is_finite(sentiment):
        bounded = clamp(float(sentiment) * SENTIMENT_CAP, -SENTIMENT_CAP, SENTIMENT_CAP)
        adjustment = 0.0 if bounded is None else bounded
        blended = {k: v * (1 + adjustment) for k, v in blended.items()}

    low, fair, high = sorted(blended.values())
    return {
        "target_low": low,
        "target_mid": fair,
        "target_high": high,
        "upside_pct": fair / price - 1,
        "tracks": {name: tracks[name] for name in tracks},
        "weights": {name: round(w / total, 4) for name, w in active.items()},
        "sentiment_adjustment": adjustment,
        "excluded_tracks": excluded,
    }


# --- 信心度 -----------------------------------------------------------


def _agreement_score(tracks: dict[str, dict]) -> float | None:
    """三軌一致性。以合理價的變異係數衡量，離散越小分數越高。

    這是信心度中權重最高的因子。理由：三軌分歧時取平均會得到一個
    看起來合理、實際上毫無意義的數字。正確做法是明確顯示分歧並
    壓低信心，而不是把矛盾平均掉。
    """
    fairs = [float(t["fair"]) for t in tracks.values() if is_finite(t.get("fair"))]
    if len(fairs) < 2:
        return None
    mean = float(np.mean(fairs))
    if not is_positive(mean):
        return None
    cv = float(np.std(fairs) / mean)
    return float(100.0 * math.exp(-6.0 * cv))


def _completeness_score(features: dict) -> float:
    total = len(features)
    if total == 0:
        return 0.0
    present = sum(1 for v in features.values() if is_finite(v))
    return 100.0 * present / total


def _interval_score(tech: dict | None, volatility: float | None) -> float | None:
    """預測區間寬度相對該檔波動度正規化。窄則信心高。"""
    if not tech or not is_positive(volatility):
        return None
    q10, q90 = tech.get("tech_q10"), tech.get("tech_q90")
    if not all_finite((q10, q90)):
        return None
    horizon_vol = float(volatility) * math.sqrt(HORIZON_DAYS / 252.0)
    ratio = (q90 - q10) / (2 * horizon_vol) if horizon_vol else None
    if ratio is None:
        return None
    return float(100.0 * math.exp(-max(ratio, 0.0)))


PEER_SCOPE_PENALTY = {"industry": 1.0, "market": 0.75, None: 0.5}


def confidence(
    *, tracks: dict[str, dict], features: dict, tech: dict | None,
    volatility: float | None, model_accuracy: float | None,
    peer_scope: str | None = "industry",
) -> dict:
    """0-100 信心度與四因子拆解。

    頁面必須把四個因子攤開顯示 —— 只給總分等於要求使用者盲信。
    """
    factors = {
        "三軌一致性": (_agreement_score(tracks), 0.30),
        "資料完整度": (_completeness_score(features), 0.25),
        "模型歷史準確度": (model_accuracy, 0.25),
        "預測區間寬度": (_interval_score(tech, volatility), 0.20),
    }
    # is_finite 而非 `is not None`：NaN 通得過 None 檢查，
    # 之後在夾取階段會被 min/max 變成 100（見 src/numeric.py）
    usable = {k: (float(v), w) for k, (v, w) in factors.items() if is_finite(v)}
    if not usable:
        return {"score": 0.0, "factors": {k: None for k in factors},
                "coverage": 0.0, "weights": {k: w for k, (_, w) in factors.items()}}

    total_weight = sum(w for _, w in usable.values())
    raw = sum(v * w for v, w in usable.values()) / total_weight

    # 只有少數因子可算時必須折價。否則「唯一能算的因子恰好滿分」就會
    # 產生 100 分的信心度 —— 那是把「沒有反證」誤當成「有正面證據」。
    coverage = total_weight / sum(w for _, w in factors.values())
    score = raw * (COVERAGE_FLOOR + (1 - COVERAGE_FLOOR) * coverage)

    # 估值來自「同市場全體」而非「同市場同產業」時較粗略，須折價
    if "valuation" in tracks:
        score *= PEER_SCOPE_PENALTY.get(peer_scope, 0.5)

    clamped = clamp(score, 0.0, 100.0)
    return {
        "score": 0.0 if clamped is None else clamped,
        "coverage": round(coverage, 3),
        "peer_scope": peer_scope,
        "factors": {
            k: (round(float(factors[k][0]), 1) if is_finite(factors[k][0]) else None)
            for k in factors
        },
        "weights": {k: w for k, (_, w) in factors.items()},
    }


# --- 趨勢分級 ---------------------------------------------------------


def classify_trend(f: dict) -> str:
    """五分類（spec 7.3）。條件依序判斷，先命中者勝。"""
    pos = f.get("pos_52w")
    bull = f.get("bullish_stack")
    bias = f.get("bias_ma20")
    vol_ratio = f.get("volume_ratio")
    ma5_20 = f.get("ma5_over_ma20")
    rsi = f.get("rsi14")

    if bull == 1.0 and (vol_ratio or 0) > 1.5 and (pos or 0) > 0.8:
        return "breakout"
    if bull == 1.0 and pos is not None and 0.5 <= pos <= 0.8:
        return "uptrend"
    if bias is not None and abs(bias) <= 0.05:
        return "range"
    if bias is not None and bias < 0 and (ma5_20 or 0) < 0:
        return "weakening"
    if pos is not None and pos < 0.2:
        return "basing"
    if bull == 1.0:
        return "uptrend"
    if rsi is not None and rsi < 40:
        return "weakening"
    return "range"


# --- 出貨前檢查 -------------------------------------------------------


def sanity_check(result: dict, *, price: float, bars: int) -> dict:
    """最後一道防線（spec 7.5）。"""
    alerts: list[str] = list(result.get("excluded_tracks") or [])

    if bars < MIN_BARS_TO_PREDICT:
        return {
            **result,
            "predictable": False,
            "alerts": [f"可用日線僅 {bars} 根，不足 {MIN_BARS_TO_PREDICT} 根，不提供預測"],
            "confidence": 0.0,
        }

    upside = result.get("upside_pct")
    if is_finite(upside) and abs(float(upside)) > DEVIATION_ALERT:
        alerts.append(
            f"目標價偏離現價 {upside:+.0%}，超過 {DEVIATION_ALERT:.0%}，"
            "多為資料異常而非真有此空間"
        )
        result = {**result, "confidence": min(result.get("confidence", 0.0), 50.0)}

    directions = {
        name: (1 if float(track["fair"]) > price else -1)
        for name, track in (result.get("tracks") or {}).items()
        if is_finite(track.get("fair"))
    }
    if len(set(directions.values())) > 1:
        alerts.append("三軌方向分歧：不同方法對後續走勢看法相反")
        result = {
            **result,
            "confidence": min(result.get("confidence", 0.0), CONFLICT_CONFIDENCE_CAP),
        }

    predictable = result.get("confidence", 0.0) >= MIN_CONFIDENCE_TO_SHOW
    if not predictable:
        alerts.append("信心度不足，僅呈現數據不提供預測值")

    return {**result, "predictable": predictable, "alerts": alerts}
