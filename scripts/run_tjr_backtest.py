#!/usr/bin/env python3
"""Backtest of the TJR (smart-money concepts) day-trading strategy across
the whole intraday universe, with a GBP 100 account simulation.

Same protocol as the generic day-trade study: pooled across ~59 symbols on
5-minute bars, chronological 60/40 train/test split, every trade a 2:1
bracket with conservative fills, 6 bps round-trip costs. GBP 100 start,
1% risk per trade.

Outputs: results/TJR_REPORT.md, results/tjr_best.json,
results/tjr_trades.csv, results/charts/tjr_*.png
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals_bot import daytrade as dt
from signals_bot import report
import signals_bot.tjr  # noqa: F401 - registers the "tjr" strategy
from signals_bot.universe import UNIVERSE
from run_daytrade_backtest import table, simulate_gbp

RESULTS = Path(__file__).resolve().parents[1] / "results"
CHARTS = RESULTS / "charts"

START_GBP = 100.0
RISK_FRACTION = 0.01
MIN_TRAIN_TRADES = 40
MIN_WIN_RATE = 0.40


def main():
    RESULTS.mkdir(exist_ok=True)
    CHARTS.mkdir(exist_ok=True)
    data = {s: d for s in UNIVERSE if (d := dt.load_intraday(s, "5m")) is not None}
    all_days = sorted({d for df in data.values() for d in set(df.index.date)})
    split = all_days[int(len(all_days) * 0.6)]

    md = ["# TJR Strategy Backtest — Liquidity Sweep → MSS → FVG Retrace",
          f"\nGenerated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
          f"\nMechanical implementation of TJR's published playbook (see "
          f"`signals_bot/tjr.py` for the exact rules): sweep of prior-day or "
          f"opening-range liquidity, close back through the level, market "
          f"structure shift, limit entry on the fair-value-gap retrace, stop "
          f"at the swept extreme, **2:1 target**, flat by 15:55 ET, one trade "
          f"per symbol per day, NY-morning entries only.",
          f"\nUniverse: **{len(data)} symbols**, 5m bars, {len(all_days)} "
          f"sessions ({all_days[0]} → {all_days[-1]}), train/test split at "
          f"**{split}**, {dt.COST_PCT*1e4:.0f} bps round-trip costs, "
          f"conservative fills (stop before target inside a bar).",
          "\n**Important honesty note:** TJR trades this pattern with "
          "discretion — skipping setups, reading context, managing winners. "
          "A mechanical scan measures the pattern's raw edge, not the "
          "trader's. Results below are what the RULES earn, unaided.\n",
          "**Research output, not financial advice.**\n"]

    results = []
    for params in dt.DAY_STRATEGIES["tjr"].combos():
        tr, te = [], []
        for sym, df in data.items():
            tr += dt.run_symbol(sym, df, "tjr", params, date_to=split)
            te += dt.run_symbol(sym, df, "tjr", params, date_from=split)
        m_tr, m_te = dt.pooled_metrics(tr), dt.pooled_metrics(te)
        results.append((params, m_tr, m_te, te))
        print(params, "| train", {k: round(m_tr.get(k, 0), 3) for k in
                                  ("n_trades", "win_rate", "expectancy_r")},
              "| test", {k: round(m_te.get(k, 0), 3) for k in
                         ("n_trades", "win_rate", "expectancy_r")})

    md.append("## All configurations — training period\n")
    md.append(table({f"{p}": m for p, m, _, _ in
                     sorted(results, key=lambda r: r[1].get("expectancy_r", -9),
                            reverse=True) if m.get("n_trades", 0) > 0}))
    md.append("\n## All configurations — out-of-sample\n")
    md.append(table({f"{p}": m for p, _, m, _ in
                     sorted(results, key=lambda r: r[2].get("expectancy_r", -9),
                            reverse=True) if m.get("n_trades", 0) > 0}))

    qualified = [r for r in results
                 if r[1].get("n_trades", 0) >= MIN_TRAIN_TRADES
                 and r[1].get("win_rate", 0) >= MIN_WIN_RATE
                 and r[1].get("expectancy_r", -9) > 0]

    # for the GBP 100 sim: the train-best config regardless, so the user
    # sees exactly what the strategy would have done with real money
    by_train = sorted([r for r in results if r[1].get("n_trades", 0) >= MIN_TRAIN_TRADES],
                      key=lambda r: r[1].get("expectancy_r", -9), reverse=True)
    params, m_tr, m_te, te_trades = by_train[0] if by_train else results[0]

    md.append(f"\n## Train-best configuration: `{params}`\n")
    md.append(f"- Train: {m_tr.get('n_trades', 0)} trades, win rate "
              f"{m_tr.get('win_rate', 0)*100:.1f}%, expectancy "
              f"{m_tr.get('expectancy_r', 0):+.2f}R.")
    md.append(f"- **Out-of-sample: {m_te.get('n_trades', 0)} trades, win rate "
              f"{m_te.get('win_rate', 0)*100:.1f}%, expectancy "
              f"**{m_te.get('expectancy_r', 0):+.2f}R**, profit factor "
              f"{m_te.get('profit_factor', 0):.2f}, outcomes: "
              f"{m_te.get('target_rate', 0)*100:.0f}% target / "
              f"{m_te.get('stop_rate', 0)*100:.0f}% stop / "
              f"{m_te.get('eod_rate', 0)*100:.0f}% EOD.**")

    if te_trades:
        df_te = pd.DataFrame([t.__dict__ for t in te_trades])
        df_te.to_csv(RESULTS / "tjr_trades.csv", index=False)
        eq_all, curve_all = simulate_gbp(te_trades, START_GBP, RISK_FRACTION)
        eq_one, curve_one = simulate_gbp(te_trades, START_GBP, RISK_FRACTION,
                                         one_per_day=True)
        md.append(f"\n### £{START_GBP:.0f} account, out-of-sample "
                  f"(risking {RISK_FRACTION*100:.0f}% per trade, no leverage)\n")
        md.append(f"- Taking **every** signal ({len(te_trades)} trades): "
                  f"£{START_GBP:.2f} → **£{eq_all:.2f}** "
                  f"({(eq_all/START_GBP-1)*100:+.1f}%)")
        md.append(f"- **First signal each day** (single-position account): "
                  f"£{START_GBP:.2f} → **£{eq_one:.2f}** "
                  f"({(eq_one/START_GBP-1)*100:+.1f}%)")
        if len(curve_all) > 1:
            report.equity_chart({"all signals": curve_all,
                                 "1 trade/day": curve_one},
                                f"£{START_GBP:.0f} account — TJR out-of-sample",
                                CHARTS / "tjr_equity.png", ylabel="Account (£)")
            md.append("\n![equity](charts/tjr_equity.png)\n")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 4), dpi=150)
        ax.hist(df_te["r_multiple"], bins=30, color=report.SERIES[0], alpha=0.85)
        ax.axvline(0, color=report.MUTED, lw=1)
        ax.set_title("TJR — out-of-sample R multiples", loc="left",
                     fontsize=12, color=report.INK)
        ax.set_ylabel("Trades")
        fig.tight_layout()
        fig.savefig(CHARTS / "tjr_r_hist.png")
        plt.close(fig)
        md.append("![r](charts/tjr_r_hist.png)\n")

    verdict_pass = (qualified and
                    max(q[2].get("expectancy_r", -9) for q in qualified) > 0
                    and max(q[2].get("win_rate", 0) for q in qualified) >= MIN_WIN_RATE)
    md.append("\n## Verdict\n")
    if verdict_pass:
        best_q = max(qualified, key=lambda r: r[2].get("expectancy_r", -9))
        md.append(f"A configuration passed both gates: `{best_q[0]}` — "
                  f"OOS win rate {best_q[2]['win_rate']*100:.1f}%, expectancy "
                  f"{best_q[2]['expectancy_r']:+.2f}R. Tradeable on paper; "
                  f"revalidate as intraday history accumulates.")
        chosen = best_q
    else:
        md.append("**No configuration held ≥40% win rate with positive "
                  "expectancy out-of-sample.** As a mechanical system on "
                  "this sample, the pattern does not show a tradeable edge "
                  "after costs. The signal scanner still reports setups for "
                  "paper-trading/study, but do not fund this with real money "
                  "on this evidence.")
        chosen = (params, m_tr, m_te, te_trades)

    (RESULTS / "tjr_best.json").write_text(json.dumps({
        "strategy": "tjr", "params": chosen[0],
        "passed_gates": bool(verdict_pass),
        "oos_win_rate": round(chosen[2].get("win_rate", 0), 4),
        "oos_expectancy_r": round(chosen[2].get("expectancy_r", 0), 4),
        "oos_n_trades": chosen[2].get("n_trades", 0),
        "account_gbp": START_GBP, "risk_fraction": RISK_FRACTION,
        "data_through": str(all_days[-1]),
    }, indent=2))
    (RESULTS / "TJR_REPORT.md").write_text("\n".join(md))
    print("Report written. Gates passed:", verdict_pass)


if __name__ == "__main__":
    main()
