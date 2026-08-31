"""軌道三：新聞情緒（詞典法，spec 6.3）。

不呼叫外部 LLM API：要錢、要網路、500 檔會很慢。

**準確率約 65-70%，明顯不如 LLM 語意理解。** 頁面必須標示為
「關鍵詞情緒」而非深度理解。本模組的介面（analyse_batch）設計成
可整批替換為 API 實作，屆時只需改這一個檔案。

英文詞表取自 Loughran-McDonald 金融情緒詞典的精簡子集 —— 這是學術界
專為財報文本設計的標準詞典。通用情緒詞典會把 liability、cost、
depreciation 誤判為負面，LM 詞典已修正這類財務中性詞。
"""
from __future__ import annotations

import math
import re
from datetime import datetime

# --- 中文財經詞典 ---------------------------------------------------

POSITIVE_ZH = {
    "創新高": 2.0, "歷史新高": 2.0, "漲停": 2.0, "飆漲": 1.8, "大漲": 1.5,
    "調升目標價": 2.0, "上調目標價": 2.0, "調升評等": 1.8, "升評": 1.5,
    "獲利創高": 2.0, "獲利倍增": 1.8, "獲利成長": 1.3, "轉盈": 1.6,
    "營收創高": 1.8, "營收成長": 1.2, "營收大增": 1.6, "訂單暢旺": 1.6,
    "接單暢旺": 1.6, "供不應求": 1.7, "產能滿載": 1.6, "滿單": 1.5,
    "擴產": 1.2, "新廠": 0.9, "投資擴充": 1.0, "資本支出增加": 0.9,
    "外資買超": 1.2, "投信買超": 1.1, "法人加碼": 1.3, "三大法人買超": 1.2,
    "股利增加": 1.3, "配息提高": 1.3, "現金股利": 0.6, "填息": 1.0,
    "簽約": 1.0, "得標": 1.3, "取得認證": 1.1, "通過驗證": 1.1,
    "市占提升": 1.4, "毛利率提升": 1.5, "毛利率創高": 1.7,
    "庫存去化": 1.2, "價格上漲": 1.1, "報價調漲": 1.4, "漲價": 1.3,
    "看好": 1.0, "樂觀": 1.0, "優於預期": 1.8, "超乎預期": 1.8,
    "轉單效應": 1.3, "受惠": 1.1, "利多": 1.4, "回溫": 1.0, "復甦": 1.2,
}

NEGATIVE_ZH = {
    "跌停": -2.0, "重挫": -1.8, "暴跌": -1.9, "大跌": -1.5, "破底": -1.7,
    "創新低": -1.8, "歷史新低": -1.8,
    "調降目標價": -2.0, "下調目標價": -2.0, "調降評等": -1.8, "降評": -1.5,
    "下修": -1.6, "獲利衰退": -1.6, "轉虧": -1.9, "虧損": -1.5,
    "認列虧損": -1.7, "減損": -1.4, "營收衰退": -1.5, "營收下滑": -1.4,
    "訂單流失": -1.7, "砍單": -1.9, "急單縮減": -1.5, "需求疲弱": -1.4,
    "減產": -1.4, "停產": -1.7, "關廠": -1.8, "裁員": -1.6, "無薪假": -1.7,
    "外資賣超": -1.2, "投信賣超": -1.1, "法人減碼": -1.3, "三大法人賣超": -1.2,
    "股利減少": -1.3, "配息降低": -1.3, "停止配息": -1.8, "貼息": -1.0,
    "違約": -2.0, "跳票": -2.0, "掏空": -2.0, "假帳": -2.0, "財報疑慮": -1.8,
    "遭起訴": -1.6, "被罰": -1.3, "裁罰": -1.3, "訴訟": -1.0, "召回": -1.4,
    "庫存積壓": -1.4, "價格下跌": -1.1, "報價調降": -1.4, "殺價": -1.5,
    "看壞": -1.0, "保守": -0.8, "不如預期": -1.8, "低於預期": -1.8,
    "毛利率下滑": -1.5, "市占流失": -1.4, "利空": -1.4, "警訊": -1.2,
    "下市": -2.0, "全額交割": -1.9, "處分股票": -0.9,
}

# --- 英文（Loughran-McDonald 精簡子集）------------------------------

POSITIVE_EN = {
    "beat": 1.6, "beats": 1.6, "exceeded": 1.7, "outperform": 1.6,
    "upgrade": 1.8, "upgraded": 1.8, "raised": 1.3, "raises": 1.3,
    "record": 1.5, "surge": 1.6, "surged": 1.6, "soar": 1.7, "soared": 1.7,
    "rally": 1.3, "gain": 1.0, "gains": 1.0, "jumped": 1.4, "climbed": 1.1,
    "profit": 0.9, "profitable": 1.3, "growth": 1.1, "expansion": 1.0,
    "strong": 1.2, "stronger": 1.3, "robust": 1.3, "solid": 1.0,
    "breakthrough": 1.6, "approval": 1.4, "approved": 1.4, "awarded": 1.3,
    "dividend": 0.6, "buyback": 1.2, "repurchase": 1.1,
    "optimistic": 1.2, "bullish": 1.5, "favorable": 1.2, "improved": 1.2,
}

