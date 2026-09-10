"""資金計算機測試。

純算術，所以每個值都測到精確數字 —— 這是這個模組與其他模組最大的不同：
它沒有統計不確定性，只有「算對」和「算錯」。
"""
import numpy as np
import pandas as pd
import pytest

from src import allocate


def _rules(**kw):
    base = dict(
        capital=500_000.0, max_per_position=0.15, max_positions=6,
        tranches=(0.4, 0.3, 0.3), cash_reserve=0.10,
        max_drawdown_tolerance=0.20,
    )
    return allocate.Rules(**{**base, **kw})


# --- 規則驗證 -----------------------------------------------------------


def test_tranches_must_sum_to_one():
    with pytest.raises(ValueError, match="每批比例"):
        _rules(tranches=(0.5, 0.3))


def test_capital_must_be_positive():
    with pytest.raises(ValueError, match="本金"):
        _rules(capital=0.0)


# --- 不代為篩選（spec §6.3）--------------------------------------------


def test_refuses_to_choose_when_candidates_exceed_max_positions():
    """挑哪幾檔是投資決策本身，程式不得代勞。"""
    candidates = [allocate.Candidate(f"S{i}.TW", "TW", 100.0) for i in range(9)]
    plan = allocate.build_plan(_rules(max_positions=6), candidates)

    assert plan.positions == ()
    assert plan.rejected_reason is not None
    assert "9" in plan.rejected_reason and "6" in plan.rejected_reason


def test_accepts_exactly_the_maximum():
    candidates = [allocate.Candidate(f"S{i}.TW", "TW", 100.0) for i in range(6)]
    plan = allocate.build_plan(_rules(max_positions=6), candidates)
    assert plan.rejected_reason is None
    assert len(plan.positions) == 6


# --- 台股整股與零股 -----------------------------------------------------


def test_tw_position_splits_into_lots_and_odd_shares():
    """本金 500,000、單檔上限 15% -> 每檔 75,000（含手續費的預算）。
    股價 100 -> 748 股（749 股連費會超過預算）= 0 整股 + 748 零股。"""
    plan = allocate.build_plan(
        _rules(tranches=(1.0,)), [allocate.Candidate("2330.TW", "TW", 100.0)],
    )
    pos = plan.positions[0]
    assert pos.total_shares == 748
    assert pos.lots == 0
    assert pos.odd_shares == 748


def test_tw_position_with_cheap_stock_forms_whole_lots():
    """股價 20 -> 含費後 3,744 股 = 3 整股 + 744 零股。"""
    plan = allocate.build_plan(
        _rules(tranches=(1.0,)), [allocate.Candidate("2882.TW", "TW", 20.0)],
    )
    pos = plan.positions[0]
    assert pos.total_shares == 3744
    assert pos.lots == 3
    assert pos.odd_shares == 744


