"""產生單一 index.html（spec 8）。

資料以 <script type="application/json"> 內嵌，圖表自繪 SVG，
不引用任何外部資源 —— 離線 file:// 直接雙擊即可開啟。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 頁面上絕不允許出現的指示性字眼（spec 1.4）。
# 這裡是唯一的定義處，測試會掃描產出的 HTML 比對這份清單。
FORBIDDEN_PHRASES = (
    "建議買進", "建議賣出", "建議加碼", "建議減碼", "應該買", "應該賣",
    "建議配置", "應配置", "值得買入", "快進場", "立即買", "報酬保證",
    "穩賺", "必漲", "必跌", "包賺", "無風險",
)

DISCLAIMER = (
    "本頁所有數字為程式依公開資料計算的統計輸出，不是投資建議，"
    "也不構成任何買賣要約。製作者非持牌投資顧問。投資決策與風險由您自行判斷與承擔。"
)


def _jsonable(value):
    """把 numpy / NaN / Inf 轉成 JSON 可表示的形式。

    NaN 與 Infinity 不是合法 JSON，直接 dump 會產出瀏覽器無法解析的字面值。
    """
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating | float):
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    return value


def build_html(payload: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("index.html.j2")

    data = _jsonable({
        "stocks": payload["stocks"],
        "boards": payload["boards"],
        "report": payload["report"],
        "generated_at": payload["generated_at"],
        "horizon_days": payload.get("horizon_days", config.HORIZON_DAYS),
    })
    # </script> 出現在 JSON 內會提前關閉標籤；一併處理 JS 行分隔符
    serialized = (
        json.dumps(data, ensure_ascii=False, allow_nan=False)
        .replace("</", "<\\/")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )

    return template.render(
        data_json=serialized,
        disclaimer=DISCLAIMER,
        generated_at=payload["generated_at"],
        report=payload["report"],
        stock_count=len(payload["stocks"]),
        horizon_days=payload.get("horizon_days", config.HORIZON_DAYS),
        style=(TEMPLATE_DIR / "style.css").read_text(encoding="utf-8"),
        script=(TEMPLATE_DIR / "app.js").read_text(encoding="utf-8"),
    )


def write_html(payload: dict, path: Path | None = None) -> Path:
    target = Path(path or config.OUT_HTML)
    target.write_text(build_html(payload), encoding="utf-8")
    return target
