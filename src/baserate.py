"""基準率引擎（spec §3）。

**為什麼需要這個模組**

2026-08-31 留存的 88 筆預測在 9/10 回頭比對，方向命中率 48.9%、
IC −0.019，與隨機無法區分。更關鍵的是查出 `upside_pct` 排序實際上
是「三軌分歧偵測器」而非報酬預測器 —— 預測漲幅前 12 名的信心度
全部都是 40（`CONFLICT_CONFIDENCE_CAP` 與 `MIN_CONFIDENCE_TO_SHOW`
兩個常數相等所致）。

那件事花了 88 筆預測與 10 天才被發現。這個模組的存在，就是為了讓
「一個數字有沒有實證支撐」變成出貨前的閘門，而不是出貨後的考古。

**四個會讓勝率變成謊言的陷阱**（逐一在下面處置）

1. 前視偏誤 —— 訊號層以截斷不變性測試強制（`tests/test_signals.py`）
2. 重疊窗口與橫斷面相關 —— 按日期做 block bootstrap，並回報 `n_effective`
3. 多重檢定 —— 全 registry 做 Benjamini-Hochberg 校正
4. 參數對著結果調 —— 第一階段完全不搜參數（訊號參數寫死）
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

import config
from src import features
from src.numeric import is_finite
from src.signals.base import Signal

BENCHMARKS = {"TW": config.TW_BENCHMARK, "US": config.US_BENCHMARK}

HORIZONS = (5, 10, 20)

# 有效樣本下限。刻意保守：低於此，任何勝率都是噪音。
MIN_EFFECTIVE = 20

# BH 校正後的顯著門檻
EDGE_P = 0.10

N_BOOT = 2000
CI_PERCENTILES = (2.5, 97.5)


@dataclass(frozen=True)
class SymbolFrame:
    """一檔個股的指標表。df 需含 date 與 adjclose，並依 date 遞增。"""

    symbol: str
    market: str
    df: pd.DataFrame


@dataclass(frozen=True)
class BaseRate:
    signal_key: str
    market: str
    horizon: int
    n_fires: int
    n_effective: int
    hit_rate: float
    baseline_hit_rate: float
    median_excess: float
    ci_low: float
    ci_high: float
    p_value: float
    p_adjusted: float
    first_half_excess: float      # 觸發組 − 未觸發組（橫斷面）
    second_half_excess: float
    first_half_median: float      # 觸發組自己的中位超額（相對指數）
    second_half_median: float
    verdict: str


@dataclass(frozen=True)
class Fire:
    """今天觸發的一筆訊號。"""

    symbol: str
    market: str
    signal_key: str
    date: str
    price: float


def scan_latest(signals: Sequence[Signal], frames: Sequence[SymbolFrame]) -> list[Fire]:
    """掃描每檔最後一根 bar 有沒有觸發。

    刻意呼叫與回測完全相同的 `signal.fires` —— 掃描與回測共用一段程式
    是這個設計最重要的防線（見 `signals/base.py`）。
    """
    out: list[Fire] = []
    for frame in frames:
        df = frame.df
        if df.empty:
            continue
        for signal in signals:
            if frame.market not in signal.markets:
                continue
            fired = signal.fires(df)
            if bool(fired.iloc[-1]):
                out.append(Fire(
                    symbol=frame.symbol, market=frame.market, signal_key=signal.key,
                    date=str(df["date"].iloc[-1]),
                    price=float(df["adjclose"].iloc[-1]),
                ))
    return out


# --- 持久化 -------------------------------------------------------------

_STATS_COLUMNS = (
    "signal_key", "market", "horizon", "n_fires", "n_effective",
    "hit_rate", "baseline_hit_rate", "median_excess", "ci_low", "ci_high",
    "p_value", "p_adjusted", "first_half_excess", "second_half_excess",
    "first_half_median", "second_half_median", "verdict",
)


def save_stats(conn, rates: Sequence[BaseRate]) -> int:
    """寫入 signal_stats。同一組 (訊號, 市場, 期間) 覆寫舊值。

    基準率要走全歷史，慢，所以不在每日流程算；存起來讓日報直接讀。
    """
    from src import store

    rows = [
        {**{c: getattr(r, c) for c in _STATS_COLUMNS},
         "computed_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")}
        for r in rates
    ]
    return store.upsert(conn, "signal_stats", rows)


def load_stats(conn) -> list[BaseRate]:
    frame = pd.read_sql_query(
        f"SELECT {', '.join(_STATS_COLUMNS)} FROM signal_stats", conn
    )
    return [BaseRate(**row) for row in frame.to_dict("records")]


# --- 前瞻報酬 -----------------------------------------------------------


def forward_excess(adjclose: pd.Series, benchmark: pd.Series, horizon: int) -> pd.Series:
    """第 t 根收盤到第 t+horizon 根收盤的超額報酬。

    最後 horizon 根必須是 NaN 而不是 0 —— 它們還沒有結果。給 0 會讓
    它們被算進分母，把勝率往 50% 拉，這正是 `accuracy.py` 已經處理過的
    同一類錯誤。
    """
    fwd = adjclose.shift(-horizon) / adjclose - 1
    bench = benchmark.reindex(adjclose.index).ffill()
    bench_fwd = bench.shift(-horizon) / bench - 1
    return fwd - bench_fwd


# --- 有效樣本 -----------------------------------------------------------


def n_effective(fired_dates: Iterable, calendar: Sequence, horizon: int) -> int:
    """觸發過的、互不重疊的日期區塊數（spec §3.2）。

    **不是**觸發次數。20 日前瞻窗口讓連續兩天的觸發共用 19/20 的結果，
    而同一天所有標的一起動 —— 名目 n=3,000 的有效樣本可能只有 40。
    貪心法從最早的觸發日開始，每取一個就跳過其後 horizon 個交易日。
    """
    position = {d: i for i, d in enumerate(calendar)}
    positions = sorted({position[d] for d in fired_dates if d in position})
    count = 0
    next_allowed = -(10**9)
    for pos in positions:
        if pos >= next_allowed:
            count += 1
            next_allowed = pos + horizon
    return count


# --- 多重檢定 -----------------------------------------------------------


def benjamini_hochberg(pvalues: Sequence[float]) -> list[float]:
    """BH 校正後的 p 值，順序與輸入一致。

    第一階段的檢定數是 10 訊號 × 2 市場 × 3 期間 = 60 次。看三個期間
    的代價就在這裡：不校正的話，60 次裡出現幾個 p<0.05 純屬必然。
    """
    raw = np.asarray(list(pvalues), dtype=float)
    n = raw.size
    if n == 0:
        return []
    order = np.argsort(raw)
    scaled = raw[order] * n / np.arange(1, n + 1)
    # 由大而小取累積最小值，確保校正後仍單調
    adjusted = np.minimum.accumulate(scaled[::-1])[::-1]
    out = np.empty(n, dtype=float)
    out[order] = np.clip(adjusted, 0.0, 1.0)
    return out.tolist()


# --- 判定 ---------------------------------------------------------------


def verdict(
    *, n_effective: float, p_adjusted: float, ci_low: float,
    hit_rate: float, baseline_hit_rate: float, stable: bool | None = True,
    beats_index: bool | None = True,
) -> str:
    """spec §2.4 的門檻，依序判定。

    前三個條件並列的理由：單看 p 值會讓極小但顯著的效果過關；單看 CI 會
    漏掉多重檢定；單看勝率會被多頭段的無條件基準騙過。三個各擋一種失敗。

    **第四個條件（stable）是實跑三年台股之後補上的。** `breakout_52w`
    全期判 edge —— 勝率 58.9%、中位超額 +4.37%、p 校正 0.000，三個條件
    全數通過。但分前後半期後：前半中位超額 −1.44%（觸發 180 次），
    後半 +5.32%（觸發 2,117 次）。同期 0050.TW 漲了 270.6%。

    全期池化把「一段多頭行情」冒充成「一個有預測力的訊號」。這在結構上
    與這個專案一開始診斷出的那個錯誤同類：`upside_pct` 排序其實是三軌
    分歧偵測器。顯著性不等於預測力 —— 它只說「這個池子裡有東西」，
    不說那個東西會不會再出現。

    **第五個條件（beats_index）**：`stable` 比的是「觸發組 vs 未觸發組」，
    是橫斷面主張。但使用者的替代選項是買指數，不是隨機買一檔沒觸發的
    股票。實跑時 breakout_52w / TW / 20日 前半的觸發組中位超額是 −1.44%
    （輸 0050），而觸發組減未觸發組是 +0.90% —— 前半台股弱勢，未觸發的
    個股比 0050 更差。「相對其他股票贏、相對指數輸」不該叫 edge。

    `stable=None` / `beats_index=None`（切不出子期間）視為不通過。
    未知不是通過。預設 True 只為了讓其他條件能被單獨測試；
    `evaluate` 一律明確傳值。
    """
    if not is_finite(n_effective) or n_effective < MIN_EFFECTIVE:
        return "insufficient"
    passes = (
        is_finite(p_adjusted) and p_adjusted < EDGE_P
        and is_finite(ci_low) and ci_low > 0
        and is_finite(hit_rate) and is_finite(baseline_hit_rate)
        and hit_rate > baseline_hit_rate
    )
    if not passes:
        return "no_edge"
    if stable is not True or beats_index is not True:
        return "unstable"
    return "edge"


def half_period_excess(panel: pd.DataFrame) -> tuple[float, float] | None:
    """把面板切成前後半，各自回傳 (觸發組 − 未觸發組) 的平均超額報酬。

    切點用日期中位數而非觸發次數中位數 —— 用觸發次數切會讓觸發集中的
    那一段自己佔滿一整個子期間，穩定性檢查就失效了（真實資料上
    breakout_52w 後半的觸發次數是前半的 11 倍）。
    """
    dates = np.sort(panel["date"].unique())
    if dates.size < 4:
        return None
    mid = dates[dates.size // 2]
    out = []
    for sub in (panel[panel["date"] < mid], panel[panel["date"] >= mid]):
        fired, others = sub[sub["fired"]], sub[~sub["fired"]]
        if fired.empty or others.empty:
            return None
        out.append(float(fired["excess"].mean() - others["excess"].mean()))
    return out[0], out[1]


def half_period_median(panel: pd.DataFrame) -> tuple[float, float] | None:
    """前後半各自的「觸發組中位超額」—— 相對指數，不是相對其他個股。"""
    dates = np.sort(panel["date"].unique())
    if dates.size < 4:
        return None
    mid = dates[dates.size // 2]
    out = []
    for sub in (panel[panel["date"] < mid], panel[panel["date"] >= mid]):
        fired = sub[sub["fired"]]
        if fired.empty:
            return None
        out.append(float(fired["excess"].median()))
    return out[0], out[1]


# --- 面板 ---------------------------------------------------------------


def build_panel(
    signal: Signal, frames: Sequence[SymbolFrame], benchmarks: Mapping[str, pd.Series],
    *, market: str, horizon: int,
) -> pd.DataFrame:
    """回傳 (date, symbol, fired, excess) 長表，已剔除沒有結果的列。"""
    benchmark = benchmarks.get(market)
    if benchmark is None:
        return pd.DataFrame(columns=["date", "symbol", "fired", "excess"])

    parts = []
    for frame in frames:
        if frame.market != market or market not in signal.markets:
            continue
        df = frame.df
        dates = df["date"].to_numpy()
        adj = pd.Series(df["adjclose"].to_numpy(dtype=float), index=dates)
        parts.append(pd.DataFrame({
            "date": dates,
            "symbol": frame.symbol,
            "fired": signal.fires(df).to_numpy(),
            "excess": forward_excess(adj, benchmark, horizon).to_numpy(),
        }))

    if not parts:
        return pd.DataFrame(columns=["date", "symbol", "fired", "excess"])
    return pd.concat(parts, ignore_index=True).dropna(subset=["excess"])


# --- Block bootstrap ----------------------------------------------------


def _bootstrap_difference(
    panel: pd.DataFrame, *, horizon: int, n_boot: int, seed: int,
) -> np.ndarray:
    """(觸發組 − 未觸發組) 平均超額報酬的 bootstrap 分布。

    重抽的單位是**日期區塊**，不是個別觀測值 —— 同一天所有標的一起動，
    逐列重抽會把橫斷面相關性當成獨立資訊，把信賴區間縮得不合理地窄。
    區塊長度取 horizon，讓區塊內的重疊留在區塊內。

    先把每個區塊聚合成 (總和, 個數)，再向量化重抽。逐次 gather 原始列
    在真實資料（102 檔 × 700 根）上會慢到不能用。
    """
    dates = np.sort(panel["date"].unique())
    n_dates = dates.size
    if n_dates == 0:
        return np.empty(0)

    position = panel["date"].map({d: i for i, d in enumerate(dates)}).to_numpy()
    block_of = position // max(1, horizon)
    n_blocks = int(block_of.max()) + 1

    fired = panel["fired"].to_numpy(dtype=bool)
    excess = panel["excess"].to_numpy(dtype=float)

    fired_sum = np.bincount(block_of[fired], weights=excess[fired], minlength=n_blocks)
    fired_n = np.bincount(block_of[fired], minlength=n_blocks).astype(float)
    other_sum = np.bincount(block_of[~fired], weights=excess[~fired], minlength=n_blocks)
    other_n = np.bincount(block_of[~fired], minlength=n_blocks).astype(float)

    rng = np.random.default_rng(seed)
    picks = rng.integers(0, n_blocks, size=(n_boot, n_blocks))

    fs, fn = fired_sum[picks].sum(axis=1), fired_n[picks].sum(axis=1)
    os_, on = other_sum[picks].sum(axis=1), other_n[picks].sum(axis=1)

    valid = (fn > 0) & (on > 0)
    diff = np.full(n_boot, np.nan)
    diff[valid] = fs[valid] / fn[valid] - os_[valid] / on[valid]
    return diff


# --- 主入口 -------------------------------------------------------------


def _empty_rate(signal_key: str, market: str, horizon: int, n_fires: int, n_eff: int) -> BaseRate:
    nan = float("nan")
    return BaseRate(
        signal_key=signal_key, market=market, horizon=horizon,
        n_fires=n_fires, n_effective=n_eff,
        hit_rate=nan, baseline_hit_rate=nan, median_excess=nan,
        ci_low=nan, ci_high=nan, p_value=nan, p_adjusted=nan,
        first_half_excess=nan, second_half_excess=nan,
        first_half_median=nan, second_half_median=nan,
        verdict="insufficient",
    )


def evaluate(
    signal: Signal, frames: Sequence[SymbolFrame], benchmarks: Mapping[str, pd.Series],
    *, market: str, horizon: int, n_boot: int = N_BOOT, seed: int = 0,
    p_adjusted: float | None = None,
) -> BaseRate:
    """量測單一 (訊號, 市場, 期間) 的基準率。

    `p_adjusted` 留給 `evaluate_all` 在跨 registry 校正後回填；
    單獨呼叫時退回未校正的 p 值。
    """
    panel = build_panel(signal, frames, benchmarks, market=market, horizon=horizon)
    if panel.empty:
        return _empty_rate(signal.key, market, horizon, 0, 0)

    calendar = list(np.sort(panel["date"].unique()))
    fired = panel[panel["fired"]]
    others = panel[~panel["fired"]]
    n_fires = int(len(fired))
    n_eff = n_effective(fired["date"].tolist(), calendar, horizon)

    if n_fires == 0 or others.empty:
        return _empty_rate(signal.key, market, horizon, n_fires, n_eff)

    hit_rate = float((fired["excess"] > 0).mean())
    baseline = float((others["excess"] > 0).mean())
    median_excess = float(fired["excess"].median())

    diff = _bootstrap_difference(panel, horizon=horizon, n_boot=n_boot, seed=seed)
    usable = diff[np.isfinite(diff)]
    if usable.size == 0:
        return _empty_rate(signal.key, market, horizon, n_fires, n_eff)

    ci_low, ci_high = (float(x) for x in np.percentile(usable, CI_PERCENTILES))
    # 兩側 bootstrap p 值：與 CI 用同一個分布，兩者不會互相矛盾
    p_value = min(1.0, 2.0 * min(
        float((usable <= 0).mean()), float((usable >= 0).mean()),
    ))
    p_adj = p_value if p_adjusted is None else p_adjusted

    nan = float("nan")
    halves = half_period_excess(panel)
    first, second = halves if halves else (nan, nan)
    stable = None if halves is None else (first > 0 and second > 0)

    medians = half_period_median(panel)
    first_med, second_med = medians if medians else (nan, nan)
    beats_index = None if medians is None else (first_med > 0 and second_med > 0)

    return BaseRate(
        signal_key=signal.key, market=market, horizon=horizon,
        n_fires=n_fires, n_effective=n_eff,
        hit_rate=hit_rate, baseline_hit_rate=baseline, median_excess=median_excess,
        ci_low=ci_low, ci_high=ci_high, p_value=p_value, p_adjusted=p_adj,
        first_half_excess=first, second_half_excess=second,
        first_half_median=first_med, second_half_median=second_med,
        verdict=verdict(
            n_effective=n_eff, p_adjusted=p_adj, ci_low=ci_low,
            hit_rate=hit_rate, baseline_hit_rate=baseline,
            stable=stable, beats_index=beats_index,
        ),
    )


def load_frames(conn) -> tuple[list[SymbolFrame], dict[str, pd.Series]]:
    """從資料庫載入指標表與基準序列。

    基準本身（0050.TW、SPY）從候選中排除 —— 它們對自己的超額報酬恆為 0，
    留在裡面只會在未觸發組製造一批必然為 0 的觀測值，把基準命中率往下拉，
    讓每個訊號看起來都贏過基準。
    """
    benchmark_symbols = set(BENCHMARKS.values())
    stocks = pd.read_sql_query("SELECT symbol, market FROM stocks", conn)
    prices = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, adjclose, volume"
        " FROM prices ORDER BY symbol, date", conn
    )
    if stocks.empty or prices.empty:
        return [], {}

    market_of = dict(zip(stocks["symbol"], stocks["market"], strict=False))

    benchmarks: dict[str, pd.Series] = {}
    for market, symbol in BENCHMARKS.items():
        rows = prices[prices["symbol"] == symbol]
        if not rows.empty:
            benchmarks[market] = rows.set_index("date")["adjclose"].astype(float)

    frames: list[SymbolFrame] = []
    for symbol, group in prices.groupby("symbol", sort=False):
        market = market_of.get(symbol)
        if market is None or symbol in benchmark_symbols:
            continue
        frames.append(SymbolFrame(
            symbol=symbol, market=market,
            df=features.add_indicators(group.reset_index(drop=True)),
        ))
    return frames, benchmarks


def evaluate_all(
    signals: Sequence[Signal], frames: Sequence[SymbolFrame],
    benchmarks: Mapping[str, pd.Series], *, markets: Sequence[str] = ("TW", "US"),
    horizons: Sequence[int] = HORIZONS, n_boot: int = N_BOOT, seed: int = 0,
    progress=None,
) -> list[BaseRate]:
    """量測整個 registry，然後跨全部檢定做一次 BH 校正並重判 verdict。

    校正必須在所有檢定跑完之後才能做 —— 這是它不能塞在 `evaluate`
    裡面的原因，也是為什麼「某個訊號有優勢」這句話在單獨看一個訊號時
    根本無法成立。
    """
    first_pass: list[BaseRate] = []
    for signal in signals:
        for market in markets:
            if market not in signal.markets:
                continue
            for horizon in horizons:
                if progress:
                    progress(f"  量測 {signal.key} / {market} / {horizon}日")
                first_pass.append(evaluate(
                    signal, frames, benchmarks, market=market, horizon=horizon,
                    n_boot=n_boot, seed=seed,
                ))

    testable = [r for r in first_pass if is_finite(r.p_value)]
    adjusted = benjamini_hochberg([r.p_value for r in testable])
    by_id = {
        (r.signal_key, r.market, r.horizon): p
        for r, p in zip(testable, adjusted, strict=True)
    }

    out = []
    for rate in first_pass:
        p_adj = by_id.get((rate.signal_key, rate.market, rate.horizon))
        out.append(rate if p_adj is None else _with_adjusted(rate, p_adj))
    return out


def _with_adjusted(rate: BaseRate, p_adjusted: float) -> BaseRate:
    return BaseRate(
        signal_key=rate.signal_key, market=rate.market, horizon=rate.horizon,
        n_fires=rate.n_fires, n_effective=rate.n_effective,
        hit_rate=rate.hit_rate, baseline_hit_rate=rate.baseline_hit_rate,
        median_excess=rate.median_excess,
        ci_low=rate.ci_low, ci_high=rate.ci_high,
        p_value=rate.p_value, p_adjusted=p_adjusted,
        first_half_excess=rate.first_half_excess,
        second_half_excess=rate.second_half_excess,
        first_half_median=rate.first_half_median,
        second_half_median=rate.second_half_median,
        verdict=verdict(
            n_effective=rate.n_effective, p_adjusted=p_adjusted, ci_low=rate.ci_low,
            hit_rate=rate.hit_rate, baseline_hit_rate=rate.baseline_hit_rate,
            stable=_both_positive(rate.first_half_excess, rate.second_half_excess),
            beats_index=_both_positive(rate.first_half_median, rate.second_half_median),
        ),
    )


def _both_positive(a: float, b: float) -> bool | None:
    if not is_finite(a) or not is_finite(b):
        return None
    return a > 0 and b > 0
