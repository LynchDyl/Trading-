"""Day-trading engine: bracket-order simulation on intraday bars.

Every trade is a bracket with reward:risk fixed at 2:1 by construction:
    target = entry + 2 x (entry - stop)   (mirrored for shorts)
Positions never survive the session — anything still open is closed on the
last bar before 16:00 ET.

Fill rules are conservative:
- entries trigger intrabar at the breakout level (or the bar open if it
  gapped through the level);
- if a bar spans both stop and target, the STOP is assumed to fill first;
- on the entry bar itself only the stop is checked, never the target.

Strategy families (the classic, extensively studied day-trade edges):
- Opening Range Breakout (ORB) — Zarattini & Aziz style, with an optional
  relative-volume filter;
- Gap-and-go — continuation after a large overnight gap;
- VWAP pullback — join an established intraday trend at VWAP.
"""
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

INTRADAY_DIR = Path(__file__).resolve().parents[1] / "data" / "intraday"
NY = "America/New_York"

RR = 2.0                      # reward:risk, fixed by construction
COST_PCT = 0.0006             # round-trip spread+slippage (0.06%)
LAST_ENTRY = "15:00"          # no new entries after this (ET)
FORCED_EXIT = "15:55"         # flatten by this bar (ET)


# ---------------------------------------------------------------- data ----

def load_intraday(symbol: str, interval: str = "5m") -> pd.DataFrame | None:
    path = INTRADAY_DIR / f"{symbol}_{interval}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["Datetime"], index_col="Datetime")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(NY)
    df = df.between_time("09:30", "15:59")
    return df if len(df) else None


def session_days(df: pd.DataFrame):
    for day, bars in df.groupby(df.index.date):
        if len(bars) >= 30:  # skip half days / partial sessions
            yield day, bars


# ------------------------------------------------------------- brackets ----

@dataclass
class Trade:
    symbol: str
    date: str
    strategy: str
    side: int                 # +1 long, -1 short
    entry_time: str
    entry: float
    stop: float
    target: float
    exit_time: str
    exit: float
    outcome: str              # 'target' | 'stop' | 'eod'
    pnl_pct: float = 0.0      # net of costs
    risk_pct: float = 0.0
    r_multiple: float = 0.0

    def finalize(self):
        gross = (self.exit / self.entry - 1.0) * self.side
        self.pnl_pct = gross - COST_PCT
        self.risk_pct = abs(self.entry - self.stop) / self.entry
        self.r_multiple = self.pnl_pct / self.risk_pct if self.risk_pct > 0 else 0.0
        return self


def simulate_bracket(bars: pd.DataFrame, i_entry: int, entry: float,
                     stop: float, side: int, symbol: str, day, strategy: str) -> Trade:
    target = entry + RR * (entry - stop) if side > 0 else entry - RR * (stop - entry)
    hi = bars["High"].to_numpy()
    lo = bars["Low"].to_numpy()
    op = bars["Open"].to_numpy()
    cl = bars["Close"].to_numpy()
    times = bars.index
    exit_deadline = times.indexer_between_time("09:30", FORCED_EXIT)[-1]

    # entry bar: stop can still take us out (conservative: never the target)
    if side > 0 and lo[i_entry] <= stop:
        return Trade(symbol, str(day), strategy, side, str(times[i_entry].time()),
                     entry, stop, target, str(times[i_entry].time()),
                     stop, "stop").finalize()
    if side < 0 and hi[i_entry] >= stop:
        return Trade(symbol, str(day), strategy, side, str(times[i_entry].time()),
                     entry, stop, target, str(times[i_entry].time()),
                     stop, "stop").finalize()

    for i in range(i_entry + 1, exit_deadline + 1):
        if side > 0:
            if op[i] <= stop or lo[i] <= stop:          # stop first, always
                px = min(op[i], stop)
                return Trade(symbol, str(day), strategy, side,
                             str(times[i_entry].time()), entry, stop, target,
                             str(times[i].time()), px, "stop").finalize()
            if hi[i] >= target:
                px = max(op[i], target) if op[i] >= target else target
                return Trade(symbol, str(day), strategy, side,
                             str(times[i_entry].time()), entry, stop, target,
                             str(times[i].time()), px, "target").finalize()
        else:
            if op[i] >= stop or hi[i] >= stop:
                px = max(op[i], stop)
                return Trade(symbol, str(day), strategy, side,
                             str(times[i_entry].time()), entry, stop, target,
                             str(times[i].time()), px, "stop").finalize()
            if lo[i] <= target:
                px = min(op[i], target) if op[i] <= target else target
                return Trade(symbol, str(day), strategy, side,
                             str(times[i_entry].time()), entry, stop, target,
                             str(times[i].time()), px, "target").finalize()

    i = exit_deadline
    return Trade(symbol, str(day), strategy, side, str(times[i_entry].time()),
                 entry, stop, target, str(times[i].time()), cl[i], "eod").finalize()


