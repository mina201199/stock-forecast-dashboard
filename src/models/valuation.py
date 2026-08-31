"""軌道二：基本面合理價（規則式，非 ML）。

**與 spec 6.2 的偏離，以及為什麼**

spec 原訂「取該檔過去 3 年 PE 的 P20/P50/P80」。但 fundamentals 表每次
執行只寫入一筆當前快照，第一次跑並沒有三年的 PE 歷史。若改用
「歷史股價 ÷ 當前 EPS」湊出一條 PE 序列，因為 EPS 是常數，那條序列
其實只是股價乘上一個常數 —— 算出來的「PE 帶狀」就是價格帶狀，
名義上是估值、實質上沒有任何基本面資訊。那是自欺。

改用**同業相對估值**：拿該檔的 PE／PB／殖利率與「同市場同產業其他
個股」的分佈比較。這是用當下就真實存在的橫斷面資料，不需要歷史累積。

比較範圍絕不跨市場 —— 見 industry_peer_values 的說明。

三種方法依股票性質選用：
- PE 法：EPS > 0 的一般股（主力）
- PB 法：金融業、資產股，以及 **EPS <= 0 時 PE 法失效的替代**
- 殖利率法：殖利率 > 4% 且配息穩定者納入加權
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.numeric import all_finite, is_finite, is_positive

FINANCIAL_KEYWORDS = ("金融", "保險", "銀行", "證券", "Financial", "Bank", "Insurance")
HIGH_YIELD_THRESHOLD = 4.0
MIN_PEERS = 5

PERCENTILES = (0.2, 0.5, 0.8)


def is_financial(industry: str | None) -> bool:
    text = industry or ""
    return any(keyword in text for keyword in FINANCIAL_KEYWORDS)


def _peer_bands(values: list[float | None]) -> tuple[float, float, float] | None:
    valid = [float(v) for v in values if is_positive(v)]
    if len(valid) < MIN_PEERS:
        return None
    low, mid, high = np.quantile(valid, PERCENTILES)
    return float(low), float(mid), float(high)


def pe_band(price: float, pe: float | None, peer_pes: list[float | None]) -> dict | None:
    """本益比法。fair = 同業 PE 百分位 × 該檔 EPS。

    EPS 由 price / pe 反推，因此 pe 必須為正 —— 虧損公司的 PE 在 TWSE
    是空字串，早已在解析階段轉成 None（見 src/parsing.py）。
    """
    if not is_positive(pe) or not is_positive(price):
        return None
    bands = _peer_bands(peer_pes)
    if bands is None:
        return None
    eps = price / pe
    low, mid, high = bands
    return {"method": "PE", "low": eps * low, "fair": eps * mid, "high": eps * high}


def pb_band(price: float, pb: float | None, peer_pbs: list[float | None]) -> dict | None:
    """股價淨值比法。給金融股、資產股，以及 EPS <= 0 時的替代。"""
    if not is_positive(pb) or not is_positive(price):
        return None
    bands = _peer_bands(peer_pbs)
    if bands is None:
        return None
    book_value = price / pb
    low, mid, high = bands
    return {"method": "PB", "low": book_value * low, "fair": book_value * mid,
            "high": book_value * high}


def yield_band(
    price: float, dividend_yield: float | None, peer_yields: list[float | None]
) -> dict | None:
    """殖利率法。fair = 現金股利 / 同業殖利率百分位。

    殖利率與價格反向，故高殖利率百分位對應低價，回傳前需反轉上下界。
    """
    if not is_positive(dividend_yield) or not is_positive(price):
        return None
    bands = _peer_bands(peer_yields)
    if bands is None:
        return None
    dividend = price * dividend_yield / 100.0
    low_y, mid_y, high_y = bands
    return {
        "method": "YIELD",
        "low": dividend / high_y * 100.0,   # 殖利率高 -> 價格低
        "fair": dividend / mid_y * 100.0,
        "high": dividend / low_y * 100.0,
    }


def select_weights(
    eps_positive: bool, financial: bool, dividend_yield: float | None
) -> dict[str, float]:
    """依股票性質決定三法權重。"""
    if financial or not eps_positive:
        weights = {"PE": 0.0, "PB": 1.0, "YIELD": 0.0}
    else:
        weights = {"PE": 0.7, "PB": 0.3, "YIELD": 0.0}
    if is_finite(dividend_yield) and float(dividend_yield) > HIGH_YIELD_THRESHOLD:
        weights = {k: v * 0.75 for k, v in weights.items()}
        weights["YIELD"] = 0.25
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()} if total else weights


def combine(estimates: list[dict], weights: dict[str, float]) -> dict | None:
    """加權合成。只計入實際算得出來的方法，並重新正規化權重。"""
    def _ok(e):
        return e is not None and all_finite(e.get(k) for k in ("low", "fair", "high"))

    usable = [e for e in estimates if _ok(e) and weights.get(e["method"], 0) > 0]
    if not usable:
        # 沒有任何偏好方法可用時，退回任何算得出來的方法
        usable = [e for e in estimates if _ok(e)]
        if not usable:
            return None
        weights = {e["method"]: 1.0 for e in usable}

    total = sum(weights[e["method"]] for e in usable)
    out = {}
    for key in ("low", "fair", "high"):
        out[key] = sum(e[key] * weights[e["method"]] for e in usable) / total
    out["methods"] = [e["method"] for e in usable]
    return out


def estimate(
    *,
    price: float,
    pe: float | None,
    pb: float | None,
    dividend_yield: float | None,
    industry: str | None,
    peer_pes: list[float | None],
    peer_pbs: list[float | None],
    peer_yields: list[float | None],
    peer_scope: str | None = "industry",
) -> dict | None:
    """單檔的合理價區間。回傳 low/fair/high/methods/upside_pct/peer_scope。"""
    if peer_scope is None:
        return None  # 連同市場樣本都不足，不給估值
    if not is_positive(price):
        return None
    estimates = [
        pe_band(price, pe, peer_pes),
        pb_band(price, pb, peer_pbs),
        yield_band(price, dividend_yield, peer_yields),
    ]
    weights = select_weights(
        eps_positive=is_positive(pe),
        financial=is_financial(industry),
        dividend_yield=dividend_yield,
    )
    combined = combine(estimates, weights)
    if combined is None:
        return None
    combined["upside_pct"] = combined["fair"] / price - 1
    combined["peer_scope"] = peer_scope
    return combined


def industry_peer_values(
    rows: pd.DataFrame, market: str, industry: str | None, column: str
) -> tuple[list[float | None], str | None]:
    """同業比較的樣本，以及樣本的來源層級。

    **絕不跨市場比較。** 台股與美股的本益比水準、產業結構、會計基礎
    都不同，混在一起會產生垃圾：實測把 PE 14 的美國保險股拿去對上
    含台股半導體的混合池，算出的「合理價」是現價的四倍。

    退回層級：
      "industry" 同市場同產業（樣本 >= MIN_PEERS）—— 最可信
      "market"   同市場全體 —— 可用但較粗略，信心度會反映
      None       連同市場樣本都不足 —— 不給估值，而不是給一個爛數字
    """
    same_market = rows[rows["market"] == market]

    if industry:
        peers = same_market[same_market["industry"] == industry]
        if len(peers) >= MIN_PEERS:
            return peers[column].tolist(), "industry"

    if len(same_market) >= MIN_PEERS:
        return same_market[column].tolist(), "market"

    return [], None
