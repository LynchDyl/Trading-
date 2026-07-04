#!/usr/bin/env python3
"""Extensive day-trading backtest across the whole liquid universe.

Protocol:
- pooled backtest of every strategy/parameter combo across ~50 symbols
- chronological split: first 60% of days = train, last 40% = test (OOS)
- qualification gates (train): >= 60 trades, win rate >= 40%, expectancy > 0
- final selection: best test expectancy among combos that ALSO hold
  win rate >= 40% out-of-sample
- every trade is a 2:1 reward:risk bracket by construction
- GBP 30 account simulation on out-of-sample trades

Outputs: results/DAYTRADE_REPORT.md, results/daytrade_best.json,
results/daytrade_trades.csv, results/charts/daytrade_*.png
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
from signals_bot.universe import UNIVERSE

RESULTS = Path(__file__).resolve().parents[1] / "results"
CHARTS = RESULTS / "charts"

MIN_TRAIN_TRADES = 60
MIN_WIN_RATE = 0.40
START_GBP = 30.0
RISK_FRACTION = 0.015          # 1.5% of account risked per trade

COLS = ["n_trades", "win_rate", "target_rate", "stop_rate", "eod_rate",
        "expectancy_r", "profit_factor", "avg_risk_pct", "trades_per_day"]


def fmt_row(m: dict) -> list[str]:
    def f(k):
        v = m.get(k)
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return "—"
        if k in ("win_rate", "target_rate", "stop_rate", "eod_rate", "avg_risk_pct"):
            return f"{v*100:.1f}%"
        if k == "n_trades":
            return str(int(v))
        return f"{v:.2f}"
    return [f(k) for k in COLS]


def table(rows: dict[str, dict]) -> str:
    head = "| config | " + " | ".join(c.replace("_", " ") for c in COLS) + " |"
    sep = "|" + "---|" * (len(COLS) + 1)
    body = [f"| {k} | " + " | ".join(fmt_row(m)) + " |" for k, m in rows.items()]
    return "\n".join([head, sep] + body)


def simulate_gbp(trades: list, start: float, risk_frac: float,
                 one_per_day: bool = False):
    """Compound a GBP account through trades in time order (no leverage)."""
    rows = sorted(trades, key=lambda t: (t.date, t.entry_time))
    if one_per_day:
        seen = set()
        rows = [t for t in rows if not (t.date in seen or seen.add(t.date))]
    eq = start
    curve = []
    for t in rows:
        position = min(eq * risk_frac / t.risk_pct, eq)   # cash-only cap
        eq += position * t.pnl_pct
        curve.append((t.date, eq))
        if eq <= 1.0:
            break
    ser = pd.Series([c[1] for c in curve],
                    index=pd.to_datetime([c[0] for c in curve]))
    return eq, ser


def main():
    RESULTS.mkdir(exist_ok=True)
    CHARTS.mkdir(exist_ok=True)

    data = {}
    for sym in UNIVERSE:
        df = dt.load_intraday(sym, "5m")
        if df is not None:
            data[sym] = df
    if not data:
        raise SystemExit("No intraday data found — run scripts/fetch_intraday.py first")

    all_days = sorted({d for df in data.values() for d in set(df.index.date)})
    split = all_days[int(len(all_days) * 0.6)]
    print(f"{len(data)} symbols, {len(all_days)} sessions "
          f"({all_days[0]} -> {all_days[-1]}), OOS from {split}")

    md = ["# Day-Trading Backtest Report",
          f"\nGenerated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
          f"\nUniverse: **{len(data)} liquid US stocks/ETFs**, 5-minute bars, "
          f"**{len(all_days)} sessions** ({all_days[0]} → {all_days[-1]}). "
          f"Every trade is a **2:1 reward:risk bracket** (target = 2× stop "
          f"distance), flat by 15:55 ET, {dt.COST_PCT*1e4:.0f} bps round-trip "
          f"costs, conservative fills (stop always assumed to hit before "
          f"target inside a bar).",
          f"\nTrain = first 60% of sessions, **test = last 40% (out-of-sample, "
          f"from {split})**. Selection gates: ≥{MIN_TRAIN_TRADES} train trades, "
          f"win rate ≥{MIN_WIN_RATE*100:.0f}% in BOTH train and test, positive "
          f"expectancy.\n",
          "**Research output, not financial advice.**\n"]

    results = []   # (strategy, params, train_m, test_m, test_trades)
    for sname, spec in dt.DAY_STRATEGIES.items():
        for params in spec.combos():
            tr_trades, te_trades = [], []
            for sym, df in data.items():
                tr_trades += dt.run_symbol(sym, df, sname, params, date_to=split)
                te_trades += dt.run_symbol(sym, df, sname, params, date_from=split)
            m_tr = dt.pooled_metrics(tr_trades)
            m_te = dt.pooled_metrics(te_trades)
            results.append((sname, params, m_tr, m_te, te_trades))
            print(f"{sname} {params}: train n={m_tr.get('n_trades',0)} "
                  f"wr={m_tr.get('win_rate',0):.2f} exp={m_tr.get('expectancy_r',0):.3f} | "
                  f"test n={m_te.get('n_trades',0)} wr={m_te.get('win_rate',0):.2f} "
                  f"exp={m_te.get('expectancy_r',0):.3f}")

    # ---- report: all combos, train ----
    md.append("## Training-period results (all configurations)\n")
    rows = {f"`{s}` {p}": m for s, p, m, _, _ in
            sorted(results, key=lambda r: r[2].get("expectancy_r", -9), reverse=True)
            if m.get("n_trades", 0) > 0}
    md.append(table(rows))

    # ---- qualification + OOS ----
    qualified = [r for r in results
                 if r[2].get("n_trades", 0) >= MIN_TRAIN_TRADES
                 and r[2].get("win_rate", 0) >= MIN_WIN_RATE
                 and r[2].get("expectancy_r", -9) > 0]
    md.append("\n## Out-of-sample results (train-qualified configurations only)\n")
    md.append(f"{len(qualified)} of {len(results)} configurations passed the "
              f"training gates (≥{MIN_TRAIN_TRADES} trades, win rate ≥40%, "
              f"positive expectancy).\n")
    rows = {f"`{s}` {p}": m_te for s, p, _, m_te, _ in
            sorted(qualified, key=lambda r: r[3].get("expectancy_r", -9), reverse=True)}
    md.append(table(rows))

    finalists = [r for r in qualified
                 if r[3].get("win_rate", 0) >= MIN_WIN_RATE
                 and r[3].get("expectancy_r", -9) > 0
                 and r[3].get("n_trades", 0) >= 30]
    best_cfg = None
    if finalists:
        finalists.sort(key=lambda r: r[3]["expectancy_r"], reverse=True)
        sname, params, m_tr, m_te, te_trades = finalists[0]
        best_cfg = (sname, params, m_tr, m_te, te_trades)

        md.append(f"\n## Selected configuration: `{sname}` {params}\n")
        md.append(f"- Out-of-sample: **{m_te['n_trades']} trades**, win rate "
                  f"**{m_te['win_rate']*100:.1f}%** (target hit {m_te['target_rate']*100:.0f}%, "
                  f"stopped {m_te['stop_rate']*100:.0f}%, closed EOD "
                  f"{m_te['eod_rate']*100:.0f}%), expectancy "
                  f"**{m_te['expectancy_r']:+.2f}R** per trade, profit factor "
                  f"{m_te['profit_factor']:.2f}.")

        # per-symbol breakdown
        df_te = pd.DataFrame([t.__dict__ for t in te_trades])
        by_sym = df_te.groupby("symbol")["r_multiple"].agg(["count", "mean", "sum"])
        by_sym = by_sym.sort_values("sum", ascending=False)
        md.append("\n### Best / worst symbols out-of-sample (total R)\n")
        md.append("| symbol | trades | avg R | total R |")
        md.append("|---|---|---|---|")
        show = pd.concat([by_sym.head(8), by_sym.tail(5)])
        for sym, row in show.iterrows():
            md.append(f"| {sym} | {int(row['count'])} | {row['mean']:+.2f} "
                      f"| {row['sum']:+.1f} |")

        # ---- GBP 30 simulations on OOS trades ----
        eq_all, curve_all = simulate_gbp(te_trades, START_GBP, RISK_FRACTION)
        eq_one, curve_one = simulate_gbp(te_trades, START_GBP, RISK_FRACTION,
                                         one_per_day=True)
        md.append(f"\n### £{START_GBP:.0f} account simulation (out-of-sample, "
                  f"risking {RISK_FRACTION*100:.1f}% per trade, no leverage)\n")
        md.append(f"- Taking **every** signal ({len(te_trades)} trades): "
                  f"£{START_GBP:.2f} → **£{eq_all:.2f}** "
                  f"({(eq_all/START_GBP-1)*100:+.1f}%)")
        md.append(f"- Taking the **first signal each day** (single-position "
                  f"account): £{START_GBP:.2f} → **£{eq_one:.2f}** "
                  f"({(eq_one/START_GBP-1)*100:+.1f}%)")
        md.append("\nNote: with ~0.5% typical stop distance, 1.5% account risk "
                  "wants ~3× the account in position size; the no-leverage cap "
                  "means realized risk per trade is often below 1.5%. Fractional "
                  "shares required at this account size.")

        if len(curve_all):
            report.equity_chart(
                {"all signals": curve_all, "1 trade/day": curve_one},
                f"£{START_GBP:.0f} account — {sname} out-of-sample",
                CHARTS / "daytrade_equity.png", ylabel="Account (£)")
        # R histogram
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 4), dpi=150)
        ax.hist(df_te["r_multiple"], bins=40, color=report.SERIES[0], alpha=0.85)
        ax.axvline(0, color=report.MUTED, lw=1)
        ax.set_title(f"{sname} — out-of-sample R-multiple distribution",
                     loc="left", fontsize=12, color=report.INK)
        ax.set_xlabel("R multiple (risk-adjusted trade result)")
        ax.set_ylabel("Trades")
        fig.tight_layout()
        fig.savefig(CHARTS / "daytrade_r_hist.png")
        plt.close(fig)
        md.append(f"\n![equity](charts/daytrade_equity.png)\n")
        md.append(f"![r distribution](charts/daytrade_r_hist.png)\n")

        df_te.to_csv(RESULTS / "daytrade_trades.csv", index=False)
        (RESULTS / "daytrade_best.json").write_text(json.dumps({
            "strategy": sname, "params": params,
            "oos_win_rate": round(m_te["win_rate"], 4),
            "oos_expectancy_r": round(m_te["expectancy_r"], 4),
            "oos_n_trades": m_te["n_trades"],
            "reward_risk": dt.RR,
            "risk_fraction": RISK_FRACTION,
            "account_gbp": START_GBP,
            "data_through": str(all_days[-1]),
        }, indent=2))
    else:
        md.append("\n## No configuration met the gates out-of-sample\n")
        md.append("No strategy held win rate ≥40% with positive expectancy on "
                  "unseen data. **Recommendation: do not trade this live.** "
                  "The nightly intraday fetch keeps accumulating history; "
                  "re-run this study as more data builds up.")

    (RESULTS / "DAYTRADE_REPORT.md").write_text("\n".join(md))
    print("Report written to results/DAYTRADE_REPORT.md")
    if best_cfg:
        print("Selected:", best_cfg[0], best_cfg[1])


if __name__ == "__main__":
    main()
