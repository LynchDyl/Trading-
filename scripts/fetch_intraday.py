#!/usr/bin/env python3
"""Fetch intraday bars for the day-trading universe and save to data/intraday/.

Yahoo Finance limits: 5m/15m bars are only available for the trailing ~60
days. Runs on a machine with open internet (GitHub Actions runner). Existing
files are merged so history accumulates beyond 60 days as the workflow keeps
running.
"""
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from signals_bot.universe import UNIVERSE

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "intraday"
INTERVALS = {"5m": "60d", "15m": "60d"}


def fetch(ticker: str, interval: str, period: str) -> pd.DataFrame | None:
    df = yf.download(ticker, interval=interval, period=period,
                     auto_adjust=False, prepost=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df = df[df["Close"] > 0]
    df.index.name = "Datetime"
    return df


def merge_save(df: pd.DataFrame, path: Path):
    if path.exists():
        old = pd.read_csv(path, parse_dates=["Datetime"], index_col="Datetime")
        old.index = pd.to_datetime(old.index, utc=True)
        new = df.copy()
        new.index = pd.to_datetime(new.index, utc=True)
        df = pd.concat([old, new])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    else:
        df = df.copy()
        df.index = pd.to_datetime(df.index, utc=True)
    df.to_csv(path, float_format="%.6f")
    return df


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for ticker in UNIVERSE:
        for interval, period in INTERVALS.items():
            try:
                df = fetch(ticker, interval, period)
                if df is None:
                    print(f"[intraday] {ticker} {interval}: EMPTY", file=sys.stderr)
                    continue
                merged = merge_save(df, DATA_DIR / f"{ticker}_{interval}.csv")
                ok += 1
                print(f"[intraday] {ticker} {interval}: {len(merged)} rows "
                      f"({merged.index[0]} -> {merged.index[-1]})")
            except Exception as exc:  # noqa: BLE001
                print(f"[intraday] {ticker} {interval} failed: {exc}", file=sys.stderr)
            time.sleep(0.3)  # be polite to Yahoo
    print(f"[intraday] {ok} files updated")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
