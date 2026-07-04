# Trading Bots — Wealth-Builder (£50/week) · Swing Signals · Day-Trading Scanner

## ⭐ Wealth-Builder: momentum rotation with weekly contributions

The flagship bot. Every Monday it tells you where the week's **£50** goes;
on the first trading day of each month it rebalances into the **top-2
momentum assets** of an aggressive universe (QQQ, 2x/3x index ETFs, gold,
bitcoin), holding each only while it trades above its 10-month average —
otherwise the money retreats to 7-10y treasuries. Configuration was chosen
by an ex-ante rule (drawdown-adjusted growth on pre-2018 data, minimum two
holdings) and judged on 2018-2026 out-of-sample data — a window containing
the 2018 correction, the 2020 crash and the 2022 bear market:

| £50/week since 2018 | CAGR | Sharpe | Max DD | Final balance (£22.2k in) |
|---|---|---|---|---|
| **Momentum rotation (this bot)** | **21.0%** | 0.71 | **-54%** | **£71,282** |
| QQQ buy-every-week | 20.4% | 0.90 | -35% | £56,224 |
| SPY buy-every-week | 14.6% | 0.81 | -34% | £45,190 |
| 60/40 | 9.6% | 0.85 | -21% | £34,668 |

Read the -54% before the 21%: this configuration WILL halve at some point.
The weekly signal lands in
[`signals/portfolio_latest.md`](signals/portfolio_latest.md); the full
study (192 configurations, overfitting exhibits, month-by-month tables) is
in [`results/PORTFOLIO_REPORT.md`](results/PORTFOLIO_REPORT.md).

A daily trading-signals bot for gold (COMEX futures, `GC=F`), NVIDIA (`NVDA`)
and Tesla (`TSLA`). It backtests seven classic short-term strategy families
over each instrument's full daily history, selects the historically best
performer per instrument using **walk-forward out-of-sample validation** (not
in-sample curve fit), and then emits daily BUY / SELL / HOLD / FLAT signals
from the winning strategy.

> **Disclaimer:** educational research tool, not financial advice. Backtested
> performance does not guarantee future results. Trade at your own risk.

## Signals

The latest signals are always in [`signals/latest.md`](signals/latest.md),
with a running log in `signals/history.csv`. A GitHub Actions workflow
([`daily-signals.yml`](.github/workflows/daily-signals.yml)) refreshes data
and regenerates signals every weekday at 21:30 UTC, after the US close. Each
signal includes:

- the action (`BUY`, `SELL`, `HOLD LONG`, `STAY FLAT`, …) and target position
- the last close and a suggested protective stop (2 × ATR14)
- context: RSI(2) and the 200-day trend direction
- the strategy and parameters that produced it

## Strategy families tested

| Family | Style | Idea |
|---|---|---|
| `rsi_reversion` | mean reversion | Connors RSI(2): buy deep oversold inside a long-term uptrend, exit into strength |
| `double_low` | mean reversion | Connors Double-N: buy an N-day closing low in an uptrend, sell the N-day high |
| `bollinger_reversion` | mean reversion | buy closes below the lower band in an uptrend, exit at the middle band |
| `donchian_breakout` | momentum | Turtle-style channel breakout with a tighter exit channel |
| `ma_cross` | momentum | fast/slow moving-average crossover (EMA or SMA, optional shorts) |
| `macd_momentum` | momentum | long while MACD is above its signal line |
| `tsmom` | momentum | time-series momentum: long when the N-day return is positive |

## Backtest protocol

Run with `python scripts/run_backtest.py`; the full report lands in
[`results/BACKTEST_REPORT.md`](results/BACKTEST_REPORT.md).

1. **Execution model** — signals are computed on each bar's close and earn the
   next bar's close-to-close return (no look-ahead). Costs: 10 bps per unit of
   turnover (commission + slippage).
2. **Grid search** — every family is swept over its parameter grid on the full
   history. Reported for reference only; in-sample winners are overfit by
   construction.
3. **Walk-forward validation** — rolling 4-year train / 1-year test windows,
   stepped yearly. Parameters are re-fit each fold on training data only and
   applied unchanged to the unseen test year. Concatenated test years form the
   out-of-sample track record used for **all** strategy selection.
