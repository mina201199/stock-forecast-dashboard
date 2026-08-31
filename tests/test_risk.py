import numpy as np
import pandas as pd
import pytest

from src import risk


def _prices(returns, start=100.0):
    return pd.Series(start * np.cumprod(1 + np.array(returns, dtype=float)))


def test_annualized_volatility_matches_manual_formula():
    rng = np.random.default_rng(11)
    returns = rng.normal(0, 0.01, 300)
    series = _prices(returns)
    expected = pd.Series(returns).std(ddof=1) * np.sqrt(252)
    assert risk.annualized_volatility(series) == pytest.approx(expected, rel=0.02)


def test_volatility_none_when_too_few_points():
    assert risk.annualized_volatility(_prices([0.01] * 5)) is None


def test_downside_volatility_ignores_positive_returns():
    """全漲的序列沒有下檔波動可算。"""
    assert risk.downside_volatility(_prices([0.01] * 100)) is None


def test_downside_volatility_below_total_when_upside_is_noisier():
    rng = np.random.default_rng(5)
    returns = np.where(rng.random(300) < 0.5, rng.normal(0.03, 0.03, 300),
                       rng.normal(-0.005, 0.003, 300))
    series = _prices(returns)
    assert risk.downside_volatility(series) < risk.annualized_volatility(series)


def test_beta_is_one_when_stock_tracks_benchmark_exactly():
    rng = np.random.default_rng(3)
    returns = rng.normal(0, 0.012, 300)
    series = _prices(returns)
    assert risk.beta(series, series.copy()) == pytest.approx(1.0, rel=1e-6)


def test_beta_is_two_for_double_amplitude():
    rng = np.random.default_rng(9)
    bench_returns = rng.normal(0, 0.01, 400)
    bench = _prices(bench_returns)
    stock = _prices(2 * bench_returns)
    assert risk.beta(stock, bench) == pytest.approx(2.0, rel=0.05)


def test_beta_none_when_benchmark_is_flat():
    """基準無波動時斜率無定義，必須回 None 而非除以零。"""
    flat = pd.Series([100.0] * 300)
    rng = np.random.default_rng(1)
    assert risk.beta(_prices(rng.normal(0, 0.01, 300)), flat) is None


def test_max_drawdown_matches_known_path():
    series = pd.Series([100.0] * 10 + [100, 120, 60, 90] + [90.0] * 10)
    assert risk.max_drawdown(series) == pytest.approx(-0.5)


def test_max_drawdown_is_zero_for_monotonic_rise():
    assert risk.max_drawdown(pd.Series(np.arange(1, 100, dtype=float))) == pytest.approx(0.0)


def test_quintile_level_places_extremes_correctly():
    population = list(np.arange(0.0, 100.0))
    assert risk.quintile_level(1.0, population) == "極低"
    assert risk.quintile_level(98.0, population) == "極高"
    assert risk.quintile_level(50.0, population) == "中"


def test_quintile_level_none_for_tiny_population():
    assert risk.quintile_level(1.0, [1.0, 2.0]) is None


def test_percentile_rank_is_neutral_number_not_a_judgement():
    population = list(np.arange(0.0, 100.0))
    assert risk.percentile_rank(88.0, population) == pytest.approx(89.0)
    assert risk.percentile_rank(None, population) is None


def test_compute_returns_all_keys_even_without_benchmark():
    rng = np.random.default_rng(2)
    series = _prices(rng.normal(0, 0.01, 300))
    out = risk.compute(series, None, turnover=1e8)
    assert set(out) == {
        "volatility_60d", "volatility_252d", "downside_volatility",
        "beta", "max_drawdown", "turnover_ma20",
    }
    assert out["beta"] is None
    assert out["turnover_ma20"] == 1e8
