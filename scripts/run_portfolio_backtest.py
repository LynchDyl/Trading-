#!/usr/bin/env python3
"""FULL backtest of the momentum-rotation wealth-builder with GBP 50/week.

Protocol:
- 192 configurations (universe x lookback x top-N x trend filter x
  defensive sleeve x rebalance frequency)
- chronological split: train = history -> 2017-12-31, test = 2018 -> now
  (test spans the 2018 correction, 2020 crash, 2022 bear and 2024-26 bull)
- configs are RANKED ON TRAIN ONLY (CAGR, subject to Sharpe >= 0.5 and
  max drawdown >= -60%); the winner's out-of-sample record is the headline
- GBP 50/week simulations vs SPY / QQQ / 60-40 DCA benchmarks
- yearly returns and last-24-months month-by-month table for the winner

Outputs: results/PORTFOLIO_REPORT.md, results/portfolio_best.json, charts.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals_bot import portfolio as pf
from signals_bot import report

RESULTS = Path(__file__).resolve().parents[1] / "results"
CHARTS = RESULTS / "charts"

SPLIT = "2018-01-01"
TRAIN_START = {"core": "2007-06-01", "aggressive": "2011-06-01"}
CONTRIB = 50.0

MCOLS = ["cagr", "sharpe", "max_drawdown", "avg_month", "pct_positive_months",
         "worst_month", "final_balance", "profit"]


def fmt(m, k):
    v = m.get(k)
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    if k in ("cagr", "max_drawdown", "avg_month", "pct_positive_months",
             "worst_month"):
        return f"{v*100:.1f}%"
    if k in ("final_balance", "profit"):
        return f"£{v:,.0f}"
    return f"{v:.2f}"


def table(rows: dict[str, dict]) -> str:
    head = "| config | " + " | ".join(c.replace("_", " ") for c in MCOLS) + " |"
    sep = "|" + "---|" * (len(MCOLS) + 1)
    return "\n".join([head, sep] + [
        f"| {k} | " + " | ".join(fmt(m, c) for c in MCOLS) + " |"
        for k, m in rows.items()])


def cfg_label(p: dict) -> str:
    return (f"`{p['universe']}` lb={p['lookback']} n={p['top_n']} "
            f"trend={'Y' if p['trend_filter'] else 'N'} def={p['defensive']} "
            f"reb={p['rebalance']}")


def bench_weights(panel, spec: dict) -> pd.DataFrame:
    w = pd.DataFrame(0.0, index=panel.index, columns=panel.columns)
    for k, v in spec.items():
        if k in w.columns:
            w[k] = v
    return w


def main():
    RESULTS.mkdir(exist_ok=True)
    CHARTS.mkdir(exist_ok=True)
    names = sorted(set(pf.CORE + pf.AGGRESSIVE + pf.DEFENSIVE_POOL + ["SPY", "IEF"]))
    panel = pf.build_panel(names)
    print("panel:", panel.shape, panel.index[0].date(), "->", panel.index[-1].date())

    md = ["# Wealth-Builder Backtest — Momentum Rotation with £50/week",
          f"\nGenerated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
          f"Data through **{panel.index[-1].date()}**.",
          "\nEvery configuration decides weights on the rebalance close and "
          "trades the next day, 10 bps turnover costs, £50 contributed every "
          "Monday. Configs are ranked **on the training period only** "
          f"(→ 2017), then judged on unseen data (**{SPLIT} → today**: covers "
          "the 2018 correction, the 2020 crash, the 2022 bear market and the "
          "2024-26 bull run). USD returns; GBP/USD ignored.\n",
          "**Research output, not financial advice.**\n"]

    # ---- grid ----
    all_rows = []
    for params in pf.RotationSpec().combos():
        uni = pf.UNIVERSES[params["universe"]]
        w = pf.target_weights(panel, uni, params["lookback"], params["top_n"],
                              params["trend_filter"], params["defensive"],
                              params["rebalance"])
        t0 = TRAIN_START[params["universe"]]
        res_tr = pf.run(panel, w, CONTRIB, date_from=t0, date_to=SPLIT)
        res_te = pf.run(panel, w, CONTRIB, date_from=SPLIT)
        all_rows.append((params, res_tr.metrics, res_te.metrics, w))
        print(cfg_label(params),
              f"train cagr={res_tr.metrics.get('cagr', np.nan):.1%}",
              f"test cagr={res_te.metrics.get('cagr', np.nan):.1%}",
              f"test dd={res_te.metrics.get('max_drawdown', np.nan):.0%}")

    # ---- benchmarks (test period) ----
    bm_specs = {"SPY (buy every week)": {"SPY": 1.0},
                "QQQ (buy every week)": {"QQQ": 1.0},
                "60/40 SPY-IEF": {"SPY": 0.6, "IEF": 0.4}}
    bench_te, bench_tr = {}, {}
    for label, spec in bm_specs.items():
        wb = bench_weights(panel, spec)
        bench_te[label] = pf.run(panel, wb, CONTRIB, date_from=SPLIT)
        bench_tr[label] = pf.run(panel, wb, CONTRIB,
                                 date_from=TRAIN_START["core"], date_to=SPLIT)

    # ---- selection on TRAIN only ----
    # Ex-ante rule (decided before looking at test results): rank by
    # drawdown-adjusted growth (Calmar) on train, require Sharpe >= 0.5,
    # max drawdown >= -60%, and top_n >= 2 (diversification floor — a
    # 100%-in-one-leveraged-asset "winner" is a lottery ticket, not a
    # strategy; the raw-CAGR table below shows why).
    by_cagr = sorted([r for r in all_rows if r[1]],
                     key=lambda r: r[1]["cagr"], reverse=True)
    md.append("## Top 10 by raw TRAINING CAGR — and how they did "
              "out-of-sample (the overfitting exhibit)\n")
    md.append("### Training period\n")
    md.append(table({cfg_label(p): m for p, m, _, _ in by_cagr[:10]}))
    md.append("\n### The same 10, out-of-sample (2018 → today)\n")
    md.append(table({cfg_label(p): m for p, _, m, _ in by_cagr[:10]}))

    ok = [r for r in all_rows
          if r[1] and r[1]["sharpe"] >= 0.5 and r[1]["max_drawdown"] >= -0.60
          and r[0]["top_n"] >= 2]
    ok.sort(key=lambda r: r[1].get("calmar", -9), reverse=True)
    md.append("\n## Top 10 by TRAINING Calmar (the ex-ante selection rule)\n")
    md.append("### Training period\n")
    md.append(table({cfg_label(p): m for p, m, _, _ in ok[:10]}))
    md.append("\n### The same 10, out-of-sample (2018 → today)\n")
    md.append(table({cfg_label(p): m for p, _, m, _ in ok[:10]}))
    md.append("\n### Benchmarks, out-of-sample (same £50/week)\n")
    md.append(table({k: v.metrics for k, v in bench_te.items()}))

    if not ok:
        md.append("\nNo configuration met the training constraints.")
        (RESULTS / "PORTFOLIO_REPORT.md").write_text("\n".join(md))
        return

    params, m_tr, m_te, w_best = ok[0]
    res_te = pf.run(panel, w_best, CONTRIB, date_from=SPLIT)
    res_full = pf.run(panel, w_best, CONTRIB, date_from=TRAIN_START[params["universe"]])

    md.append(f"\n## Selected (best train, judged out-of-sample): {cfg_label(params)}\n")
    md.append(f"- Out-of-sample ({SPLIT} → today): CAGR **{m_te['cagr']*100:.1f}%**, "
              f"Sharpe {m_te['sharpe']:.2f}, max drawdown {m_te['max_drawdown']*100:.0f}%, "
              f"average month {m_te['avg_month']*100:+.2f}%, "
              f"{m_te['pct_positive_months']*100:.0f}% of months positive, "
              f"worst month {m_te['worst_month']*100:.1f}%.")
    md.append(f"- £50/week since {SPLIT}: contributed "
              f"£{m_te['total_contributed']:,.0f} → **£{m_te['final_balance']:,.0f}** "
              f"(profit £{m_te['profit']:,.0f}).")
    spy_te = bench_te["SPY (buy every week)"].metrics
    md.append(f"- Same money into SPY: £{spy_te['final_balance']:,.0f} "
              f"(profit £{spy_te['profit']:,.0f}).")

    # yearly returns
    yr = (1 + res_full.returns).resample("YE").prod() - 1
    spy_full = pf.run(panel, bench_weights(panel, {"SPY": 1.0}), CONTRIB,
                      date_from=TRAIN_START[params["universe"]])
    yr_spy = (1 + spy_full.returns).resample("YE").prod() - 1
    md.append("\n### Yearly returns (strategy vs SPY)\n")
    md.append("| year | strategy | SPY |")
    md.append("|---|---|---|")
    for y in yr.index:
        md.append(f"| {y.year} | {yr[y]*100:+.1f}% | {yr_spy.get(y, np.nan)*100:+.1f}% |")

    # month-by-month, last 24 months
    mo = (1 + res_te.returns).resample("ME").prod() - 1
    md.append("\n### Month-by-month, last 24 months\n")
    md.append("| month | return | | month | return |")
    md.append("|---|---|---|---|---|")
    last = mo.tail(24)
    half = len(last) // 2
    for a, b in zip(last.index[:half], last.index[half:]):
        md.append(f"| {a.strftime('%Y-%m')} | {last[a]*100:+.1f}% | "
                  f"| {b.strftime('%Y-%m')} | {last[b]*100:+.1f}% |")

    # parameter robustness: same config, neighbouring lookbacks/topN
    md.append("\n### Robustness — neighbouring configurations, out-of-sample\n")
    nbrs = {}
    for p, _, m2, _ in all_rows:
        if (p["universe"] == params["universe"]
                and p["trend_filter"] == params["trend_filter"]
                and p["rebalance"] == params["rebalance"]
                and p["defensive"] == params["defensive"]):
            nbrs[cfg_label(p)] = m2
    md.append(table(nbrs))

    # ---- charts ----
    report.equity_chart(
        {"strategy": res_te.balance,
         "SPY DCA": bench_te["SPY (buy every week)"].balance,
         "60/40 DCA": bench_te["60/40 SPY-IEF"].balance,
         "contributions": res_te.contributed},
        "£50/week since 2018 — balance", CHARTS / "portfolio_balance.png",
        ylabel="Account (£)")
    eq = (1 + res_te.returns).cumprod()
    report.drawdown_chart(eq, "Strategy drawdown, out-of-sample",
                          CHARTS / "portfolio_drawdown.png")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 4), dpi=150)
    ax.hist(mo * 100, bins=30, color=report.SERIES[0], alpha=0.85)
    ax.axvline(0, color=report.MUTED, lw=1)
    ax.set_title("Monthly returns out-of-sample (%)", loc="left", fontsize=12,
                 color=report.INK)
    ax.set_ylabel("Months")
    fig.tight_layout()
    fig.savefig(CHARTS / "portfolio_monthly_hist.png")
    plt.close(fig)
    md.append("\n![balance](charts/portfolio_balance.png)\n")
    md.append("![drawdown](charts/portfolio_drawdown.png)\n")
    md.append("![monthly](charts/portfolio_monthly_hist.png)\n")

    (RESULTS / "portfolio_best.json").write_text(json.dumps({
        "params": params,
        "oos_cagr": round(m_te["cagr"], 4),
        "oos_sharpe": round(m_te["sharpe"], 3),
        "oos_max_drawdown": round(m_te["max_drawdown"], 4),
        "oos_pct_positive_months": round(m_te["pct_positive_months"], 4),
        "contribution_gbp_weekly": CONTRIB,
        "data_through": str(panel.index[-1].date()),
    }, indent=2))
    (RESULTS / "PORTFOLIO_REPORT.md").write_text("\n".join(md))
    print("\nSelected:", cfg_label(params))
    print(json.dumps(m_te, indent=2))


if __name__ == "__main__":
    main()
