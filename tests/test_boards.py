"""榜單測試（spec §4.2：upside_pct 從排序中除役）。

那 88 筆留存預測證明 `prediction.upside_pct` 排序不是報酬預測器，
而是三軌分歧偵測器。以它排序的「看多榜」由結構保證只從最不可信的
那一格挑股票 —— 所以看多榜改由**有統計優勢的訊號**驅動。
"""
from datetime import datetime

from src import analysis, baserate

NOW = datetime(2026, 9, 10)


def _result(symbol, *, upside=0.10, confidence=95.0, predictable=True):
    return {
        "symbol": symbol, "name": symbol, "market": "TW", "price": 100.0,
        # _board_row 需要的欄位，補齊以免測試在 fixture 缺欄上失敗
        "industry": "半導體", "change_pct": 0.01,
        "trend": "uptrend", "trend_label": "穩步上升",
        "prediction": {
            "predictable": predictable, "upside_pct": upside,
            "confidence": confidence, "target_low": 90.0, "target_mid": 110.0,
            "target_high": 130.0, "trend": "uptrend",
        },
        "valuation": None,
        "fundamentals": {},
        "chips": {},
        "risk": {},
        "dividends": [],
    }


def test_bullish_board_is_empty_when_no_signal_has_edge():
    """沒有任何訊號有統計優勢時，看多榜必須是空的。

    修復前：以 upside_pct 排序，所以只要有預測就填得滿 —— 而填進去的
    正好是三軌吵架最兇、信心度剛好 40 的那些。
    """
    results = [_result(f"S{i}.TW", upside=0.40 - i * 0.01) for i in range(10)]
    boards = analysis.build_boards(results, NOW, edge_fires=())

    assert boards["bullish"] == []
    assert boards["bullish_note"]
    assert "統計優勢" in boards["bullish_note"]


def test_bullish_note_does_not_blame_the_market_when_the_cause_is_no_edge():
    """空榜的原因要說對：是訊號沒有優勢，不是今天沒人漲。"""
    results = [_result("2330.TW", upside=0.35)]
    note = analysis.build_boards(results, NOW, edge_fires=())["bullish_note"]
    assert "沒有" in note


def test_bullish_board_lists_only_symbols_with_an_edge_signal_firing():
    results = [_result("2330.TW"), _result("2317.TW"), _result("2454.TW")]
    fires = (
        baserate.Fire("2317.TW", "TW", "breakout_20d_vol2", "2026-09-10", 249.0),
    )
    boards = analysis.build_boards(results, NOW, edge_fires=fires)

    assert [row["symbol"] for row in boards["bullish"]] == ["2317.TW"]
    assert boards["bullish_note"] is None


def test_bullish_board_ignores_upside_ordering():
    """排序不得再由 upside_pct 決定。

    給兩檔都有 edge 訊號，但 upside 反向 —— 順序必須跟著訊號進來的
    順序，而不是被 upside 重新排。
    """
    results = [_result("LOW.TW", upside=0.01), _result("HIGH.TW", upside=0.99)]
    fires = (
        baserate.Fire("LOW.TW", "TW", "sig", "2026-09-10", 100.0),
        baserate.Fire("HIGH.TW", "TW", "sig", "2026-09-10", 100.0),
    )
    boards = analysis.build_boards(results, NOW, edge_fires=fires)
    assert [row["symbol"] for row in boards["bullish"]] == ["LOW.TW", "HIGH.TW"]


def test_edge_fire_for_an_unpredictable_stock_still_counts():
    """訊號是否觸發與三軌能不能合成目標價無關 —— 兩者是不同的判斷。"""
    results = [_result("2330.TW", predictable=False)]
    fires = (baserate.Fire("2330.TW", "TW", "sig", "2026-09-10", 100.0),)
    boards = analysis.build_boards(results, NOW, edge_fires=fires)
    assert [row["symbol"] for row in boards["bullish"]] == ["2330.TW"]


def test_other_boards_are_unaffected():
    """undervalued 排的是基本面軌道自己的偏離，不是那 88 筆量測的對象，
    所以不在這次除役範圍內。"""
    results = [_result("2330.TW")]
    boards = analysis.build_boards(results, NOW, edge_fires=())
    assert set(boards) == {"bullish", "bullish_note", "undervalued", "chips", "events"}
