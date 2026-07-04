"""Generate current trading signals from the backtested best strategies."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import data
from .indicators import atr, rsi, sma
from .strategies import positions

ROOT = Path(__file__).resolve().parents[1]
BEST_PARAMS = ROOT / "results" / "best_params.json"
SIGNALS_DIR = ROOT / "signals"

ACTIONS = {
    (0, 1): "BUY (open long)",
    (1, 0): "SELL (close long)",
    (0, -1): "SELL SHORT (open short)",
    (-1, 0): "COVER (close short)",
    (1, -1): "REVERSE: sell long, go short",
    (-1, 1): "REVERSE: cover short, go long",
    (1, 1): "HOLD LONG",
    (-1, -1): "HOLD SHORT",
    (0, 0): "STAY FLAT",
}


def generate(name: str, cfg: dict) -> dict:
    df = data.load(name)
    pos = positions(cfg["strategy"], df, cfg["params"])
    today = df.index[-1]
    cur = int(pos.iloc[-1])
    prev = int(pos.iloc[-2]) if len(pos) > 1 else 0
    close = float(df["Close"].iloc[-1])
    a14 = float(atr(df, 14).iloc[-1])
    r2 = float(rsi(df["Close"], 2).iloc[-1])
    trend = "UP" if close > float(sma(df["Close"], 200).iloc[-1]) else "DOWN"
    stop = close - 2.0 * a14 if cur > 0 else close + 2.0 * a14 if cur < 0 else None
    return {
        "instrument": name,
        "date": str(today.date()),
        "strategy": cfg["strategy"],
        "params": cfg["params"],
        "action": ACTIONS[(prev, cur)],
        "position": {1: "LONG", 0: "FLAT", -1: "SHORT"}[cur],
        "close": round(close, 2),
        "atr14": round(a14, 2),
        "suggested_stop": round(stop, 2) if stop is not None else None,
        "rsi2": round(r2, 1),
        "trend_200d": trend,
        "oos_sharpe": cfg.get("oos_sharpe"),
    }


def main():
    if not BEST_PARAMS.exists():
        raise SystemExit("results/best_params.json missing — run scripts/run_backtest.py first")
    cfg_all = json.loads(BEST_PARAMS.read_text())
    SIGNALS_DIR.mkdir(exist_ok=True)
    rows = [generate(name, cfg) for name, cfg in cfg_all.items()]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Trading Signals — {now}\n",
             "| Instrument | Data date | Signal | Position | Close | Stop (2×ATR) | RSI(2) | 200d trend | Strategy |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| **{r['instrument']}** | {r['date']} | **{r['action']}** | {r['position']} "
            f"| {r['close']} | {r['suggested_stop'] or '—'} | {r['rsi2']} "
            f"| {r['trend_200d']} | `{r['strategy']}` |")
    lines.append("\nStrategy parameters and out-of-sample stats: see "
                 "`results/best_params.json` and `results/BACKTEST_REPORT.md`.")
    lines.append("\n*Automated research output — not financial advice.*")
    (SIGNALS_DIR / "latest.md").write_text("\n".join(lines))

    hist_path = SIGNALS_DIR / "history.csv"
    hist_row = pd.DataFrame([{**{k: v for k, v in r.items() if k != "params"},
                              "params": json.dumps(r["params"])} for r in rows])
    if hist_path.exists():
        old = pd.read_csv(hist_path)
        hist_row = pd.concat([old, hist_row]).drop_duplicates(
            subset=["instrument", "date"], keep="last")
    hist_row.to_csv(hist_path, index=False)

    print("\n".join(lines))


if __name__ == "__main__":
    main()
