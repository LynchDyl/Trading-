"""Load daily OHLCV data from the repo's data/ directory."""
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


def load(name: str, start: str | None = None) -> pd.DataFrame:
    """Load one instrument's OHLCV history, sorted and cleaned."""
    path = DATA_DIR / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No data file for {name}: {path}")
    df = pd.read_csv(path, parse_dates=["Date"], index_col="Date")
    df = df[REQUIRED_COLS].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Close"] > 0]
    if start:
        df = df.loc[start:]
    return df


def available() -> list[str]:
    return sorted(p.stem for p in DATA_DIR.glob("*.csv"))
