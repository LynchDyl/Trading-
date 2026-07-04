"""Short-term trading strategies.

Each strategy takes an OHLCV DataFrame and parameters, and returns a pandas
Series of target positions (1 = long, 0 = flat, -1 = short), decided on each
bar's close. The backtester applies position[t] to the close-to-close return
of bar t+1, so there is no look-ahead.

The families covered are the classic, extensively studied short-term edges:

- RSI(2) mean reversion (Connors/Alvarez): buy oversold dips inside a
  long-term uptrend, exit into strength. Historically one of the most robust
  short-hold-period equity strategies.
- Double N-day low/high reversion (Connors "Double 7s").
- Bollinger band mean reversion.
- Donchian channel breakout (classic Turtle-style momentum, strong on gold).
- Moving-average crossover momentum.
- MACD momentum.
- Rate-of-change time-series momentum.
"""
from dataclasses import dataclass
from itertools import product
from typing import Callable

import numpy as np
import pandas as pd

from .indicators import atr, bollinger, donchian, ema, macd, roc, rsi, sma


def _state_positions(entries: np.ndarray, exits: np.ndarray) -> np.ndarray:
    """Long/flat state machine: enter on `entries`, leave on `exits`."""
    pos = np.zeros(len(entries), dtype=float)
    in_pos = False
    for i in range(len(entries)):
        if in_pos and exits[i]:
            in_pos = False
        elif not in_pos and entries[i]:
            in_pos = True
        pos[i] = 1.0 if in_pos else 0.0
    return pos


def rsi_reversion(df: pd.DataFrame, rsi_n: int = 2, entry: float = 10.0,
                  exit_level: float = 70.0, trend_n: int = 200) -> pd.Series:
    """Connors RSI(2): buy RSI < entry while above the long trend MA; exit on RSI > exit."""
    close = df["Close"]
    r = rsi(close, rsi_n)
    trend_ok = close > sma(close, trend_n) if trend_n else pd.Series(True, index=df.index)
    entries = ((r < entry) & trend_ok).to_numpy(na_value=False)
    exits = ((r > exit_level) | ~trend_ok).to_numpy(na_value=False)
    return pd.Series(_state_positions(entries, exits), index=df.index)


def double_low(df: pd.DataFrame, n: int = 7, trend_n: int = 200) -> pd.Series:
    """Connors Double-N: buy an N-day closing low in an uptrend, sell the N-day closing high."""
    close = df["Close"]
    trend_ok = close > sma(close, trend_n) if trend_n else pd.Series(True, index=df.index)
    n_low = close <= close.rolling(n, min_periods=n).min()
    n_high = close >= close.rolling(n, min_periods=n).max()
    entries = (n_low & trend_ok).to_numpy(na_value=False)
    exits = (n_high | ~trend_ok).to_numpy(na_value=False)
    return pd.Series(_state_positions(entries, exits), index=df.index)


def bollinger_reversion(df: pd.DataFrame, n: int = 20, k: float = 2.0,
                        trend_n: int = 200) -> pd.Series:
    """Buy a close below the lower band (in an uptrend), exit at the middle band."""
    close = df["Close"]
    lower, mid, _ = bollinger(close, n, k)
    trend_ok = close > sma(close, trend_n) if trend_n else pd.Series(True, index=df.index)
    entries = ((close < lower) & trend_ok).to_numpy(na_value=False)
    exits = ((close > mid) | ~trend_ok).to_numpy(na_value=False)
    return pd.Series(_state_positions(entries, exits), index=df.index)


def donchian_breakout(df: pd.DataFrame, entry_n: int = 20, exit_n: int = 10,
                      allow_short: bool = False) -> pd.Series:
    """Turtle-style channel breakout: long above the entry_n-bar high, exit below the exit_n-bar low."""
    entry_hi, entry_lo = donchian(df, entry_n)
    exit_hi, exit_lo = donchian(df, exit_n)
    close = df["Close"].to_numpy()
    e_hi, e_lo = entry_hi.to_numpy(), entry_lo.to_numpy()
    x_hi, x_lo = exit_hi.to_numpy(), exit_lo.to_numpy()
    pos = np.zeros(len(df))
    state = 0
    for i in range(len(df)):
        if np.isnan(e_hi[i]) or np.isnan(x_lo[i]):
            pos[i] = 0.0
            continue
        if state == 1 and close[i] < x_lo[i]:
            state = 0
        elif state == -1 and close[i] > x_hi[i]:
            state = 0
        if state == 0:
            if close[i] > e_hi[i]:
                state = 1
            elif allow_short and close[i] < e_lo[i]:
                state = -1
        pos[i] = state
    return pd.Series(pos, index=df.index)


