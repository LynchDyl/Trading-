#!/usr/bin/env python3
"""APEX ensemble signal — the tournament winners, combined.

Reads results/apex_config.json (written by scripts/run_tournament.py) and
emits the ensemble's current stance: each component's position and the
net per-instrument allocation of an account following the Apex bot.
Run after scripts/fetch_data.py.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals_bot import data
from signals_bot import portfolio as pf
from signals_bot.strategies import positions

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "results" / "apex_config.json"
OUT = ROOT / "signals"


def main():
    if not CFG.exists():
        raise SystemExit("results/apex_config.json missing — run the tournament first")
    cfg = json.loads(CFG.read_text())
    alloc: dict[str, float] = {}
    lines_c = ["| Component | Weight | Current stance |", "|---|---|---|"]
    data_date = None

    for comp in cfg["components"]:
        wgt = comp["weight"]
        if comp["type"] == "swing":
            df = data.load(comp["asset"])
            data_date = max(data_date or df.index[-1], df.index[-1])
            pos = float(positions(comp["strategy"], df, comp["params"]).iloc[-1])
            stance = {1: "LONG", 0: "FLAT", -1: "SHORT"}.get(int(pos), f"{pos:+.1f}")
            lines_c.append(f"| `{comp['name']}` | {wgt*100:.0f}% | "
                           f"**{stance}** {comp['asset']} |")
            alloc[comp["asset"]] = alloc.get(comp["asset"], 0.0) + wgt * pos
        elif comp["type"] == "rotation":
            p = comp["params"]
            names = sorted(set(pf.CORE + pf.AGGRESSIVE + pf.DEFENSIVE_POOL))
            panel = pf.build_panel(names)
            data_date = max(data_date or panel.index[-1], panel.index[-1])
            w = pf.target_weights(panel, pf.UNIVERSES[p["universe"]],
                                  p["lookback"], p["top_n"], p["trend_filter"],
                                  p["defensive"], p["rebalance"])
            cur = w.iloc[-1][w.iloc[-1] > 0]
            stance = ", ".join(f"{a} {v*100:.0f}%" for a, v in cur.items()) or "cash"
            lines_c.append(f"| `{comp['name']}` | {wgt*100:.0f}% | {stance} |")
            for a, v in cur.items():
                alloc[a] = alloc.get(a, 0.0) + wgt * float(v)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# APEX Ensemble Signal — {now}\n",
             f"Tournament-validated ensemble "
             f"(out-of-sample: avg week {cfg['oos_avg_week']*100:+.2f}%, "
             f"{cfg['oos_pct_pos_weeks']*100:.0f}% positive weeks, CAGR "
             f"{cfg['oos_cagr']*100:.1f}%, max DD {cfg['oos_max_dd']*100:.0f}%). "
             f"Data through **{data_date.date() if data_date is not None else '?'}**.\n",
             "## Components\n", *lines_c,
             "\n## Net account allocation\n",
             "| Instrument | Target % of account |", "|---|---|"]
    total = 0.0
    for a, v in sorted(alloc.items(), key=lambda kv: -abs(kv[1])):
        if abs(v) < 0.005:
            continue
        lines.append(f"| **{a}** | {v*100:+.1f}% |")
        total += v
    lines.append(f"| cash | {(1-total)*100:.1f}% |")
    lines.append("\nRebalance to these targets; positions change on daily "
                 "closes (swing components) and monthly/weekly rebalances "
                 "(rotation). See `results/TOURNAMENT_REPORT.md` for the "
                 "evidence behind each component.")
    lines.append("\n*Automated research output — not financial advice.*")

    OUT.mkdir(exist_ok=True)
    (OUT / "apex_latest.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
