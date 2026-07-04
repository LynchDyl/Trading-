"""Mechanical implementation of TJR's day-trading playbook (smart-money
concepts / ICT family).

The published recipe, made rule-based so it can be backtested:

1. LIQUIDITY — mark the prior day's high/low (and optionally the first
   30 minutes' high/low) as resting liquidity.
2. SWEEP — wait for a 5m bar to wick THROUGH a level but close back on the
   original side (a "liquidity grab" / stop hunt).
3. MARKET STRUCTURE SHIFT (MSS) — after a bullish sweep of a low, require a
   later bar to CLOSE above the minor swing high formed going into the sweep
   (displacement in the reversal direction). Mirrored for shorts.
4. FAIR VALUE GAP (FVG) — inside the displacement leg, find a 3-bar
   imbalance (bar N's low above bar N-2's high for longs). Entry is a limit
   at the gap's upper edge or midpoint on the retrace.
5. BRACKET — stop at the swept extreme, target = 2x risk (the engine's
   fixed 2:1 reward:risk), flat by 15:55 ET. One trade per symbol per day.
6. TIMING — setups are only taken in the New York morning (entries armed
   between 10:00 and a cutoff), which is when TJR trades.

Honest caveats: TJR trades this discretionarily (he skips setups a
mechanical scan takes, and manages winners actively). This is a faithful
but rule-based approximation — the backtest measures the mechanical edge
of the pattern, not the trader.
"""
import numpy as np
import pandas as pd

from .daytrade import DAY_STRATEGIES, DayStrategySpec, _range_bars


def _find_fvg(hi: np.ndarray, lo: np.ndarray, start: int, end: int,
              side: int) -> tuple | None:
    """Most recent 3-bar imbalance inside [start, end], as (gap_lo, gap_hi)."""
    best = None
    for k in range(max(start + 2, 2), end + 1):
        if side > 0 and lo[k] > hi[k - 2]:
            best = (hi[k - 2], lo[k])
        elif side < 0 and hi[k] < lo[k - 2]:
            best = (hi[k], lo[k - 2])
    return best


def tjr_setup(bars: pd.DataFrame, prev: dict, liq: str = "prevday",
              window_end: str = "11:30", fvg_entry: str = "edge",
              allow_short: bool = True) -> tuple | None:
    """Returns (i_entry, entry, stop, side) or None."""
    if "low" not in prev or "high" not in prev:
        return None
    hi = bars["High"].to_numpy()
    lo = bars["Low"].to_numpy()
    cl = bars["Close"].to_numpy()
    op = bars["Open"].to_numpy()
    times = bars.index

    liq_lows = [prev["low"]]
    liq_highs = [prev["high"]]
    if liq == "both":
        rng = _range_bars(bars, 30)
        if len(rng):
            liq_lows.append(float(rng["Low"].min()))
            liq_highs.append(float(rng["High"].max()))

    try:
        i_win_start = times.indexer_between_time("10:00", "15:59")[0]
        i_win_end = times.indexer_between_time("09:30", window_end)[-1]
        i_last_entry = times.indexer_between_time("09:30", "14:30")[-1]
    except IndexError:
        return None

    def bracket_from_sweep(i_sweep: int, side: int, swept: float):
        """MSS then FVG retrace after a sweep at bar i_sweep."""
        k0 = max(0, i_sweep - 3)
        mss_level = hi[k0:i_sweep + 1].max() if side > 0 else lo[k0:i_sweep + 1].min()
        for j in range(i_sweep + 1, i_win_end + 1):
            broke = cl[j] > mss_level if side > 0 else cl[j] < mss_level
            if not broke:
                continue
            gap = _find_fvg(hi, lo, i_sweep, j, side)
            if gap is None:
                return None
            g_lo, g_hi = gap
            if fvg_entry == "mid":
                level = (g_lo + g_hi) / 2.0
            else:
                level = g_hi if side > 0 else g_lo   # edge touched first
            for m in range(j + 1, i_last_entry + 1):
                if side > 0 and lo[m] <= level:
                    entry = min(op[m], level)
                    if entry > swept:
                        return m, float(entry), float(swept), 1
                    return None
                if side < 0 and hi[m] >= level:
                    entry = max(op[m], level)
                    if entry < swept:
                        return m, float(entry), float(swept), -1
                    return None
            return None
        return None

    # scan bars in the entry window for the first valid sweep -> setup
    for i in range(max(1, i_win_start), i_win_end + 1):
        for lvl in liq_lows:
            if lo[i] < lvl and cl[i] > lvl:                 # bullish sweep
                out = bracket_from_sweep(i, 1, lo[i])
                if out:
                    return out
        if allow_short:
            for lvl in liq_highs:
                if hi[i] > lvl and cl[i] < lvl:             # bearish sweep
                    out = bracket_from_sweep(i, -1, hi[i])
                    if out:
                        return out
    return None


DAY_STRATEGIES["tjr"] = DayStrategySpec("tjr", tjr_setup, {
    "liq": ["prevday", "both"],
    "window_end": ["11:30", "14:00"],
    "fvg_entry": ["edge", "mid"],
    "allow_short": [True, False],
})
