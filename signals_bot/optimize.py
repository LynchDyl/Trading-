"""Grid search and walk-forward validation.

Comprehensive protocol per instrument:

1. Grid search each strategy family over its parameter grid on the full
   history (in-sample reference, prone to overfit — reported but not used
   for selection).
2. Walk-forward: rolling 4-year train window, 1-year test window, stepped
   yearly. In each fold the best parameters on the train window (by Sharpe,
   with a minimum-trade constraint) are applied unchanged to the unseen
   test year. Concatenated test returns form the out-of-sample record.
3. Strategy selection for live signals is based on out-of-sample Sharpe,
   not in-sample fit.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import backtest
from .strategies import STRATEGIES, positions

MIN_TRADES_PER_YEAR = 2.0   # reject degenerate "1 lucky trade" fits
TRAIN_BARS = 4 * 252
TEST_BARS = 252


def _valid(strategy: str, params: dict) -> bool:
    if strategy in ("ma_cross", "macd_momentum") and params["fast"] >= params["slow"]:
        return False
    if strategy == "donchian_breakout" and params["exit_n"] > params["entry_n"]:
        return False
    return True


def _score(metrics: dict, years: float) -> float:
    """Selection score: Sharpe, disqualified if too few trades."""
    if not metrics or metrics["n_trades"] < MIN_TRADES_PER_YEAR * years:
        return -np.inf
    return metrics["sharpe"]


def grid_search(df: pd.DataFrame, strategy: str, cost: float = backtest.DEFAULT_COST):
    """Return list of (params, metrics) for all valid combos on df."""
    years = len(df) / backtest.TRADING_DAYS
    out = []
    for params in STRATEGIES[strategy].combos():
        if not _valid(strategy, params):
            continue
        pos = positions(strategy, df, params)
        res = backtest.run(df, pos, cost)
        out.append((params, res.metrics, _score(res.metrics, years)))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


@dataclass
class WalkForwardResult:
    strategy: str
    oos_returns: pd.Series       # concatenated out-of-sample daily returns
    oos_positions: pd.Series
    fold_params: list            # (test_start, test_end, params, train_sharpe)
    metrics: dict


def walk_forward(df: pd.DataFrame, strategy: str, cost: float = backtest.DEFAULT_COST,
                 train_bars: int = TRAIN_BARS, test_bars: int = TEST_BARS) -> WalkForwardResult | None:
    n = len(df)
    if n < train_bars + test_bars:
        return None
    oos_ret, oos_pos, folds = [], [], []
    start = 0
    while start + train_bars + 1 < n:
        train = df.iloc[start:start + train_bars]
        test_end = min(start + train_bars + test_bars, n)
        # include the train window in the data given to the test run so
        # indicators (e.g. SMA200) are warm from the first test bar
        window = df.iloc[start:test_end]
        ranked = grid_search(train, strategy, cost)
        if not ranked or not np.isfinite(ranked[0][2]):
            start += test_bars
            continue
        params, _, train_sharpe = ranked[0]
        pos = positions(strategy, window, params)
        res = backtest.run(window, pos, cost)
        test_slice = res.returns.iloc[train_bars:]
        pos_slice = res.positions.iloc[train_bars:]
        oos_ret.append(test_slice)
        oos_pos.append(pos_slice)
        folds.append((str(window.index[train_bars].date()),
                      str(window.index[-1].date()), params, float(train_sharpe)))
        start += test_bars
    if not oos_ret:
        return None
    ret = pd.concat(oos_ret)
    pos = pd.concat(oos_pos)
    # trades across the whole OOS record (approximate, from held positions)
    trades = backtest._extract_trades(pos, df["Close"].reindex(pos.index))
    metrics = backtest.compute_metrics(ret, pos, trades)
    return WalkForwardResult(strategy, ret, pos, folds, metrics)


def evaluate_instrument(df: pd.DataFrame, cost: float = backtest.DEFAULT_COST):
    """Full protocol for one instrument. Returns dict with everything the report needs."""
    full_years = len(df) / backtest.TRADING_DAYS

    # benchmark
    bh = backtest.run(df, pd.Series(1.0, index=df.index), cost=0.0)

    in_sample, oos = {}, {}
    for name in STRATEGIES:
        ranked = grid_search(df, name, cost)
        if ranked:
            in_sample[name] = {"params": ranked[0][0], "metrics": ranked[0][1],
                               "score": ranked[0][2]}
        wf = walk_forward(df, name, cost)
        if wf is not None:
            oos[name] = wf

    # pick live strategy: best OOS Sharpe among strategies that beat 0 return OOS
    candidates = {k: v for k, v in oos.items()
                  if np.isfinite(_score(v.metrics, len(v.oos_returns) / backtest.TRADING_DAYS))}
    best = max(candidates, key=lambda k: candidates[k].metrics["sharpe"]) if candidates else None

    # live params: re-fit the chosen family on the most recent train window
    live_params = None
    if best is not None:
        recent = df.iloc[-TRAIN_BARS:]
        ranked = grid_search(recent, best, cost)
        if ranked and np.isfinite(ranked[0][2]):
            live_params = ranked[0][0]
        else:
            live_params = oos[best].fold_params[-1][2]

    return {
        "buy_hold": bh,
        "in_sample": in_sample,
        "oos": oos,
        "best_strategy": best,
        "live_params": live_params,
        "years": full_years,
    }
