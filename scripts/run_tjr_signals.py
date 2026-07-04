#!/usr/bin/env python3
"""TJR setup scanner — PAPER TRADING ONLY.

The backtest (results/TJR_REPORT.md) found NO configuration of the
mechanical TJR pattern with positive expectancy after costs. This scanner
exists so the setups can be paper-traded and studied while intraday history
accumulates — not to trade real money.

Scans the universe on today's 5-minute bars for the sweep -> MSS -> FVG
sequence and reports any completed setups with their 2:1 brackets.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals_bot import daytrade as dt
import signals_bot.tjr  # noqa: F401 - registers "tjr"
from signals_bot.universe import UNIVERSE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_daytrade_signals import today_bars

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "results" / "tjr_best.json"
OUT = ROOT / "signals"


def main():
    if not CFG.exists():
        raise SystemExit("results/tjr_best.json missing — run the backtest first")
    cfg = json.loads(CFG.read_text())
    params = cfg["params"]
    account = cfg.get("account_gbp", 100.0)
    risk_frac = cfg.get("risk_fraction", 0.01)

    rows = []
    for sym in UNIVERSE:
        try:
            bars, prev = today_bars(sym)
            if bars is None or len(bars) < 8 or not prev:
                continue
            setup = signals_bot.tjr.tjr_setup(bars, prev, **params)
            if setup is None:
                continue
            i, entry, stop, side = setup
            target = entry + dt.RR * (entry - stop) if side > 0 \
                else entry - dt.RR * (stop - entry)
            rows.append({"symbol": sym, "side": "LONG" if side > 0 else "SHORT",
                         "entry": entry, "stop": stop, "target": target,
                         "time": str(bars.index[i].time()),
                         "last": float(bars["Close"].iloc[-1])})
        except Exception as exc:  # noqa: BLE001
            print(f"[tjr] {sym} failed: {exc}", file=sys.stderr)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# TJR Setups — {now}\n",
             "> ⚠️ **PAPER TRADING ONLY.** The backtest found no positive "
             "expectancy for this pattern after costs "
             "(see `results/TJR_REPORT.md`). Track it, don't fund it.\n",
             f"Rules: `{params}` — sweep → structure shift → FVG retrace, "
             f"2:1 bracket, flat by 15:55 ET.\n"]
    if not rows:
        lines.append("**No completed TJR setups in the universe so far today.**")
    else:
        lines.append("| Symbol | Side | Setup time (ET) | Entry | Stop | "
                     "Target (2R) | Risk % | Paper position £ | Last |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            risk_pct = abs(r["entry"] - r["stop"]) / r["entry"]
            if risk_pct <= 0:
                continue
            pos = min(account * risk_frac / risk_pct, account)
            lines.append(f"| **{r['symbol']}** | {r['side']} | {r['time']} "
                         f"| {r['entry']:.2f} | {r['stop']:.2f} "
                         f"| {r['target']:.2f} | {risk_pct*100:.2f}% "
                         f"| £{pos:.2f} | {r['last']:.2f} |")
    lines.append("\n*Automated research output — not financial advice.*")
    OUT.mkdir(exist_ok=True)
    (OUT / "tjr_latest.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
