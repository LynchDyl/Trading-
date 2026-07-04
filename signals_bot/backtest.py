"""Vectorized backtester.

Execution model (no look-ahead):
- Signals are computed on bar t's close.
- The position taken at bar t's close earns bar t+1's close-to-close return.
- Costs are charged on turnover: |pos_t - pos_{t-1}| * cost_bps.
  Default 10 bps per unit of turnover (commission + slippage + spread),
  which is deliberately conservative for liquid names like NVDA/TSLA/GC.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TRADING_DAYS = 252
DEFAULT_COST = 0.0010  # 10 bps per unit turnover


@dataclass
class BacktestResult:
    returns: pd.Series          # daily strategy returns (net of costs)
    positions: pd.Series        # position held during each bar's return
    equity: pd.Series           # compounded equity curve, starts at 1.0
    trades: pd.DataFrame        # one row per round-trip
    metrics: dict = field(default_factory=dict)


def _extract_trades(pos: pd.Series, close: pd.Series) -> pd.DataFrame:
    """Round-trip trades from a position series (entry/exit at bar closes)."""
    rows = []
    p = pos.to_numpy()
    idx = pos.index
    c = close.to_numpy()
    cur = 0.0
    entry_i = None
    for i in range(len(p)):
        if p[i] != cur:
            if cur != 0.0 and entry_i is not None:
                ret = (c[i] / c[entry_i] - 1.0) * np.sign(cur)
                rows.append((idx[entry_i], idx[i], cur, c[entry_i], c[i], ret, i - entry_i))
            entry_i = i if p[i] != 0.0 else None
            cur = p[i]
    if cur != 0.0 and entry_i is not None and entry_i < len(p) - 1:
        ret = (c[-1] / c[entry_i] - 1.0) * np.sign(cur)
        rows.append((idx[entry_i], idx[-1], cur, c[entry_i], c[-1], ret, len(p) - 1 - entry_i))
    return pd.DataFrame(
        rows,
        columns=["entry_date", "exit_date", "direction", "entry_price", "exit_price",
                 "return", "bars_held"],
    )


def compute_metrics(returns: pd.Series, positions: pd.Series,
                    trades: pd.DataFrame) -> dict:
    n = len(returns)
    if n == 0:
        return {}
    equity = (1.0 + returns).cumprod()
    total = equity.iloc[-1] - 1.0
    years = n / TRADING_DAYS
    cagr = equity.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 and equity.iloc[-1] > 0 else np.nan
    vol = returns.std(ddof=0) * np.sqrt(TRADING_DAYS)
    sharpe = (returns.mean() / returns.std(ddof=0) * np.sqrt(TRADING_DAYS)
              if returns.std(ddof=0) > 0 else 0.0)
    downside = returns[returns < 0].std(ddof=0) * np.sqrt(TRADING_DAYS)
    sortino = returns.mean() * TRADING_DAYS / downside if downside and downside > 0 else np.nan
    dd = equity / equity.cummax() - 1.0
    max_dd = dd.min()
    calmar = cagr / abs(max_dd) if max_dd < 0 and not np.isnan(cagr) else np.nan
    exposure = float((positions != 0).mean())
    n_tr = len(trades)
    win_rate = float((trades["return"] > 0).mean()) if n_tr else np.nan
    gains = trades.loc[trades["return"] > 0, "return"].sum() if n_tr else 0.0
    losses = -trades.loc[trades["return"] < 0, "return"].sum() if n_tr else 0.0
    profit_factor = gains / losses if losses > 0 else np.inf if gains > 0 else np.nan
    return {
        "total_return": float(total),
        "cagr": float(cagr),
        "ann_vol": float(vol),
        "sharpe": float(sharpe),
        "sortino": float(sortino) if not pd.isna(sortino) else np.nan,
        "max_drawdown": float(max_dd),
        "calmar": float(calmar) if not pd.isna(calmar) else np.nan,
        "exposure": exposure,
        "n_trades": int(n_tr),
        "win_rate": win_rate,
        "profit_factor": float(profit_factor) if np.isfinite(profit_factor) else np.nan,
        "avg_trade_return": float(trades["return"].mean()) if n_tr else np.nan,
        "avg_bars_held": float(trades["bars_held"].mean()) if n_tr else np.nan,
        "trades_per_year": n_tr / years if years > 0 else np.nan,
    }


def run(df: pd.DataFrame, target_pos: pd.Series, cost: float = DEFAULT_COST) -> BacktestResult:
    """Backtest a target-position series over OHLCV data."""
    close = df["Close"]
    asset_ret = close.pct_change().fillna(0.0)
    held = target_pos.shift(1).fillna(0.0)          # position during bar t
    turnover = (held - held.shift(1).fillna(0.0)).abs()
    strat_ret = held * asset_ret - turnover * cost
    equity = (1.0 + strat_ret).cumprod()
    trades = _extract_trades(target_pos, close)
    res = BacktestResult(strat_ret, held, equity, trades)
    res.metrics = compute_metrics(strat_ret, held, trades)
    return res
