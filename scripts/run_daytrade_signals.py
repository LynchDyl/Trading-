#!/usr/bin/env python3
"""Morning day-trade signal scan.

Runs shortly after the opening range completes (~10:05+ ET) on a machine
with market access (GitHub Actions runner). Reads the strategy chosen by
scripts/run_daytrade_backtest.py from results/daytrade_best.json, scans the
whole universe on today's 5-minute bars, and emits bracket orders sized for
the configured account.

Statuses:
- TRIGGERED — the entry level has already been crossed today
- ARMED     — level not yet crossed; a stop/limit order can be staged
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals_bot import daytrade as dt
from signals_bot.universe import UNIVERSE

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "results" / "daytrade_best.json"
OUT = ROOT / "signals"


def today_bars(sym: str):
    df = yf.download(sym, interval="5m", period="5d", auto_adjust=False,
                     prepost=False, progress=False)
    if df is None or df.empty:
        return None, None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(dt.NY)
    df = df.between_time("09:30", "15:59")
    days = sorted(set(df.index.date))
    if not days:
        return None, None
    today = days[-1]
    prev: dict = {}
    range_mins = 30
    for day in days[:-1]:
        bars = df[df.index.date == day]
        if len(bars):
            rngv = dt._range_bars(bars, range_mins)["Volume"].sum()
            dt._update_prev(prev, bars, rngv)
    return df[df.index.date == today], prev


def scan(sym: str, strategy: str, params: dict) -> dict | None:
    bars, prev = today_bars(sym)
    if bars is None or len(bars) < 3:
        return None
    last = float(bars["Close"].iloc[-1])
    if strategy == "orb":
        rng = dt._range_bars(bars, params.get("range_mins", 30))
        if rng.empty or len(rng) < 3:
            return None
        r_hi, r_lo = float(rng["High"].max()), float(rng["Low"].min())
        if params.get("rvol_min", 0) > 0:
            base = prev.get("avg_range_vol")
            if base and float(rng["Volume"].sum()) / base < params["rvol_min"]:
                return None
        stop = r_lo if params.get("stop_mode", "range") == "range" else (r_hi + r_lo) / 2
        entry = r_hi
        setup = dt.orb_setup(bars, prev, **params)
        status = "TRIGGERED" if setup is not None else "ARMED"
        if setup is not None:
            _, entry, stop, side = setup
            if side < 0:
                entry_side = "SHORT"
            else:
                entry_side = "LONG"
        else:
            entry_side = "LONG"
        target = entry + dt.RR * (entry - stop)
        return {"symbol": sym, "side": entry_side, "status": status,
                "entry": entry, "stop": stop, "target": target, "last": last}
    if strategy == "gap_go":
        setup = dt.gap_go_setup(bars, prev, **params)
        prev_close = prev.get("close")
        if prev_close is None:
            return None
        gap = bars["Open"].iloc[0] / prev_close - 1.0
        if abs(gap) < params.get("gap_min", 0.02):
            return None
        rng = dt._range_bars(bars, 15)
        r_hi, r_lo = float(rng["High"].max()), float(rng["Low"].min())
        side = 1 if gap > 0 else -1
        if side < 0 and not params.get("allow_short", True):
            return None
        entry, stop = (r_hi, r_lo) if side > 0 else (r_lo, r_hi)
        if setup is not None:
            _, entry, stop, side = setup
        target = entry + dt.RR * (entry - stop) * (1 if side > 0 else -1) \
            if side > 0 else entry - dt.RR * (stop - entry)
        return {"symbol": sym, "side": "LONG" if side > 0 else "SHORT",
                "status": "TRIGGERED" if setup else "ARMED",
                "entry": float(entry), "stop": float(stop),
                "target": float(target), "last": last, "gap": f"{gap*100:+.1f}%"}
    return None


def main():
    if not CFG.exists():
        raise SystemExit("results/daytrade_best.json missing — run the backtest first")
    cfg = json.loads(CFG.read_text())
    strategy, params = cfg["strategy"], cfg["params"]
    account = cfg.get("account_gbp", 30.0)
    risk_frac = cfg.get("risk_fraction", 0.015)

    rows = []
    for sym in UNIVERSE:
        try:
            r = scan(sym, strategy, params)
            if r:
                rows.append(r)
        except Exception as exc:  # noqa: BLE001
            print(f"[scan] {sym} failed: {exc}", file=sys.stderr)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Day-Trade Signals — {now}\n",
             f"Strategy: `{strategy}` {params} — every order is a 2:1 "
             f"reward:risk bracket, flat by 15:55 ET. Sized for a "
             f"£{account:.0f} account risking {risk_frac*100:.1f}% per trade "
             f"(fractional shares).\n"]
    if not rows:
        lines.append("**No setups in the universe today.**")
    else:
        lines.append("| Symbol | Side | Status | Entry | Stop | Target (2R) | "
                     "Risk % | Position £ | Last |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in sorted(rows, key=lambda x: x["status"]):
            risk_pct = abs(r["entry"] - r["stop"]) / r["entry"]
            if risk_pct <= 0:
                continue
            pos = min(account * risk_frac / risk_pct, account)
            lines.append(
                f"| **{r['symbol']}** | {r['side']} | {r['status']} "
                f"| {r['entry']:.2f} | {r['stop']:.2f} | {r['target']:.2f} "
                f"| {risk_pct*100:.2f}% | £{pos:.2f} | {r['last']:.2f} |")
        lines.append(f"\n{len(rows)} setups. TRIGGERED = already through the "
                     f"entry level; ARMED = stage a stop-entry order at the "
                     f"level. One position at a time on a small account — "
                     f"prefer the first TRIGGERED signal.")
    lines.append("\n*Automated research output — not financial advice.*")
    OUT.mkdir(exist_ok=True)
    (OUT / "daytrade_latest.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
