#!/usr/bin/env python3
"""Weekly wealth-builder allocation signal.

Reads the configuration selected by scripts/run_portfolio_backtest.py and
emits: the current target allocation, where this week's contribution goes,
and the momentum ranking behind it. Run after scripts/fetch_data.py.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals_bot import portfolio as pf

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "results" / "portfolio_best.json"
OUT = ROOT / "signals"


def main():
    if not CFG.exists():
        raise SystemExit("results/portfolio_best.json missing — run the backtest first")
    cfg = json.loads(CFG.read_text())
    p = cfg["params"]
    contrib = cfg.get("contribution_gbp_weekly", 50.0)

    names = sorted(set(pf.CORE + pf.AGGRESSIVE + pf.DEFENSIVE_POOL))
    panel = pf.build_panel(names)
    uni = pf.UNIVERSES[p["universe"]]
    w = pf.target_weights(panel, uni, p["lookback"], p["top_n"],
                          p["trend_filter"], p["defensive"], p["rebalance"])
    today = panel.index[-1]
    alloc = w.iloc[-1][w.iloc[-1] > 0].sort_values(ascending=False)

    scores = pf.momentum_scores(panel, p["lookback"]).iloc[-1]
    sma = panel.rolling(pf.TREND_SMA, min_periods=pf.TREND_SMA).mean().iloc[-1]
    live = [c for c in uni if c in panel.columns]
    rank = scores[live].sort_values(ascending=False)

    period = "month" if p["rebalance"] == "M" else "week"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Wealth-Builder Allocation — {now}\n",
             f"Config: `{p['universe']}` universe, lookback={p['lookback']}, "
             f"top {p['top_n']}, trend filter "
             f"{'ON' if p['trend_filter'] else 'OFF'}, defensive "
             f"{p['defensive']}, rebalanced every {period}. "
             f"Data through **{today.date()}**.\n",
             "## Current target allocation\n",
             "| Asset | Weight | This week's £50 |", "|---|---|---|"]
    for asset, weight in alloc.items():
        lines.append(f"| **{asset}** | {weight*100:.0f}% | £{contrib*weight:.2f} |")
    lines.append("\nPut this week's contribution into the targets above. "
                 f"Rebalance the whole account to these weights on the first "
                 f"trading day of each {period} (fractional shares, "
                 "zero-commission broker).\n")
    lines.append("## Momentum ranking (context)\n")
    lines.append("| Asset | Momentum | Above 10-mo SMA |")
    lines.append("|---|---|---|")
    for a in rank.index:
        above = "yes" if panel[a].iloc[-1] > sma[a] else "NO"
        lines.append(f"| {a} | {rank[a]*100:+.1f}% | {above} |")
    lines.append(f"\nBacktest evidence: `results/PORTFOLIO_REPORT.md` "
                 f"(out-of-sample CAGR {cfg['oos_cagr']*100:.1f}%, max drawdown "
                 f"{cfg['oos_max_drawdown']*100:.0f}%).")
    lines.append("\n*Automated research output — not financial advice.*")

    OUT.mkdir(exist_ok=True)
    (OUT / "portfolio_latest.md").write_text("\n".join(lines))

    hist = OUT / "portfolio_history.csv"
    row = pd.DataFrame([{"date": str(today.date()),
                         "allocation": json.dumps({k: round(v, 3) for k, v
                                                   in alloc.items()})}])
    if hist.exists():
        old = pd.read_csv(hist)
        row = pd.concat([old, row]).drop_duplicates(subset=["date"], keep="last")
    row.to_csv(hist, index=False)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
