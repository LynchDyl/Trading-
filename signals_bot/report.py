"""Backtest report and chart generation."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# palette: fixed categorical order (blue, aqua, yellow, green, violet, red)
SERIES = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK_2,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "font.size": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

PCT_COLS = ["total_return", "cagr", "ann_vol", "max_drawdown", "win_rate",
            "avg_trade_return", "exposure"]


def fmt(v, col):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    if col in PCT_COLS:
        return f"{v * 100:,.1f}%"
    if col in ("n_trades",):
        return f"{int(v)}"
    return f"{v:,.2f}"


def metrics_table(rows: dict[str, dict], cols: list[str]) -> str:
    """rows: label -> metrics dict. Returns a markdown table."""
    header = "| Strategy | " + " | ".join(c.replace("_", " ") for c in cols) + " |"
    sep = "|" + "---|" * (len(cols) + 1)
    lines = [header, sep]
    for label, m in rows.items():
        cells = [fmt(m.get(c), c) for c in cols]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def equity_chart(curves: dict[str, pd.Series], title: str, path: Path,
                 log_scale: bool = False, ylabel: str = "Growth of $1"):
    """Plot equity curves (dict label -> equity series starting at 1.0)."""
    fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
    for i, (label, eq) in enumerate(curves.items()):
        color = SERIES[i % len(SERIES)]
        ax.plot(eq.index, eq.values, lw=2 if i == 0 else 1.6, color=color, label=label)
        # direct label at line end
        ax.annotate(f" {label}", xy=(eq.index[-1], eq.values[-1]),
                    color=color, fontsize=9, va="center")
    ax.set_title(title, loc="left", fontsize=12, color=INK)
    ax.set_ylabel(ylabel)
    if log_scale:
        ax.set_yscale("log")
    ax.grid(True, axis="y")
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    ax.margins(x=0.12)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def drawdown_chart(equity: pd.Series, title: str, path: Path):
    dd = equity / equity.cummax() - 1.0
    fig, ax = plt.subplots(figsize=(10, 2.8), dpi=150)
    ax.fill_between(dd.index, dd.values * 100, 0, color=SERIES[0], alpha=0.35, lw=0)
    ax.plot(dd.index, dd.values * 100, color=SERIES[0], lw=1.2)
    ax.set_title(title, loc="left", fontsize=12, color=INK)
    ax.set_ylabel("Drawdown %")
    ax.grid(True, axis="y")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