def ma_cross(df: pd.DataFrame, fast: int = 10, slow: int = 50,
             use_ema: bool = True, allow_short: bool = False) -> pd.Series:
    """Momentum: long while the fast MA is above the slow MA."""
    f = (ema if use_ema else sma)(df["Close"], fast)
    s = (ema if use_ema else sma)(df["Close"], slow)
    pos = pd.Series(np.nan, index=df.index)
    pos[f > s] = 1.0
    pos[f <= s] = -1.0 if allow_short else 0.0
    pos[f.isna() | s.isna()] = 0.0
    return pos


def macd_momentum(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                  signal: int = 9, allow_short: bool = False) -> pd.Series:
    """Long while the MACD line is above its signal line."""
    line, sig = macd(df["Close"], fast, slow, signal)
    pos = pd.Series(np.nan, index=df.index)
    pos[line > sig] = 1.0
    pos[line <= sig] = -1.0 if allow_short else 0.0
    pos[line.isna() | sig.isna()] = 0.0
    return pos


def tsmom(df: pd.DataFrame, lookback: int = 60, trend_n: int = 0,
          allow_short: bool = False) -> pd.Series:
    """Time-series momentum: long when the lookback return is positive."""
    r = roc(df["Close"], lookback)
    trend_ok = (df["Close"] > sma(df["Close"], trend_n)) if trend_n else pd.Series(True, index=df.index)
    pos = pd.Series(0.0, index=df.index)
    pos[(r > 0) & trend_ok] = 1.0
    if allow_short:
        pos[(r < 0) & ~trend_ok if trend_n else (r < 0)] = -1.0
    pos[r.isna()] = 0.0
    return pos


def buy_hold(df: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=df.index)


@dataclass(frozen=True)
class StrategySpec:
    name: str
    func: Callable
    grid: dict  # param name -> list of values

    def combos(self):
        keys = list(self.grid)
        for values in product(*(self.grid[k] for k in keys)):
            yield dict(zip(keys, values))


STRATEGIES: dict[str, StrategySpec] = {
    "rsi_reversion": StrategySpec(
        "rsi_reversion", rsi_reversion,
        {"rsi_n": [2, 3, 4], "entry": [5, 10, 15, 20], "exit_level": [60, 70, 80],
         "trend_n": [100, 200]},
    ),
    "double_low": StrategySpec(
        "double_low", double_low,
        {"n": [5, 7, 10], "trend_n": [100, 200]},
    ),
    "bollinger_reversion": StrategySpec(
        "bollinger_reversion", bollinger_reversion,
        {"n": [10, 20], "k": [1.5, 2.0, 2.5], "trend_n": [100, 200]},
    ),
    "donchian_breakout": StrategySpec(
        "donchian_breakout", donchian_breakout,
        {"entry_n": [10, 20, 55], "exit_n": [5, 10, 20], "allow_short": [False, True]},
    ),
    "ma_cross": StrategySpec(
        "ma_cross", ma_cross,
        {"fast": [5, 10, 20], "slow": [20, 50, 100], "use_ema": [True, False],
         "allow_short": [False, True]},
    ),
    "macd_momentum": StrategySpec(
        "macd_momentum", macd_momentum,
        {"fast": [5, 8, 12], "slow": [17, 26, 35], "signal": [5, 9],
         "allow_short": [False, True]},
    ),
    "tsmom": StrategySpec(
        "tsmom", tsmom,
        {"lookback": [10, 20, 60, 120], "trend_n": [0, 200], "allow_short": [False, True]},
    ),
}


def positions(strategy: str, df: pd.DataFrame, params: dict) -> pd.Series:
    spec = STRATEGIES[strategy]
    return spec.func(df, **params).astype(float)