# ------------------------------------------------------------ strategies ----

def _range_bars(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    return bars.between_time("09:30", (pd.Timestamp("09:30") +
                                       pd.Timedelta(minutes=minutes - 1)).strftime("%H:%M"))


def orb_setup(bars: pd.DataFrame, prev: dict, range_mins: int = 30,
              stop_mode: str = "range", allow_short: bool = False,
              rvol_min: float = 0.0) -> tuple | None:
    """Opening range breakout. Returns (i_entry, entry, stop, side) or None."""
    rng = _range_bars(bars, range_mins)
    if len(rng) < max(1, range_mins // 10):
        return None
    r_hi, r_lo = rng["High"].max(), rng["Low"].min()
    if r_hi <= r_lo:
        return None
    if rvol_min > 0:
        base = prev.get("avg_range_vol")
        if base and base > 0 and rng["Volume"].sum() / base < rvol_min:
            return None
    after = bars[bars.index > rng.index[-1]]
    after = after.between_time("09:30", LAST_ENTRY)
    mid = (r_hi + r_lo) / 2.0
    for i_local, (ts, bar) in enumerate(after.iterrows()):
        i = bars.index.get_loc(ts)
        if bar["High"] > r_hi:
            entry = max(bar["Open"], r_hi)
            stop = r_lo if stop_mode == "range" else mid
            if entry > stop:
                return i, float(entry), float(stop), 1
            return None
        if allow_short and bar["Low"] < r_lo:
            entry = min(bar["Open"], r_lo)
            stop = r_hi if stop_mode == "range" else mid
            if entry < stop:
                return i, float(entry), float(stop), -1
            return None
    return None


def gap_go_setup(bars: pd.DataFrame, prev: dict, gap_min: float = 0.02,
                 allow_short: bool = True) -> tuple | None:
    """Continuation after an overnight gap >= gap_min, triggered by a break
    of the first-15-minute extreme in the gap direction."""
    prev_close = prev.get("close")
    if not prev_close:
        return None
    gap = bars["Open"].iloc[0] / prev_close - 1.0
    if abs(gap) < gap_min:
        return None
    side = 1 if gap > 0 else -1
    if side < 0 and not allow_short:
        return None
    rng = _range_bars(bars, 15)
    if rng.empty:
        return None
    r_hi, r_lo = rng["High"].max(), rng["Low"].min()
    after = bars[bars.index > rng.index[-1]].between_time("09:30", LAST_ENTRY)
    for ts, bar in after.iterrows():
        i = bars.index.get_loc(ts)
        if side > 0 and bar["High"] > r_hi:
            entry = max(bar["Open"], r_hi)
            if entry > r_lo:
                return i, float(entry), float(r_lo), 1
            return None
        if side < 0 and bar["Low"] < r_lo:
            entry = min(bar["Open"], r_lo)
            if entry < r_hi:
                return i, float(entry), float(r_hi), -1
            return None
    return None


def vwap_pullback_setup(bars: pd.DataFrame, prev: dict, confirm_mins: int = 30,
                        stop_atr: float = 1.0, allow_short: bool = False) -> tuple | None:
    """If the first `confirm_mins` close above VWAP (uptrend day), buy the
    first later touch of VWAP; stop = stop_atr x intraday ATR below entry."""
    tp = (bars["High"] + bars["Low"] + bars["Close"]) / 3.0
    cum_v = bars["Volume"].cumsum()
    vwap = (tp * bars["Volume"]).cumsum() / cum_v.replace(0, np.nan)
    rng = _range_bars(bars, confirm_mins)
    if rng.empty:
        return None
    i_confirm = len(rng) - 1
    tr = np.maximum(bars["High"] - bars["Low"],
                    (bars["High"] - bars["Close"].shift()).abs())
    atr = tr.rolling(14, min_periods=5).mean()
    up_day = bars["Close"].iloc[i_confirm] > vwap.iloc[i_confirm]
    side = 1 if up_day else -1
    if side < 0 and not allow_short:
        return None
    after = bars.iloc[i_confirm + 1:].between_time("09:30", LAST_ENTRY)
    for ts, bar in after.iterrows():
        i = bars.index.get_loc(ts)
        v = vwap.iloc[i]
        a = atr.iloc[i]
        if np.isnan(v) or np.isnan(a) or a <= 0:
            continue
        if side > 0 and bar["Low"] <= v and bar["Open"] > v:
            entry = float(v)
            return i, entry, entry - stop_atr * float(a), 1
        if side < 0 and bar["High"] >= v and bar["Open"] < v:
            entry = float(v)
            return i, entry, entry + stop_atr * float(a), -1
    return None


@dataclass(frozen=True)
class DayStrategySpec:
    name: str
    func: object
    grid: dict

    def combos(self):
        keys = list(self.grid)
        for values in product(*(self.grid[k] for k in keys)):
            yield dict(zip(keys, values))


DAY_STRATEGIES = {
    "orb": DayStrategySpec("orb", orb_setup, {
        "range_mins": [15, 30, 60],
        "stop_mode": ["range", "half"],
        "allow_short": [False, True],
        "rvol_min": [0.0, 1.5],
    }),
    "gap_go": DayStrategySpec("gap_go", gap_go_setup, {
        "gap_min": [0.015, 0.025, 0.04],
        "allow_short": [False, True],
    }),
    "vwap_pullback": DayStrategySpec("vwap_pullback", vwap_pullback_setup, {
        "confirm_mins": [30, 60],
        "stop_atr": [1.0, 1.5],
        "allow_short": [False, True],
    }),
}


# ------------------------------------------------------------ backtester ----

def run_symbol(symbol: str, df: pd.DataFrame, strategy: str, params: dict,
               date_from=None, date_to=None) -> list[Trade]:
    spec = DAY_STRATEGIES[strategy]
    trades = []
    prev: dict = {}
    range_mins = params.get("range_mins", 30)
    for day, bars in session_days(df):
        if (date_from and day < date_from) or (date_to and day >= date_to):
            # still update prev-day state so filters stay warm
            rngv = _range_bars(bars, range_mins)["Volume"].sum()
            _update_prev(prev, bars, rngv)
            continue
        setup = spec.func(bars, prev, **params)
        if setup is not None:
            i, entry, stop, side = setup
            if abs(entry - stop) / entry > 1e-5:
                trades.append(simulate_bracket(bars, i, entry, stop, side,
                                               symbol, day, strategy))
        rngv = _range_bars(bars, range_mins)["Volume"].sum()
        _update_prev(prev, bars, rngv)
    return trades


def _update_prev(prev: dict, bars: pd.DataFrame, range_vol: float):
    prev["close"] = float(bars["Close"].iloc[-1])
    prev["high"] = float(bars["High"].max())
    prev["low"] = float(bars["Low"].min())
    hist = prev.setdefault("range_vols", [])
    hist.append(float(range_vol))
    if len(hist) > 14:
        del hist[0]
    prev["avg_range_vol"] = sum(hist) / len(hist)


def pooled_metrics(trades: list[Trade]) -> dict:
    if not trades:
        return {"n_trades": 0}
    df = pd.DataFrame([t.__dict__ for t in trades])
    wins = df["pnl_pct"] > 0
    gains = df.loc[wins, "r_multiple"].sum()
    losses = -df.loc[~wins, "r_multiple"].sum()
    return {
        "n_trades": len(df),
        "win_rate": float(wins.mean()),
        "target_rate": float((df["outcome"] == "target").mean()),
        "stop_rate": float((df["outcome"] == "stop").mean()),
        "eod_rate": float((df["outcome"] == "eod").mean()),
        "expectancy_r": float(df["r_multiple"].mean()),
        "median_r": float(df["r_multiple"].median()),
        "profit_factor": float(gains / losses) if losses > 0 else np.inf,
        "avg_risk_pct": float(df["risk_pct"].mean()),
        "trades_per_day": float(len(df) / max(1, df["date"].nunique())),
    }
