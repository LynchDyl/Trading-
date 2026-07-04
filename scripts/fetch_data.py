#!/usr/bin/env python3
"""Fetch daily OHLCV history for the bot's instruments and save to data/.

Runs on a machine with open internet (e.g. a GitHub Actions runner).
Falls back gracefully: an instrument that fails to download keeps its
previously committed CSV.
"""
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

# name -> yahoo ticker
INSTRUMENTS = {
    "GOLD": "GC=F",   # COMEX gold futures, front month
    "NVDA": "NVDA",
    "TSLA": "TSLA",
}

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def fetch(name: str, ticker: str) -> bool:
    df = yf.download(
        ticker,
        period="max",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    if df is None or df.empty:
        print(f"[fetch_data] {name} ({ticker}): EMPTY download, keeping old file", file=sys.stderr)
        return False
    # yfinance >=0.2 returns MultiIndex columns for single tickers too
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index.name = "Date"
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Close"] > 0]
    out = DATA_DIR / f"{name}.csv"
    df.to_csv(out, float_format="%.6f")
    print(f"[fetch_data] {name} ({ticker}): {len(df)} rows, {df.index[0].date()} -> {df.index[-1].date()}")
    return True


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for name, ticker in INSTRUMENTS.items():
        try:
            ok += fetch(name, ticker)
        except Exception as exc:  # noqa: BLE001 - keep going per instrument
            print(f"[fetch_data] {name} ({ticker}) failed: {exc}", file=sys.stderr)
    print(f"[fetch_data] {ok}/{len(INSTRUMENTS)} instruments updated")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
