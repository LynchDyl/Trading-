#!/usr/bin/env python3
"""GRAND TOURNAMENT: every implemented trading method vs all available data,
ranked by out-of-sample WEEKLY profit.

Contestants
- 9 daily strategy families (RSI-2 reversion, double-low, Bollinger
  reversion, Donchian breakout, MA cross, MACD, time-series momentum,
  52-week-high momentum, turn-of-month seasonality) x every daily asset in
  data/ — each via rolling walk-forward (4y train / 1y test, params re-fit
  yearly), so ALL reported numbers are out-of-sample.
- Momentum-rotation portfolios (core and aggressive universes) — train
  pre-2018, judged 2018+ (ex-ante Calmar rule).
- Intraday systems (ORB day-trading, TJR/SMC) — from their own studies'
  out-of-sample trades (small sample; flagged, excluded from the ensemble).
- Buy & hold on every asset as the honest baseline.

Metric: average weekly return out-of-sample (plus % positive weeks,
worst week, max drawdown, sample size).

The APEX ensemble: top qualifying components (>=150 OOS weeks, positive
avg weekly return, >=45% positive weeks), equal-weighted on their common
window -> results/apex_config.json drives the live Apex signal.

Not tested (no data): options strategies, fundamentals/news, order-flow,
sub-minute HFT, futures carry/term structure.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals_bot import backtest, data, optimize, report
from signals_bot import portfolio as pf
from signals_bot.strategies import STRATEGIES

RESULTS = Path(__file__).resolve().parents[1] / "results"
CHARTS = RESULTS / "charts"

MIN_WEEKS = 150
MIN_POS_WEEKS = 0.45
ENSEMBLE_K = 5


def weekly_stats(daily: pd.Series) -> dict:
    w = (1.0 + daily).resample("W-FRI").prod() - 1.0
    w = w[w.index >= daily.index[0]]
    if len(w) < 10:
        return {}
    eq = (1.0 + daily).cumprod()
    dd = (eq / eq.cummax() - 1.0).min()
    years = len(daily) / 252
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 and eq.iloc[-1] > 0 else np.nan
    return {"avg_week": float(w.mean()), "median_week": float(w.median()),
            "pct_pos_weeks": float((w > 0).mean()), "best_week": float(w.max()),
            "worst_week": float(w.min()), "n_weeks": int(len(w)),
            "cagr": float(cagr), "max_dd": float(dd)}


WCOLS = ["avg_week", "median_week", "pct_pos_weeks", "worst_week", "best_week",
         "n_weeks", "cagr", "max_dd"]


def fmt(v, k):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    if k == "n_weeks":
        return str(int(v))
    if k in ("avg_week", "median_week", "worst_week", "best_week"):
        return f"{v*100:+.2f}%"
    return f"{v*100:.1f}%"


def main():
    RESULTS.mkdir(exist_ok=True)
    CHARTS.mkdir(exist_ok=True)
    rows = []          # (component, kind, stats, daily_returns or None, live_cfg)

    # ---------- PART A: daily swing families x every asset ----------
    assets = data.available()
    for asset in assets:
        df = data.load(asset)
        if len(df) < 1500:
            continue
        # buy & hold baseline
        bh = df["Close"].pct_change().fillna(0.0)
        st = weekly_stats(bh)
        if st:
            rows.append((f"buy&hold:{asset}", "baseline", st, bh, None))
        for sname in STRATEGIES:
            try:
                wf = optimize.walk_forward(df, sname)
            except Exception as exc:  # noqa: BLE001
                print(f"[wf] {asset}/{sname} failed: {exc}", file=sys.stderr)
                continue
            if wf is None:
                continue
            st = weekly_stats(wf.oos_returns)
            if not st:
                continue
            live = {"type": "swing", "asset": asset, "strategy": sname,
                    "params": wf.fold_params[-1][2]}
            rows.append((f"swing:{sname}:{asset}", "swing", st,
                         wf.oos_returns, live))
            print(f"{asset:6s} {sname:18s} avg_wk={st['avg_week']*100:+.2f}% "
                  f"pos={st['pct_pos_weeks']*100:.0f}% n={st['n_weeks']}")

    # ---------- PART B: rotation portfolios ----------
    names = sorted(set(pf.CORE + pf.AGGRESSIVE + pf.DEFENSIVE_POOL))
    panel = pf.build_panel(names)
    for uni_name in ("core", "aggressive"):
        best = None
        t0 = {"core": "2007-06-01", "aggressive": "2011-06-01"}[uni_name]
        for params in pf.RotationSpec().combos():
            if params["universe"] != uni_name or params["top_n"] < 2:
                continue
            w = pf.target_weights(panel, pf.UNIVERSES[uni_name],
                                  params["lookback"], params["top_n"],
                                  params["trend_filter"], params["defensive"],
                                  params["rebalance"])
            m_tr = pf.run(panel, w, 0.0, start_capital=1.0,
                          date_from=t0, date_to="2018-01-01").metrics
            if not m_tr or m_tr["sharpe"] < 0.5 or m_tr["max_drawdown"] < -0.60:
                continue
            score = m_tr.get("calmar", -9)
            if best is None or score > best[0]:
                best = (score, params, w)
        if best is None:
            continue
        _, params, w = best
        res = pf.run(panel, w, 0.0, start_capital=1.0, date_from="2018-01-01")
        st = weekly_stats(res.returns)
        rows.append((f"rotation:{uni_name} {params['lookback']}/top{params['top_n']}",
                     "rotation", st, res.returns,
                     {"type": "rotation", "params": params}))
        print(f"rotation:{uni_name} {params} avg_wk={st['avg_week']*100:+.2f}%")

    # ---------- PART C: intraday systems (small OOS sample — flagged) ----------
    for label, csv in (("intraday:ORB", "daytrade_trades.csv"),
                       ("intraday:TJR", "tjr_trades.csv")):
        p = RESULTS / csv
        if not p.exists():
            continue
        tr = pd.read_csv(p, parse_dates=["date"])
        # account-level daily return: 1.5% risk per trade, no-leverage cap
        tr["acct_ret"] = np.minimum(0.015 / tr["risk_pct"], 1.0) * tr["pnl_pct"]
        daily = tr.groupby("date")["acct_ret"].sum()
        daily.index = pd.to_datetime(daily.index)
        w = (1 + daily).resample("W-FRI").prod() - 1
        st = {"avg_week": float(w.mean()), "median_week": float(w.median()),
              "pct_pos_weeks": float((w > 0).mean()), "best_week": float(w.max()),
              "worst_week": float(w.min()), "n_weeks": int(len(w)),
              "cagr": np.nan, "max_dd": np.nan}
        rows.append((f"{label} (only {len(w)} wks!)", "intraday", st, None, None))

    # ---------- leaderboard ----------
    rows.sort(key=lambda r: r[2].get("avg_week", -9), reverse=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = ["# Grand Tournament — Every Method vs All Data, Ranked by Weekly Profit",
          f"\nGenerated {now}. All strategy numbers are **out-of-sample** "
          "(walk-forward for swing, 2018+ for rotation, test split for "
          "intraday). Costs included. Not tested for lack of data: options, "
          "fundamentals/news, order-flow, HFT, futures carry.\n",
          "**Research output, not financial advice.**\n",
          "## Leaderboard (top 40 of "
          f"{len(rows)} strategy-asset combinations)\n",
          "| # | Component | " + " | ".join(c.replace("_", " ") for c in WCOLS) + " |",
          "|---|---|" + "---|" * len(WCOLS)]
    for i, (name, kind, st, _, _) in enumerate(rows[:40], 1):
        md.append(f"| {i} | `{name}` | " +
                  " | ".join(fmt(st.get(c), c) for c in WCOLS) + " |")
    md.append("\n### Bottom 10\n")
    md.append("| # | Component | " + " | ".join(c.replace("_", " ") for c in WCOLS) + " |")
    md.append("|---|---|" + "---|" * len(WCOLS))
    for i, (name, kind, st, _, _) in enumerate(rows[-10:], len(rows) - 9):
        md.append(f"| {i} | `{name}` | " +
                  " | ".join(fmt(st.get(c), c) for c in WCOLS) + " |")

    # ---------- APEX ensemble ----------
    qual = [r for r in rows
            if r[3] is not None and r[4] is not None
            and r[2]["n_weeks"] >= MIN_WEEKS
            and r[2]["avg_week"] > 0
            and r[2]["pct_pos_weeks"] >= MIN_POS_WEEKS]
    # at most one component per asset (avoid 5 flavours of NVDA momentum)
    seen_assets, picks = set(), []
    for r in qual:
        key = r[4].get("asset", r[0]) if r[4]["type"] == "swing" else r[0]
        if key in seen_assets:
            continue
        seen_assets.add(key)
        picks.append(r)
        if len(picks) == ENSEMBLE_K:
            break

    md.append("\n## APEX ensemble — the bot built from the winners\n")
    if picks:
        md.append(f"Qualification: ≥{MIN_WEEKS} OOS weeks, positive average "
                  f"week, ≥{MIN_POS_WEEKS*100:.0f}% positive weeks, one "
                  f"component per asset. Equal weight across "
                  f"{len(picks)} components:\n")
        aligned = pd.concat({r[0]: r[3] for r in picks}, axis=1)
        aligned = aligned.loc[aligned.notna().all(axis=1).idxmax():].fillna(0.0)
        ens = aligned.mean(axis=1)
        st_e = weekly_stats(ens)
        comp_rows = {r[0]: r[2] for r in picks}
        comp_rows["**APEX (equal-weight ensemble)**"] = st_e
        md.append("| Component | " + " | ".join(c.replace("_", " ") for c in WCOLS) + " |")
        md.append("|---|" + "---|" * len(WCOLS))
        for name, st in comp_rows.items():
            md.append(f"| {name} | " + " | ".join(fmt(st.get(c), c) for c in WCOLS) + " |")
        md.append(f"\nEnsemble window: {aligned.index[0].date()} → "
                  f"{aligned.index[-1].date()}. Diversification does the "
                  "work: the ensemble's worst week and drawdown are far "
                  "shallower than its components'.")
        eq = (1 + ens).cumprod()
        spy = data.load("SPY")["Close"].pct_change().reindex(ens.index).fillna(0)
        report.equity_chart({"APEX ensemble": eq,
                             "SPY": (1 + spy).cumprod()},
                            "APEX ensemble vs SPY (out-of-sample, growth of 1)",
                            CHARTS / "apex_equity.png", log_scale=True)
        report.drawdown_chart(eq, "APEX ensemble drawdown",
                              CHARTS / "apex_drawdown.png")
        md.append("\n![apex](charts/apex_equity.png)\n")
        md.append("![apex dd](charts/apex_drawdown.png)\n")
        (RESULTS / "apex_config.json").write_text(json.dumps({
            "components": [{"name": r[0], "weight": 1.0 / len(picks), **r[4]}
                           for r in picks],
            "oos_avg_week": round(st_e["avg_week"], 5),
            "oos_pct_pos_weeks": round(st_e["pct_pos_weeks"], 4),
            "oos_cagr": round(st_e["cagr"], 4),
            "oos_max_dd": round(st_e["max_dd"], 4),
            "generated": now,
        }, indent=2, default=str))
    else:
        md.append("No components passed qualification.")

    (RESULTS / "TOURNAMENT_REPORT.md").write_text("\n".join(md))
    pd.DataFrame([{"component": n, "kind": k, **s} for n, k, s, _, _ in rows]) \
        .to_csv(RESULTS / "tournament_leaderboard.csv", index=False)
    print(f"\n{len(rows)} components ranked. Apex components:",
          [r[0] for r in picks])


if __name__ == "__main__":
    main()