def test_us_position_uses_whole_shares_only():
    plan = allocate.build_plan(
        _rules(tranches=(1.0,)), [allocate.Candidate("AAPL", "US", 230.0)],
    )
    pos = plan.positions[0]
    assert pos.total_shares == int(75_000 // 230)
    assert pos.lots == 0
    assert pos.odd_shares == pos.total_shares


# --- 整股化落差 ---------------------------------------------------------


def test_shortfall_is_reported_not_hidden():
    """75,000 買不到整數股時必然有剩，那個剩額要出現在輸出裡。"""
    plan = allocate.build_plan(
        _rules(tranches=(1.0,)), [allocate.Candidate("X.TW", "TW", 333.0)],
    )
    pos = plan.positions[0]
    assert pos.total_shares == 224           # 含費後 224，225 會超預算
    assert pos.shortfall == pytest.approx(75_000 - 224 * 333.0 - pos.total_fee)
    assert pos.shortfall > 0


# --- 手續費 -------------------------------------------------------------


def test_tw_fee_is_rate_times_amount():
    plan = allocate.build_plan(
        _rules(tranches=(1.0,)), [allocate.Candidate("X.TW", "TW", 100.0)],
    )
    pos = plan.positions[0]
    assert pos.total_fee == pytest.approx(748 * 100.0 * allocate.TW_FEE_RATE)


def test_tw_fee_has_a_floor_of_twenty():
    """小額買進時手續費有最低 20 元 —— 分批會放大這個影響。"""
    rules = _rules(capital=20_000.0, max_per_position=0.05, tranches=(1.0,))
    plan = allocate.build_plan(rules, [allocate.Candidate("X.TW", "TW", 50.0)])
    pos = plan.positions[0]
    # 1,000 元的部位，0.1425% = 1.4 元，必須被拉到 20 元
    assert pos.total_fee == pytest.approx(allocate.TW_FEE_MIN)


def test_fee_discount_applies_before_the_floor():
    rules = _rules(tranches=(1.0,), fee_discount=0.6)
    plan = allocate.build_plan(rules, [allocate.Candidate("X.TW", "TW", 100.0)])
    pos = plan.positions[0]
    assert pos.total_fee == pytest.approx(749 * 100.0 * allocate.TW_FEE_RATE * 0.6)


# --- 分批 ---------------------------------------------------------------


def test_tranches_split_the_target_amount():
    plan = allocate.build_plan(
        _rules(tranches=(0.5, 0.5)), [allocate.Candidate("X.TW", "TW", 100.0)],
    )
    pos = plan.positions[0]
    assert len(pos.tranches) == 2
    assert pos.tranches[0].target_amount == pytest.approx(37_500)
    assert pos.tranches[0].shares == 374
    assert sum(t.shares for t in pos.tranches) == pos.total_shares


def test_each_tranche_pays_its_own_minimum_fee():
    """分三批就付三次手續費 —— 分批的成本必須看得見。"""
    one = allocate.build_plan(
        _rules(capital=60_000.0, max_per_position=0.05, tranches=(1.0,)),
        [allocate.Candidate("X.TW", "TW", 100.0)],
    ).positions[0]
    three = allocate.build_plan(
        _rules(capital=60_000.0, max_per_position=0.05, tranches=(0.34, 0.33, 0.33)),
        [allocate.Candidate("X.TW", "TW", 100.0)],
    ).positions[0]
    assert three.total_fee > one.total_fee


# --- 現金 ---------------------------------------------------------------


def test_cash_reserve_is_never_invested():
    rules = _rules(max_per_position=1.0, max_positions=1, tranches=(1.0,))
    plan = allocate.build_plan(rules, [allocate.Candidate("X.TW", "TW", 100.0)])
    # 保留 10% -> 可投入 450,000
    assert plan.positions[0].target_amount == pytest.approx(450_000)
    assert plan.cash_left >= 50_000


def test_invested_plus_cash_equals_capital():
    candidates = [allocate.Candidate(f"S{i}.TW", "TW", 100.0 + i) for i in range(4)]
    plan = allocate.build_plan(_rules(), candidates)
    assert plan.invested + plan.cash_left == pytest.approx(plan.rules.capital)


# --- 壞資料 -------------------------------------------------------------


def test_candidate_with_non_finite_price_is_excluded_with_a_reason():
    plan = allocate.build_plan(_rules(), [
        allocate.Candidate("GOOD.TW", "TW", 100.0),
        allocate.Candidate("BAD.TW", "TW", float("nan")),
    ])
    assert [p.symbol for p in plan.positions] == ["GOOD.TW"]
    assert any("BAD.TW" in note for note in plan.notes)


def test_price_too_high_to_buy_one_share_is_excluded():
    rules = _rules(capital=10_000.0, max_per_position=0.10, tranches=(1.0,))
    plan = allocate.build_plan(rules, [allocate.Candidate("BRK-A", "US", 700_000.0)])
    assert plan.positions == ()
    assert any("BRK-A" in note for note in plan.notes)


# --- 組合層（描述性）---------------------------------------------------


def _price_frame(symbol, closes):
    n = len(closes)
    return pd.DataFrame({
        "symbol": symbol,
        "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d"),
        "adjclose": np.array(closes, dtype=float),
    })


def test_portfolio_stats_flags_drawdown_over_tolerance():
    """組合歷史最大回撤超過容忍值時要說，但不得建議換股。"""
    crash = list(np.linspace(100, 130, 150)) + list(np.linspace(130, 70, 150))
    history = pd.concat([_price_frame("A.TW", crash), _price_frame("B.TW", crash)])
    stats = allocate.portfolio_stats(
        {"A.TW": 0.5, "B.TW": 0.5}, history, tolerance=0.20,
    )
    assert stats["max_drawdown"] < -0.20
    assert stats["exceeds_tolerance"] is True


def test_portfolio_stats_does_not_flag_when_within_tolerance():
    steady = list(np.linspace(100, 120, 300))
    history = pd.concat([_price_frame("A.TW", steady), _price_frame("B.TW", steady)])
    stats = allocate.portfolio_stats(
        {"A.TW": 0.5, "B.TW": 0.5}, history, tolerance=0.20,
    )
    assert stats["exceeds_tolerance"] is False


def test_portfolio_stats_reports_average_correlation():
    rng = np.random.default_rng(0)
    a = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
    history = pd.concat([_price_frame("A.TW", a), _price_frame("B.TW", a)])
    stats = allocate.portfolio_stats({"A.TW": 0.5, "B.TW": 0.5}, history, tolerance=0.5)
    # 兩檔完全相同 -> 平均相關性為 1
    assert stats["avg_correlation"] == pytest.approx(1.0)


def test_portfolio_stats_returns_none_fields_without_enough_history():
    history = _price_frame("A.TW", [100.0, 101.0])
    stats = allocate.portfolio_stats({"A.TW": 1.0}, history, tolerance=0.2)
    assert stats["max_drawdown"] is None
    assert stats["exceeds_tolerance"] is None
