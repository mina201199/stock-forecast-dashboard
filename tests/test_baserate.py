"""基準率引擎測試。

spec §8.1 是這個引擎唯一的正確性證明：植入真有優勢的訊號必須判 edge，
純噪音必須判 no_edge。若引擎在純噪音中找到優勢，它就是壞的 ——
而一個壞掉的引擎比沒有引擎更危險，因為它會給錯誤的東西蓋上統計核可。
"""
import numpy as np
import pandas as pd
import pytest

from src import baserate
from src.signals.base import Signal

# --- 測試用的可控訊號 ---------------------------------------------------


def _planted_signal():
    """讀取注入的 planted 欄位，完全不依賴價格。

    刻意與價格解耦：若訊號由價格決定，而我們又依訊號改價格，
    兩者互相定義，植入的效果量就無法控制。
    """
    return Signal(
        key="planted", name="植入訊號", family="technical",
        markets=("TW",), rule=lambda df: df["planted"] == 1, min_bars=0,
    )


def _synthetic_frames(n_symbols, n_bars, *, drift_after_fire, seed, fire_every=30,
                      drift_from_bar=0, early_drift=0.0):
    """建構合成面板。drift_after_fire 是觸發後每日額外報酬。

    drift_from_bar 之前的觸發套用 early_drift，之後套用 drift_after_fire。
    用來合成「前半賠錢、後半大賺、池化後顯著」—— 真實資料上那個假象的形狀。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n_bars, freq="B").strftime("%Y-%m-%d")
    planted = np.zeros(n_bars)
    planted[::fire_every] = 1.0

    frames = []
    for i in range(n_symbols):
        daily = rng.normal(0, 0.015, n_bars)
        for t in np.flatnonzero(planted):
            drift = early_drift if int(t) < drift_from_bar else drift_after_fire
            if not drift:
                continue
            end = min(int(t) + 21, n_bars)
            daily[int(t) + 1:end] += drift
        adj = 100 * np.exp(np.cumsum(daily))
        frames.append(baserate.SymbolFrame(
            symbol=f"S{i}", market="TW",
            df=pd.DataFrame({"date": dates, "adjclose": adj, "planted": planted}),
        ))
    flat = pd.Series(np.ones(n_bars) * 100.0, index=dates)
    return frames, {"TW": flat}


# --- 前瞻報酬 -----------------------------------------------------------


def test_forward_excess_subtracts_benchmark():
    dates = pd.date_range("2024-01-01", periods=6, freq="B").strftime("%Y-%m-%d")
    stock = pd.Series([100, 100, 100, 110, 110, 110], index=dates, dtype=float)
    bench = pd.Series([100, 100, 100, 105, 105, 105], index=dates, dtype=float)

    out = baserate.forward_excess(stock, bench, horizon=3)

    # 第 0 根：個股 +10%、基準 +5% -> 超額 +5%
    assert out.iloc[0] == pytest.approx(0.10 - 0.05)


def test_forward_excess_is_nan_for_the_last_horizon_bars():
    """最後 horizon 根還沒有結果，必須是 NaN 而不是 0。

    給 0 會讓它們被算進分母，把勝率往 50% 拉。
    """
    dates = pd.date_range("2024-01-01", periods=10, freq="B").strftime("%Y-%m-%d")
    stock = pd.Series(np.linspace(100, 120, 10), index=dates)
    out = baserate.forward_excess(stock, stock * 0 + 100, horizon=3)
    assert out.iloc[-3:].isna().all()
    assert out.iloc[:-3].notna().all()


# --- n_effective --------------------------------------------------------


def test_n_effective_collapses_overlapping_fires():
    """連續 100 天每天觸發、期間 20 日 -> 只有 5 個互不重疊的區塊。"""
    calendar = list(range(200))
    assert baserate.n_effective(list(range(100)), calendar, horizon=20) == 5


def test_n_effective_counts_well_separated_fires_individually():
    calendar = list(range(300))
    assert baserate.n_effective([0, 100, 200], calendar, horizon=20) == 3


def test_n_effective_never_exceeds_number_of_fires():
    """spec §8.3：有效樣本永遠 ≤ 名目觸發次數。"""
    calendar = list(range(300))
    for fires in ([0], [0, 1], [0, 50, 51, 52], list(range(0, 300, 25))):
        assert baserate.n_effective(fires, calendar, horizon=20) <= len(fires)


# --- Benjamini-Hochberg -------------------------------------------------


def test_bh_leaves_a_single_pvalue_unchanged():
    assert baserate.benjamini_hochberg([0.03]) == pytest.approx([0.03])


def test_bh_inflates_pvalues_when_many_tests():
    raw = [0.01, 0.02, 0.03, 0.04, 0.05]
    adjusted = baserate.benjamini_hochberg(raw)
    assert all(a >= r for a, r in zip(adjusted, raw, strict=True))
    assert adjusted[-1] == pytest.approx(0.05)   # 最大的 p 校正後不變


def test_bh_output_is_monotone_in_input_order():
    raw = [0.5, 0.001, 0.2, 0.04]
    adjusted = baserate.benjamini_hochberg(raw)
    order = np.argsort(raw)
    assert list(np.argsort(adjusted)) == list(order)


# --- verdict 門檻 -------------------------------------------------------


def test_verdict_is_insufficient_below_effective_sample_floor():
    v = baserate.verdict(
        n_effective=19, p_adjusted=0.0001, ci_low=0.05,
        hit_rate=0.9, baseline_hit_rate=0.5,
    )
    assert v == "insufficient"


def test_verdict_requires_all_three_conditions():
    strong = dict(n_effective=50, p_adjusted=0.01, ci_low=0.02,
                  hit_rate=0.6, baseline_hit_rate=0.5)
    assert baserate.verdict(**strong) == "edge"

    assert baserate.verdict(**{**strong, "p_adjusted": 0.20}) == "no_edge"
    assert baserate.verdict(**{**strong, "ci_low": -0.01}) == "no_edge"
    assert baserate.verdict(**{**strong, "hit_rate": 0.49}) == "no_edge"


# --- spec §8.1：引擎唯一的正確性證明 -----------------------------------


def test_planted_edge_is_recovered():
    """植入每日 +0.25% 的觸發後漂移（20 日約 +5%），引擎必須判 edge。"""
    frames, benchmarks = _synthetic_frames(
        15, 700, drift_after_fire=0.0025, seed=1,
    )
    rate = baserate.evaluate(
        _planted_signal(), frames, benchmarks,
        market="TW", horizon=20, n_boot=400, seed=7,
    )
    assert rate.n_fires > 0
    assert rate.median_excess > 0.02
    assert rate.hit_rate > rate.baseline_hit_rate
    assert rate.ci_low > 0
    assert rate.verdict == "edge"


def test_pure_noise_is_not_mistaken_for_edge():
    """純噪音必須判 no_edge。

    用 8 個不同種子檢定，而不是一個 —— 單一種子若剛好過關，換個種子
    就會失敗，那樣的測試是在挑資料。允許少量偽陽性（門檻 p<0.10，
    期望值約 1 個），但不允許系統性地在噪音裡看到訊號。
    """
    edges = 0
    for seed in range(8):
        frames, benchmarks = _synthetic_frames(
            15, 700, drift_after_fire=0.0, seed=seed,
        )
        rate = baserate.evaluate(
            _planted_signal(), frames, benchmarks,
            market="TW", horizon=20, n_boot=400, seed=100 + seed,
        )
        if rate.verdict == "edge":
            edges += 1
    assert edges <= 2, f"8 次純噪音中有 {edges} 次被判為 edge，引擎過度樂觀"


def test_evaluate_reports_effective_sample_below_nominal_fires():
    """真實面板上，有效樣本必須遠小於名目觸發次數（spec §3.2）。"""
    frames, benchmarks = _synthetic_frames(15, 700, drift_after_fire=0.0, seed=3)
    rate = baserate.evaluate(
        _planted_signal(), frames, benchmarks,
        market="TW", horizon=20, n_boot=100, seed=1,
    )
    # 15 檔 × 23 個可用觸發日 = 345 次名目觸發，但只有 23 個獨立日期。
    # 這個比例（低於十分之一）就是 spec §3.2 講的那個陷阱的量化形式。
    assert rate.n_fires > 300
    assert rate.n_effective < rate.n_fires / 10


def test_evaluate_returns_insufficient_when_signal_never_fires():
    frames, benchmarks = _synthetic_frames(5, 300, drift_after_fire=0.0, seed=5)
    never = Signal(
        key="never", name="不觸發", family="technical",
        markets=("TW",), rule=lambda df: pd.Series(False, index=df.index), min_bars=0,
    )
    rate = baserate.evaluate(
        never, frames, benchmarks, market="TW", horizon=20, n_boot=50, seed=1,
    )
    assert rate.n_fires == 0
    assert rate.verdict == "insufficient"


# --- 從資料庫載入 -------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    from src import store
    c = store.connect(tmp_path / "t.db")
    store.init_schema(c)
    yield c
    c.close()


def _seed(conn, symbol, market, n=80, start=100.0):
    from src import store
    store.upsert(conn, "stocks", [{
        "symbol": symbol, "market": market, "code": symbol.split(".")[0], "name": symbol,
    }])
    rows = []
    dates = pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    for i, d in enumerate(dates):
        px = start + i
        rows.append({"symbol": symbol, "date": d, "open": px, "high": px,
                     "low": px, "close": px, "adjclose": px, "volume": 1000})
    store.upsert(conn, "prices", rows)


def test_load_frames_tags_market_from_stocks_table(conn):
    _seed(conn, "2330.TW", "TW")
    _seed(conn, "AAPL", "US")
    _seed(conn, "0050.TW", "TW")
    _seed(conn, "SPY", "US")

    frames, benchmarks = baserate.load_frames(conn)

    by_symbol = {f.symbol: f for f in frames}
    assert by_symbol["2330.TW"].market == "TW"
    assert by_symbol["AAPL"].market == "US"
    assert set(benchmarks) == {"TW", "US"}


def test_load_frames_excludes_benchmark_symbols_from_candidates(conn):
    """基準本身不該被當成候選標的去量測 —— 它的超額報酬恆為 0。"""
    _seed(conn, "2330.TW", "TW")
    _seed(conn, "0050.TW", "TW")
    _seed(conn, "SPY", "US")

    frames, _ = baserate.load_frames(conn)

    assert {f.symbol for f in frames} == {"2330.TW"}


def test_load_frames_computes_indicators(conn):
    _seed(conn, "2330.TW", "TW")
    _seed(conn, "0050.TW", "TW")
    frames, _ = baserate.load_frames(conn)
    assert "rsi14" in frames[0].df.columns
    assert "bullish_stack" in frames[0].df.columns


# --- 跨期穩定性（實跑真實資料後補上的閘門）-----------------------------


def test_verdict_rejects_unstable_effect_even_when_significant():
    """效應只存在於一半期間時，不得判 edge。

    這條是實跑三年台股後補上的。breakout_52w 全期判 edge（勝率 58.9%、
    p 校正 0.000），但分前後半後：前半中位超額 −1.44%、後半 +5.32%。
    0050.TW 這三年漲了 270.6% —— 全期池化把「一段多頭行情」冒充成
    「一個有預測力的訊號」。
    """
    strong = dict(n_effective=50, p_adjusted=0.001, ci_low=0.02,
                  hit_rate=0.6, baseline_hit_rate=0.5)
    assert baserate.verdict(**strong, stable=True) == "edge"
    assert baserate.verdict(**strong, stable=False) == "unstable"


def test_verdict_treats_unknown_stability_as_unstable():
    """樣本切不出兩個子期間時，不能當成穩定 —— 未知不是通過。"""
    strong = dict(n_effective=50, p_adjusted=0.001, ci_low=0.02,
                  hit_rate=0.6, baseline_hit_rate=0.5)
    assert baserate.verdict(**strong, stable=None) == "unstable"


def test_edge_present_in_both_halves_is_still_edge():
    frames, benchmarks = _synthetic_frames(15, 700, drift_after_fire=0.0025, seed=1)
    rate = baserate.evaluate(
        _planted_signal(), frames, benchmarks,
        market="TW", horizon=20, n_boot=400, seed=7,
    )
    assert rate.first_half_excess > 0
    assert rate.second_half_excess > 0
    assert rate.verdict == "edge"


def test_edge_only_in_second_half_is_reported_unstable():
    """植入只存在於後半期的優勢 —— 引擎必須判 unstable，不是 edge。

    這是真實資料上那個假象的合成版本，當回歸測試用。
    """
    # 觸發間隔 200 根遠大於 20 日窗口，未觸發組才不會被漂移污染 ——
    # 間隔 30 根時三分之二的未觸發 bar 也落在漂移窗口內，基準命中率
    # 會被拉到 70%，比觸發組還高。
    frames, benchmarks = _synthetic_frames(
        10, 5000, drift_after_fire=0.004, seed=2, fire_every=200,
        drift_from_bar=2500, early_drift=-0.001,
    )
    rate = baserate.evaluate(
        _planted_signal(), frames, benchmarks,
        market="TW", horizon=20, n_boot=400, seed=7,
    )
    assert rate.ci_low > 0, "全期池化本來就會顯著，這是前提"
    assert rate.first_half_excess < rate.second_half_excess
    assert rate.verdict == "unstable"


def test_stability_requires_beating_the_index_not_just_other_stocks():
    """觸發組相對未觸發組贏、但相對指數輸的那半期，不得判 edge。

    實跑真實資料時 breakout_52w / TW / 20日 出現這個情形：
      前半 觸發組中位超額 −1.44%（輸 0050），但 觸發組−未觸發組 +0.90%
    前半台股弱勢，未觸發的個股比 0050 更差，所以「相對其他股票」贏、
    「相對指數」輸。使用者的替代選項是買 0050，不是隨機買一檔沒觸發的
    股票 —— 所以絕對超額必須也為正，兩個條件都要。
    """
    common = dict(n_effective=50, p_adjusted=0.01, ci_low=0.02,
                  hit_rate=0.6, baseline_hit_rate=0.5)
    assert baserate.verdict(**common, stable=True) == "edge"
    # 相對贏（stable）但絕對輸 -> 不得是 edge
    assert baserate.verdict(**common, stable=True, beats_index=False) == "unstable"
    assert baserate.verdict(**common, stable=True, beats_index=None) == "unstable"


def test_evaluate_reports_absolute_median_excess_per_half():
    frames, benchmarks = _synthetic_frames(15, 700, drift_after_fire=0.0025, seed=1)
    rate = baserate.evaluate(
        _planted_signal(), frames, benchmarks,
        market="TW", horizon=20, n_boot=400, seed=7,
    )
    assert rate.first_half_median > 0
    assert rate.second_half_median > 0
    assert rate.verdict == "edge"
