#!/usr/bin/env python3
"""Run the full backtest protocol on every instrument and write results/.

Outputs:
- results/BACKTEST_REPORT.md      full report with tables
- results/summary.csv             machine-readable per-strategy metrics
- results/best_params.json        what the live signal bot trades
- results/charts/*.png            equity + drawdown charts
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals_bot import INSTRUMENTS, backtest, data, optimize, report
from signals_bot.strategies import STRATEGIES, positions

RESULTS = Path(__file__).resolve().parents[1] / "results"
CHARTS = RESULTS / "charts"

OOS_COLS = ["total_return", "cagr", "sharpe", "sortino", "max_drawdown",
            "win_rate", "profit_factor", "n_trades", "avg_bars_held", "exposure"]


def run_instrument(name: str, md: list, summary_rows: list, best_params: dict):
    df = data.load(name)
    ev = optimize.evaluate_instrument(df)
    years = ev["years"]
    bh = ev["buy_hold"]

    md.append(f"\n## {name}\n")
    md.append(f"Data: **{df.index[0].date()} → {df.index[-1].date()}** "
              f"({len(df):,} bars, {years:.1f} years). "
              f"Buy & hold: {bh.metrics['total_return']*100:,.0f}% total, "
              f"CAGR {bh.metrics['cagr']*100:.1f}%, Sharpe {bh.metrics['sharpe']:.2f}, "
              f"max drawdown {bh.metrics['max_drawdown']*100:.0f}%.\n")

    # ---- out-of-sample walk-forward table (the one that matters) ----
    md.append("### Walk-forward (out-of-sample) results\n")
    md.append("Rolling 4-year train → 1-year test, parameters re-fit each year "
              "on the train window only. These numbers are on unseen data.\n")
    rows = {}
    for sname, wf in sorted(ev["oos"].items(), key=lambda kv: kv[1].metrics["sharpe"],
                            reverse=True):
        label = f"**{sname}**" if sname == ev["best_strategy"] else sname
        rows[label] = wf.metrics
        summary_rows.append({"instrument": name, "phase": "walk_forward",
                             "strategy": sname, **wf.metrics})
    # buy & hold over the same OOS window
    if ev["oos"]:
        any_wf = next(iter(ev["oos"].values()))
        oos_idx = any_wf.oos_returns.index
        bh_oos = backtest.run(df.loc[oos_idx[0]:oos_idx[-1]],
                              pd.Series(1.0, index=df.loc[oos_idx[0]:oos_idx[-1]].index),
                              cost=0.0)
        rows["buy & hold (same period)"] = bh_oos.metrics
        summary_rows.append({"instrument": name, "phase": "walk_forward",
                             "strategy": "buy_hold", **bh_oos.metrics})
    md.append(report.metrics_table(rows, OOS_COLS))

    # ---- in-sample best-fit table (reference only) ----
    md.append("\n### In-sample best fits (reference — overfit by construction)\n")
    is_rows = {}
    for sname, entry in sorted(ev["in_sample"].items(),
                               key=lambda kv: kv[1]["score"], reverse=True):
        is_rows[f"{sname} {entry['params']}"] = entry["metrics"]
        summary_rows.append({"instrument": name, "phase": "in_sample",
                             "strategy": sname, "params": json.dumps(entry["params"]),
                             **entry["metrics"]})
    md.append(report.metrics_table(is_rows, OOS_COLS))

    # ---- charts ----
    curves = {}
    top = sorted(ev["oos"].items(), key=lambda kv: kv[1].metrics["sharpe"],
                 reverse=True)[:3]
    for sname, wf in top:
        curves[sname] = (1.0 + wf.oos_returns).cumprod()
    if ev["oos"]:
        curves["buy & hold"] = (1.0 + bh_oos.returns).cumprod()
        report.equity_chart(curves, f"{name} — walk-forward out-of-sample equity",
                            CHARTS / f"{name}_oos_equity.png",
                            log_scale=(name in ("NVDA", "TSLA")))
        best = ev["best_strategy"]
        if best:
            report.drawdown_chart((1.0 + ev["oos"][best].oos_returns).cumprod(),
                                  f"{name} — {best} out-of-sample drawdown",
                                  CHARTS / f"{name}_oos_drawdown.png")

    # ---- live selection ----
    if ev["best_strategy"]:
        wf = ev["oos"][ev["best_strategy"]]
        md.append(f"\n### Selected for live signals: `{ev['best_strategy']}`\n")
        md.append(f"- Out-of-sample: Sharpe **{wf.metrics['sharpe']:.2f}**, "
                  f"CAGR {wf.metrics['cagr']*100:.1f}%, "
                  f"max DD {wf.metrics['max_drawdown']*100:.1f}%, "
                  f"{wf.metrics['n_trades']} trades, "
                  f"win rate {wf.metrics['win_rate']*100:.0f}%.")
        md.append(f"- Live parameters (re-fit on the last 4 years): "
                  f"`{ev['live_params']}`")
        md.append(f"- Walk-forward parameter history: most recent folds "
                  f"{[f[2] for f in wf.fold_params[-3:]]}")
        md.append(f"\n![{name} equity](charts/{name}_oos_equity.png)\n")
        md.append(f"![{name} drawdown](charts/{name}_oos_drawdown.png)\n")
        best_params[name] = {
            "strategy": ev["best_strategy"],
            "params": ev["live_params"],
            "oos_sharpe": round(wf.metrics["sharpe"], 3),
            "oos_cagr": round(wf.metrics["cagr"], 4),
            "oos_max_drawdown": round(wf.metrics["max_drawdown"], 4),
            "oos_win_rate": round(wf.metrics["win_rate"], 4)
            if np.isfinite(wf.metrics.get("win_rate", np.nan)) else None,
            "data_through": str(df.index[-1].date()),
        }
    return ev


def main():
    RESULTS.mkdir(exist_ok=True)
    CHARTS.mkdir(exist_ok=True)
    md = ["# Backtest Report — Short-Term Trading Signals",
          f"\nGenerated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
          f"Execution: signals at the close, positions earn the next bar's "
          f"close-to-close return, {backtest.DEFAULT_COST*1e4:.0f} bps cost per unit "
          f"turnover. Strategy selection is by walk-forward out-of-sample Sharpe, "
          f"never in-sample fit.\n",
          "**This is research output, not financial advice.** Past performance "
          "does not guarantee future results.\n"]
    summary_rows: list = []
    best_params: dict = {}
    for name in INSTRUMENTS:
        try:
            run_instrument(name, md, summary_rows, best_params)
        except FileNotFoundError as exc:
            md.append(f"\n## {name}\n\nSkipped: {exc}\n")

    (RESULTS / "BACKTEST_REPORT.md").write_text("\n".join(md))
    pd.DataFrame(summary_rows).to_csv(RESULTS / "summary.csv", index=False)
    (RESULTS / "best_params.json").write_text(json.dumps(best_params, indent=2))
    print("Report written to results/BACKTEST_REPORT.md")
    print(json.dumps(best_params, indent=2))


if __name__ == "__main__":
    main()
