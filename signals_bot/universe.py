"""Liquid US day-trading universe: mega-caps, high-beta movers and index/commodity ETFs.

Chosen for tight spreads and high intraday volume — the two things that make
day-trading costs survivable. Survivorship bias is not a concern over the
~60-day intraday window Yahoo provides.
"""

UNIVERSE = [
    # index / commodity ETFs
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "XLE", "XLF", "SMH",
    # mega-cap tech
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AVGO", "NFLX", "AMD",
    # high-beta / heavily day-traded
    "COIN", "MSTR", "PLTR", "HOOD", "SOFI", "MARA", "RIOT", "SMCI", "MU", "INTC",
    # large-cap movers
    "QCOM", "CRM", "ORCL", "ADBE", "UBER", "ABNB", "SHOP", "SQ", "PYPL", "DIS",
    # industrials / energy / financials / consumer
    "BA", "CAT", "GE", "F", "GM", "XOM", "CVX", "JPM", "BAC", "GS",
    "WMT", "COST", "MCD", "NKE", "SBUX", "BABA", "NIO", "RIVN", "LCID", "AAL",
]
