#!/usr/bin/env python3
"""Generate today's signals from the backtested strategy configuration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals_bot.signals import main

if __name__ == "__main__":
    main()
