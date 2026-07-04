"""Momentum-rotation portfolio engine with pound-cost-averaging contributions.

The strategy family ("dual momentum" / tactical asset allocation):
- at each rebalance (monthly or weekly), rank the universe by trailing
  total-return momentum;
- hold the top N assets equal-weighted, but only those passing an
  absolute-momentum/trend filter (price above its 10-month SMA);
- capital not allocated to risk assets goes to a defensive sleeve
  (7-10y treasuries, or the best of treasuries/cash/gold by momentum).

Execution model: weights are decided on the rebalance day's close using
only data up to that close, and take effect the next trading day.
Costs are charged on turnover. Contributions (e.g. GBP 50 every Monday)
buy into the current allocation with no look-ahead.

GBP note: assets are USD-quoted; the simulation ignores GBP/USD moves
(equivalent to measuring returns in USD terms).
"""
from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd

from . import data

TRADING_DAYS = 252
COST = 0.0010                  # per unit turnover
TREND_SMA = 210                # ~10 months

CORE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "VNQ", "GLD", "SLV", "TLT", "DBC"]
AGGRESSIVE = ["QQQ", "SSO", "TQQQ", "UPRO", "GLD", "BTC"]
DEFENSIVE_POOL = ["IEF", "SHY", "GLD"]

UNIVERSES = {"core": CORE, "aggressive": AGGRESSIVE}


def build_panel(names: list[str]) -> pd.DataFrame:
    """Close-price panel aligned to SPY's trading calendar."""
    spy = data.load("SPY")
    panel = pd.DataFrame(index=spy.index)
    for n in names:
        try:
            panel[n] = data.load(n)["Close"]
        except FileNotFoundError:
            pass
    return panel.ffill(limit=3)


def momentum_scores(panel: pd.DataFrame, lookback) -> pd.DataFrame:
    if lookback == "blend":
        parts = [panel.pct_change(n) for n in (63, 126, 252)]
        return sum(parts) / 3.0
    return panel.pct_change(int(lookback))


@dataclass
class PortfolioResult:
    returns: pd.Series            # daily net time-weighted returns
    weights: pd.DataFrame         # effective daily weights
    balance: pd.Series            # DCA account balance (contributions added)
    contributed: pd.Series        # cumulative contributions
    metrics: dict


def target_weights(panel: pd.DataFrame, universe: list[str], lookback,
                   top_n: int, trend_filter: bool, defensive: str,
                   rebalance: str) -> pd.DataFrame:
    """Weight matrix decided at each rebalance close (effective next day)."""
    cols = [c for c in set(universe + DEFENSIVE_POOL) if c in panel.columns]
    px = panel[cols]
    scores = momentum_scores(px, lookback)
    sma = px.rolling(TREND_SMA, min_periods=TREND_SMA).mean()
    # rebalance dates: first trading day of month, or every Monday
    if rebalance == "M":
        is_reb = pd.Series(px.index.to_period("M"), index=px.index).diff() != 0
    else:
        is_reb = pd.Series(px.index.isocalendar().week.astype(int),
                           index=px.index).diff().fillna(1) != 0
    live_uni = [c for c in universe if c in px.columns]
    reb = px.index[is_reb.to_numpy()]
    s = scores.loc[reb, live_uni]
    if trend_filter:
        above = px.loc[reb, live_uni] > sma.loc[reb, live_uni]
        s = s.where(above & (s > 0))
    ranks = s.rank(axis=1, ascending=False, method="first")
    picks = (ranks <= top_n).astype(float) / top_n
    w = pd.DataFrame(0.0, index=reb, columns=px.columns)
    w[live_uni] = picks.fillna(0.0)
    residual = (1.0 - w.sum(axis=1)).clip(lower=0.0)
    def_cols = [c for c in DEFENSIVE_POOL if c in px.columns]
    if defensive == "best_def" and def_cols:
        d_scores = scores.loc[reb, def_cols]
        valid = d_scores.notna().any(axis=1)
        d_best = d_scores.loc[valid].idxmax(axis=1).reindex(reb)
        for c in def_cols:
            w[c] += residual.where(d_best == c, 0.0).fillna(0.0)
    elif defensive in px.columns:
        w[defensive] += residual
    w = w.reindex(px.index)
    return w.ffill().fillna(0.0)