NEGATIVE_EN = {
    "miss": -1.6, "missed": -1.6, "misses": -1.6, "shortfall": -1.6,
    "downgrade": -1.8, "downgraded": -1.8, "cut": -1.3, "cuts": -1.3,
    "plunge": -1.8, "plunged": -1.8, "slump": -1.6, "slumped": -1.6,
    "tumble": -1.6, "tumbled": -1.6, "fell": -1.0, "drop": -1.1,
    "loss": -1.5, "losses": -1.5, "deficit": -1.4, "impairment": -1.5,
    "warning": -1.4, "warned": -1.4, "weak": -1.2, "weaker": -1.3,
    "decline": -1.2, "declined": -1.2, "slowdown": -1.4, "recession": -1.5,
    "lawsuit": -1.3, "litigation": -1.2, "investigation": -1.4, "probe": -1.3,
    "fraud": -2.0, "restatement": -1.8, "bankruptcy": -2.0, "default": -1.9,
    "layoff": -1.6, "layoffs": -1.6, "recall": -1.4, "halt": -1.5,
    "bearish": -1.5, "pessimistic": -1.2, "unfavorable": -1.2,
}

# 否定前綴：出現在情緒詞之前時翻轉極性。
NEGATORS_ZH = ("不", "未", "無法", "沒有", "難以", "取消", "延後", "暫停")
NEGATORS_EN = ("not", "no", "without", "fails", "failed", "unable", "cancels", "cancelled")

TITLE_WEIGHT = 1.0
BODY_WEIGHT = 0.4
DECAY_HALFLIFE_DAYS = 3.5
LOOKBACK_DAYS = 7

_EN_TOKEN = re.compile(r"[A-Za-z']+")


def _negated_zh(text: str, position: int) -> bool:
    window = text[max(0, position - 4):position]
    return any(neg in window for neg in NEGATORS_ZH)


def score_text(text: str) -> float:
    """單段文字的原始情緒分數（未正規化）。"""
    if not text:
        return 0.0
    total = 0.0

    for word, weight in {**POSITIVE_ZH, **NEGATIVE_ZH}.items():
        start = 0
        while True:
            idx = text.find(word, start)
            if idx < 0:
                break
            total += -weight if _negated_zh(text, idx) else weight
            start = idx + len(word)

    tokens = [t.lower() for t in _EN_TOKEN.findall(text)]
    en_lexicon = {**POSITIVE_EN, **NEGATIVE_EN}
    for i, token in enumerate(tokens):
        weight = en_lexicon.get(token)
        if weight is None:
            continue
        negated = any(t in NEGATORS_EN for t in tokens[max(0, i - 3):i])
        total += -weight if negated else weight

    return total


def _squash(raw: float) -> float:
    """壓到 [-1, 1]。tanh 讓極端值不會主導，同時保留方向與強度。"""
    return math.tanh(raw / 3.0)


def _age_weight(published_at: str | None, now: datetime) -> float:
    if not published_at:
        return 0.5
    try:
        moment = datetime.fromisoformat(published_at)
    except ValueError:
        return 0.5
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=now.tzinfo)
    days = max((now - moment).total_seconds() / 86400.0, 0.0)
    if days > LOOKBACK_DAYS:
        return 0.0
    return 0.5 ** (days / DECAY_HALFLIFE_DAYS)


def analyse_item(title: str | None, summary: str | None = None) -> float:
    """單則新聞的情緒分數，範圍 [-1, 1]。標題權重高於摘要。"""
    raw = TITLE_WEIGHT * score_text(title or "") + BODY_WEIGHT * score_text(summary or "")
    return _squash(raw)


def analyse_batch(items: list[dict], now: datetime) -> dict:
    """一檔股票的新聞情緒彙總。

    items 每筆需含 title、published_at。回傳：
      sentiment  加權平均情緒（近期權重高），無有效新聞時為 None
      news_count 納入計算的則數
      trend      近 3 日均值減去第 4-7 日均值，用於判斷情緒轉向
      top        依情緒絕對值排序的代表性新聞（最多 3 則）
    """
    scored = []
    for item in items:
        weight = _age_weight(item.get("published_at"), now)
        if weight <= 0:
            continue
        score = analyse_item(item.get("title"), item.get("summary"))
        scored.append({**item, "sentiment": score, "_weight": weight})

    if not scored:
        return {"sentiment": None, "news_count": 0, "trend": None, "top": []}

    total_weight = sum(s["_weight"] for s in scored)
    weighted = sum(s["sentiment"] * s["_weight"] for s in scored) / total_weight

    recent = [s["sentiment"] for s in scored if s["_weight"] >= 0.5 ** (3 / DECAY_HALFLIFE_DAYS)]
    older = [s["sentiment"] for s in scored if s["_weight"] < 0.5 ** (3 / DECAY_HALFLIFE_DAYS)]
    trend = None
    if recent and older:
        trend = sum(recent) / len(recent) - sum(older) / len(older)

    top = sorted(scored, key=lambda s: abs(s["sentiment"]), reverse=True)[:3]
    return {
        "sentiment": weighted,
        "news_count": len(scored),
        "trend": trend,
        "top": [
            {"title": s.get("title"), "link": s.get("link"), "source": s.get("source"),
             "published_at": s.get("published_at"), "sentiment": round(s["sentiment"], 3)}
            for s in top
        ],
    }
