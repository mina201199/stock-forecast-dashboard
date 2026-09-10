"""資金計算機（spec §6）。

**界線寫在程式的形狀裡**

使用者設定規則，程式計算後果。它不挑股票、不建議比例、不評價該不該買。

最能體現這條界線的是 `build_plan`：候選標的超過使用者自訂的檔數上限時，
它**回報拒絕**而不是代為篩選（`rejected_reason`）。挑哪幾檔就是投資決策
本身，那個決定必須留給使用者。程式能誠實回答的是「按你的規則，
這樣配置會買到幾股、成本多少、這個組合的歷史波動與最大回撤是多少」。

**組合層的數字全部是歷史的，不是未來的。** 這 3 年沒發生過的情境
（2008、2020 那類）不會出現在裡面。這句話必須跟著數字一起出現。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src import risk
from src.numeric import is_finite, is_positive

# 台股：1 交易單位 = 1,000 股；零股另計
TW_LOT_SIZE = 1000
# 券商手續費 0.1425%，最低 20 元（賣出另有 0.3% 證交稅，本模組只算進場）
TW_FEE_RATE = 0.001425
TW_FEE_MIN = 20.0
# 美股按券商設定，多數零手續費
US_FEE_RATE = 0.0
US_FEE_MIN = 0.0

MIN_BARS_FOR_STATS = 60


@dataclass(frozen=True)
class Rules:
    capital: float
    max_per_position: float
    max_positions: int
    tranches: tuple[float, ...]
    cash_reserve: float = 0.0
    max_drawdown_tolerance: float | None = None
    fee_discount: float = 1.0

    def __post_init__(self) -> None:
        if not is_positive(self.capital):
            raise ValueError("本金必須是正數")
        if not self.tranches or abs(sum(self.tranches) - 1.0) > 1e-9:
            raise ValueError(f"每批比例總和必須是 1，收到 {sum(self.tranches)}")
        if any(t <= 0 for t in self.tranches):
            raise ValueError("每批比例必須都是正數")
        if not 0 <= self.cash_reserve < 1:
            raise ValueError("保留現金比例必須在 [0, 1) 之間")
        if self.max_positions < 1:
            raise ValueError("最多持有檔數必須至少 1")

    @property
    def investable(self) -> float:
        return self.capital * (1.0 - self.cash_reserve)


@dataclass(frozen=True)
class Candidate:
    symbol: str
    market: str
    price: float


@dataclass(frozen=True)
class Tranche:
    index: int
    ratio: float
    target_amount: float
    shares: int
    cost: float
    fee: float


@dataclass(frozen=True)
class Position:
    symbol: str
    market: str
    price: float
    target_amount: float
    tranches: tuple[Tranche, ...]
    total_shares: int
    lots: int
    odd_shares: int
    total_cost: float
    total_fee: float
    shortfall: float
    weight_of_capital: float


@dataclass(frozen=True)
class Plan:
    rules: Rules
    positions: tuple[Position, ...]
    invested: float
    cash_left: float
    rejected_reason: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def _fee(market: str, amount: float, discount: float) -> float:
    """進場手續費。**每批各自計費** —— 分批的成本必須看得見，
    小額分批時最低費會把實際費率放大好幾倍。"""
    if amount <= 0:
        return 0.0
    rate, floor = (TW_FEE_RATE, TW_FEE_MIN) if market == "TW" else (US_FEE_RATE, US_FEE_MIN)
    return max(floor, amount * rate * discount)


def _shares_for(market: str, budget: float, price: float, discount: float) -> int:
    """在**含手續費**的預算內買得到的股數。

    含費計算是必要的，不是講究：不含費的話手續費會從保留現金裡扣，
    「保留 10%」就變成謊言 —— 實測 500,000 本金保留 10%、單檔滿倉時，
    剩下的現金是 49,358 而不是 50,000。保留現金是硬底線。

    先用比例費反推，再處理最低費綁定的情形（小額時最低 20 元不是比例）。
    """
    if not is_positive(price) or budget <= 0:
        return 0
    rate, floor = (TW_FEE_RATE, TW_FEE_MIN) if market == "TW" else (US_FEE_RATE, US_FEE_MIN)

    shares = int((budget / (1.0 + rate * discount)) // price)
    if shares > 0 and shares * price + _fee(market, shares * price, discount) > budget:
        # 最低費綁定：改以「預算扣掉最低費」重估
        shares = int(max(0.0, budget - floor) // price)
    while shares > 0 and shares * price + _fee(market, shares * price, discount) > budget:
        shares -= 1
    return shares


def build_plan(rules: Rules, candidates: Sequence[Candidate]) -> Plan:
    """把規則與候選標的換算成具體股數與成本。

    不排序、不篩選。候選超過上限時回報拒絕（spec §6.3）。
    """
    notes: list[str] = []
    usable: list[Candidate] = []
    for c in candidates:
        if not is_positive(c.price):
            notes.append(f"{c.symbol}：沒有有效價格，已排除")
            continue
        usable.append(c)

    if len(usable) > rules.max_positions:
        return Plan(
            rules=rules, positions=(), invested=0.0, cash_left=rules.capital,
            rejected_reason=(
                f"候選 {len(usable)} 檔超過你設定的最多持有 {rules.max_positions} 檔。"
                f"請自行指定要哪 {rules.max_positions} 檔 —— "
                f"挑哪幾檔是投資決策，程式不代為篩選。"
            ),
            notes=tuple(notes),
        )

    if not usable:
        return Plan(rules=rules, positions=(), invested=0.0,
                    cash_left=rules.capital, notes=tuple(notes))

    per_position_cap = rules.capital * rules.max_per_position
    # 可投入金額平均分給候選檔數，但不得超過單檔上限
    even_split = rules.investable / len(usable)
    target = min(per_position_cap, even_split)

    positions: list[Position] = []
    for c in usable:
        tranches: list[Tranche] = []
        for i, ratio in enumerate(rules.tranches, start=1):
            slice_amount = target * ratio
            shares = _shares_for(c.market, slice_amount, c.price, rules.fee_discount)
            cost = shares * c.price
            tranches.append(Tranche(
                index=i, ratio=ratio, target_amount=slice_amount,
                shares=shares, cost=cost,
                fee=_fee(c.market, cost, rules.fee_discount),
            ))

        total_shares = sum(t.shares for t in tranches)
        if total_shares == 0:
            notes.append(
                f"{c.symbol}：股價 {c.price:,.2f} 高於單筆可投入金額，"
                f"連 1 股都買不到，已排除"
            )
            continue

        total_cost = sum(t.cost for t in tranches)
        positions.append(Position(
            symbol=c.symbol, market=c.market, price=c.price, target_amount=target,
            tranches=tuple(tranches), total_shares=total_shares,
            lots=total_shares // TW_LOT_SIZE if c.market == "TW" else 0,
            odd_shares=total_shares % TW_LOT_SIZE if c.market == "TW" else total_shares,
            total_cost=total_cost,
            total_fee=sum(t.fee for t in tranches),
            # 預算沒用掉的部分（含費之後）。整股化必然有剩，那個剩額要看得見。
            shortfall=target - total_cost - sum(t.fee for t in tranches),
            weight_of_capital=total_cost / rules.capital,
        ))

    invested = sum(p.total_cost + p.total_fee for p in positions)
    return Plan(
        rules=rules, positions=tuple(positions), invested=invested,
        cash_left=rules.capital - invested, notes=tuple(notes),
    )


# --- 組合層（描述性歷史數字）------------------------------------------


def portfolio_stats(
    weights: Mapping[str, float], history: pd.DataFrame, *, tolerance: float | None = None,
) -> dict:
    """組合的歷史波動、最大回撤與集中度。

    做法是先把加權日報酬合成一條權益曲線，再餵給 `risk.py` 既有的函式 ——
    複用而不重寫，讓個股與組合的風險定義保持一致。

    回傳的每一個數字都是歷史的。資料不足時回 None，不得回 0 —— 「沒有
    足夠歷史」和「回撤為零」是完全不同的兩件事（spec 9.4 的同一條教訓）。
    """
    wide = (
        history.pivot_table(index="date", columns="symbol", values="adjclose")
        .sort_index()
    )
    cols = [s for s in weights if s in wide.columns]
    empty = {
        "max_drawdown": None, "volatility": None, "avg_correlation": None,
        "exceeds_tolerance": None, "n_bars": 0,
    }
    if not cols:
        return empty

    # fill_method=None：缺值不得向前填補。填補會製造「那天沒動」的假報酬，
    # 把波動度與回撤都低估。
    returns = wide[cols].pct_change(fill_method=None).dropna(how="all")
    if len(returns) < MIN_BARS_FOR_STATS:
        return {**empty, "n_bars": len(returns)}

    total = sum(weights[s] for s in cols)
    if total <= 0:
        return {**empty, "n_bars": len(returns)}
    w = np.array([weights[s] / total for s in cols])

    portfolio_returns = returns[cols].fillna(0.0).to_numpy() @ w
    equity = pd.Series(100.0 * np.cumprod(1.0 + portfolio_returns), index=returns.index)

    drawdown = risk.max_drawdown(equity, window=len(equity))
    volatility = risk.annualized_volatility(equity, window=len(equity))

    avg_corr = None
    if len(cols) > 1:
        corr = returns[cols].corr().to_numpy()
        off_diagonal = corr[~np.eye(len(cols), dtype=bool)]
        if off_diagonal.size and np.isfinite(off_diagonal).any():
            avg_corr = float(np.nanmean(off_diagonal))
    elif len(cols) == 1:
        avg_corr = 1.0

    exceeds = None
    if is_finite(drawdown) and is_finite(tolerance):
        exceeds = bool(abs(float(drawdown)) > abs(float(tolerance)))

    return {
        "max_drawdown": float(drawdown) if is_finite(drawdown) else None,
        "volatility": float(volatility) if is_finite(volatility) else None,
        "avg_correlation": avg_corr,
        "exceeds_tolerance": exceeds,
        "n_bars": len(returns),
    }