def run(panel: pd.DataFrame, weights: pd.DataFrame, contribution: float = 50.0,
        start_capital: float = 0.0, cost: float = COST,
        date_from=None, date_to=None) -> PortfolioResult:
    px = panel[weights.columns]
    rets = px.pct_change().fillna(0.0)
    w_eff = weights.shift(1).fillna(0.0)              # effective next day
    port_ret = (w_eff * rets).sum(axis=1)
    turnover = (weights - w_eff).abs().sum(axis=1)    # trade at close of reb day
    port_ret = port_ret - turnover.shift(1).fillna(0.0) * cost
    if date_from:
        port_ret = port_ret.loc[str(date_from):]
    if date_to:
        port_ret = port_ret.loc[:str(date_to)]
    idx = port_ret.index
    # weekly contributions on the first trading day of each ISO week
    week = pd.Series(idx.isocalendar().week.astype(int) +
                     idx.isocalendar().year.astype(int) * 100, index=idx)
    contrib = (week.diff().fillna(1) != 0).astype(float) * contribution
    bal = np.empty(len(idx))
    b = start_capital
    r = port_ret.to_numpy()
    c = contrib.to_numpy()
    for i in range(len(idx)):
        b = b * (1.0 + r[i]) + c[i]
        bal[i] = b
    balance = pd.Series(bal, index=idx)
    contributed = contrib.cumsum()
    res = PortfolioResult(port_ret, w_eff.loc[idx], balance, contributed, {})
    res.metrics = portfolio_metrics(port_ret, balance, contributed)
    return res


def portfolio_metrics(returns: pd.Series, balance: pd.Series,
                      contributed: pd.Series) -> dict:
    n = len(returns)
    if n < 30:
        return {}
    eq = (1.0 + returns).cumprod()
    years = n / TRADING_DAYS
    cagr = eq.iloc[-1] ** (1 / years) - 1 if eq.iloc[-1] > 0 else np.nan
    sd = returns.std(ddof=0)
    sharpe = returns.mean() / sd * np.sqrt(TRADING_DAYS) if sd > 0 else 0.0
    dd = eq / eq.cummax() - 1.0
    monthly = (1.0 + returns).resample("ME").prod() - 1.0
    monthly = monthly[monthly.index >= returns.index[0] + pd.Timedelta(days=25)]
    profit = balance.iloc[-1] - contributed.iloc[-1]
    max_dd = float(dd.min())
    return {
        "cagr": float(cagr),
        "sharpe": float(sharpe),
        "calmar": float(cagr / abs(max_dd)) if max_dd < 0 else np.nan,
        "max_drawdown": max_dd,
        "avg_month": float(monthly.mean()),
        "median_month": float(monthly.median()),
        "pct_positive_months": float((monthly > 0).mean()),
        "worst_month": float(monthly.min()),
        "best_month": float(monthly.max()),
        "final_balance": float(balance.iloc[-1]),
        "total_contributed": float(contributed.iloc[-1]),
        "profit": float(profit),
        "years": float(years),
    }


@dataclass(frozen=True)
class RotationSpec:
    grid = {
        "universe": ["core", "aggressive"],
        "lookback": [63, 126, 252, "blend"],
        "top_n": [1, 2, 3],
        "trend_filter": [True, False],
        "defensive": ["IEF", "best_def"],
        "rebalance": ["M", "W"],
    }

    def combos(self):
        keys = list(self.grid)
        for values in product(*(self.grid[k] for k in keys)):
            yield dict(zip(keys, values))
