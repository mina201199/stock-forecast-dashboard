"""軌道一：LightGBM 分位數預測（spec 6.1）。

標籤是**超額報酬**而非絕對報酬：

    y = 個股未來 N 日報酬 − 基準同期報酬

絕對報酬有八成以上由大盤 beta 主導，模型會退化成「猜大盤」——
那是猜不到的，硬做只會得到一個看似有用、實際在噪音裡打轉的模型。
改用超額報酬後，模型專注於橫斷面的相對強弱，這才是特徵能解釋的部分。

輸出是三條分位數（q10/q50/q90）而非單點。單點預測會給出虛假的精確感。
"""
from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd

QUANTILES = (0.1, 0.5, 0.9)

_LGB_PARAMS = {
    "objective": "quantile",
    "metric": "quantile",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbose": -1,
    "num_threads": 0,
}


def forward_return(adjclose: pd.Series, horizon: int) -> pd.Series:
    """未來 N 日報酬。

    這個函式**刻意**看向未來 —— 它產生的是標籤，絕不可放進特徵。
    最後 N 列必然為 NaN（沒有未來可看），訓練時會被丟棄。
    """
    return adjclose.shift(-horizon) / adjclose - 1


def build_labels(
    panel: pd.DataFrame, benchmark: pd.DataFrame, horizon: int
) -> pd.Series:
    """對每檔算超額報酬。

    panel 需含 symbol/date/adjclose，benchmark 需含 date/adjclose 且
    每個 market 一條。以 date 對齊，缺基準的日期不產生標籤。
    """
    stock_fwd = panel.groupby("symbol", group_keys=False)["adjclose"].apply(
        lambda s: forward_return(s, horizon)
    )
    bench_fwd = (
        benchmark.assign(bench_fwd=lambda d: d.groupby("market", group_keys=False)["adjclose"]
                         .apply(lambda s: forward_return(s, horizon)))
        [["market", "date", "bench_fwd"]]
    )
    merged = panel[["market", "date"]].merge(bench_fwd, on=["market", "date"], how="left")
    return pd.Series(stock_fwd.to_numpy() - merged["bench_fwd"].to_numpy(), index=panel.index)


@dataclass(frozen=True)
class Split:
    train: np.ndarray
    valid: np.ndarray
    test: np.ndarray


def purged_split(
    dates: pd.Series, horizon: int, train_frac: float = 0.70, valid_frac: float = 0.15
) -> Split:
    """時序切分並在訓練集尾端 purge。

    禁止隨機切分。訓練集最後 `horizon` 個交易日的樣本，其未來窗口會伸進
    驗證期，不 purge 就會洩漏 —— 而且洩漏後的回測會漂亮到不真實。
    """
    unique = np.array(sorted(pd.Series(dates).dropna().unique()))
    n = len(unique)
    train_end = int(n * train_frac)
    valid_end = int(n * (train_frac + valid_frac))

    purged_train_end = max(train_end - horizon, 0)
    train_dates = set(unique[:purged_train_end])
    valid_dates = set(unique[train_end:valid_end])
    test_dates = set(unique[valid_end:])

    values = pd.Series(dates).to_numpy()
    return Split(
        train=np.isin(values, list(train_dates)),
        valid=np.isin(values, list(valid_dates)),
        test=np.isin(values, list(test_dates)),
    )


def train_quantile_models(
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_valid: pd.DataFrame,
    y_valid: pd.Series,
    *,
    num_boost_round: int = 400,
    early_stopping: int = 50,
) -> dict[float, lgb.Booster]:
    models: dict[float, lgb.Booster] = {}
    for alpha in QUANTILES:
        params = {**_LGB_PARAMS, "alpha": alpha}
        train_set = lgb.Dataset(x_train, label=y_train, free_raw_data=False)
        valid_set = lgb.Dataset(x_valid, label=y_valid, reference=train_set,
                                free_raw_data=False)
        models[alpha] = lgb.train(
            params,
            train_set,
            num_boost_round=num_boost_round,
            valid_sets=[valid_set],
            callbacks=[lgb.early_stopping(early_stopping, verbose=False),
                       lgb.log_evaluation(0)],
        )
    return models


def predict(models: dict[float, lgb.Booster], features: pd.DataFrame) -> pd.DataFrame:
    """回傳 q10/q50/q90。

    分位數模型各自獨立訓練，理論上可能交叉（q10 > q90）。
    交叉代表該樣本落在模型不確定的區域，直接排序即可，
    保留原始順序反而會讓下游算出負寬度的區間。
    """
    raw = np.column_stack([models[a].predict(features) for a in QUANTILES])
    ordered = np.sort(raw, axis=1)
    return pd.DataFrame(
        {"tech_q10": ordered[:, 0], "tech_q50": ordered[:, 1], "tech_q90": ordered[:, 2]},
        index=features.index,
    )


def information_coefficient(predicted: pd.Series, actual: pd.Series) -> float | None:
    """Spearman 秩相關。橫斷面預測能力的標準指標。"""
    joined = pd.concat([predicted, actual], axis=1).dropna()
    if len(joined) < 30:
        return None
    return float(joined.iloc[:, 0].corr(joined.iloc[:, 1], method="spearman"))


def quintile_returns(predicted: pd.Series, actual: pd.Series, groups: int = 5) -> list[float]:
    """依預測值分組後各組的平均實際報酬。有效的模型應呈單調遞增。"""
    joined = pd.concat([predicted.rename("p"), actual.rename("a")], axis=1).dropna()
    if len(joined) < groups * 10:
        return []
    joined["bucket"] = pd.qcut(joined["p"].rank(method="first"), groups, labels=False)
    return [float(v) for v in joined.groupby("bucket")["a"].mean().tolist()]