4. **Selection constraints** — a strategy must trade at least ~2×/year to
   qualify (no "one lucky trade" fits). The best family per instrument by
   out-of-sample Sharpe is written to `results/best_params.json`, with live
   parameters re-fit on the most recent 4 years.

## Day-trading module (whole-market scanner)

A second bot day-trades a **~50-symbol liquid US universe** (mega-caps,
high-beta movers, index/commodity ETFs) on 5-minute bars. Design targets:
**reward:risk fixed at 2:1 by construction** (every trade is a bracket:
target = 2× stop distance, flat by 15:55 ET) and a **win rate ≥ 40%**
enforced as a selection gate in *out-of-sample* data, not just in-sample.

- Strategy families: Opening Range Breakout (with relative-volume filter),
  gap-and-go continuation, VWAP pullback — long and short variants.
- Backtest: pooled across the whole universe, chronological 60/40
  train/test split, conservative fills (stop assumed to hit before target
  when both are inside one bar), 6 bps round-trip costs.
  Report: [`results/DAYTRADE_REPORT.md`](results/DAYTRADE_REPORT.md).
- Yahoo only serves ~60 days of 5-minute bars, so the
  `fetch-intraday` workflow runs nightly and **accumulates** history —
  re-run `scripts/run_daytrade_backtest.py` as the sample grows.
- Morning scan: the `intraday-signals` workflow runs after the opening
  range completes (10:35 ET) and writes bracket orders — entry, stop,
  2R target, and position size for a **£30 account risking 1.5% per
  trade** — to [`signals/daytrade_latest.md`](signals/daytrade_latest.md).

### TJR strategy (smart-money concepts) — tested, currently paper-only

A mechanical implementation of TJR's playbook (liquidity sweep → market
structure shift → fair-value-gap retrace, 2:1 bracket, NY morning) lives in
`signals_bot/tjr.py` and is scanned every morning into
[`signals/tjr_latest.md`](signals/tjr_latest.md). The verdict from the full
backtest ([`results/TJR_REPORT.md`](results/TJR_REPORT.md)): **all 16 rule
variants had negative expectancy after costs, in train AND test** (best:
41% win rate but −0.03R). A £100 account taking every signal ended at
£97.93 out-of-sample. The scanner is kept for paper-trading and study —
the pattern's edge, if any, appears to live in the discretion of the
trader, which rules can't capture. Do not fund it on this evidence.

**£30 reality check:** at 1.5% risk you are risking ~45p per trade, which
requires a zero-commission fractional-share broker (e.g. Trading 212
Invest; UK accounts are not subject to the US pattern-day-trader rule).
Expect small absolute numbers — this stake is for proving the process,
not for income.

## Repo layout

```
signals_bot/          # library: data, indicators, strategies, backtest, optimizer, signals
scripts/fetch_data.py    # refresh daily data/*.csv from Yahoo Finance
scripts/fetch_intraday.py         # accumulate 5m/15m bars for the universe
scripts/run_backtest.py  # daily-bar grid-search + walk-forward -> results/
scripts/run_daytrade_backtest.py  # pooled intraday study -> results/
scripts/run_signals.py   # emit swing signals -> signals/latest.md
scripts/run_daytrade_signals.py   # morning bracket-order scan -> signals/
data/                 # daily OHLCV CSVs (Date,Open,High,Low,Close,Volume)
results/              # backtest report, summary.csv, best_params.json, charts
signals/              # latest.md + history.csv
.github/workflows/    # fetch-data (manual) + daily-signals (cron)
```

## Running locally

```bash
pip install -r requirements.txt
python scripts/fetch_data.py     # needs internet access to Yahoo Finance
python scripts/run_backtest.py   # ~2-3 minutes
python scripts/run_signals.py
```

## Re-tuning

Re-run `scripts/run_backtest.py` periodically (e.g. quarterly): it re-selects
the best strategy per instrument from fresh walk-forward evidence and rewrites
`results/best_params.json`, which the daily signal job reads.
