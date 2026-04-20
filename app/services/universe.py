"""Ticker universe for the weekly picker.

Strategy:
- Stocks: 50 liquid large-caps across sectors (curated S&P 500 subset).
  Falls back to this list if FMP index-constituents endpoint is unavailable.
- Crypto ETFs: spot bitcoin/ether ETFs + futures-based ETFs.
"""
from __future__ import annotations

CRYPTO_ETFS: list[str] = [
    "IBIT",   # iShares Bitcoin Trust
    "FBTC",   # Fidelity Wise Origin Bitcoin Fund
    "BITO",   # ProShares Bitcoin Strategy (futures)
    "ETHA",   # iShares Ethereum Trust
    "ETHE",   # Grayscale Ethereum Trust
    "BITB",   # Bitwise Bitcoin ETF
]

# Curated large-cap liquid universe. Chosen to be sector-diverse and to have
# reliable fundamentals data so the z-score picker can rank them.
CORE_STOCKS: list[str] = [
    # Tech / mega-cap
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "ORCL", "CRM",
    "ADBE", "AMD", "INTC", "QCOM", "CSCO", "NFLX", "PYPL", "UBER", "SHOP",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BLK", "SCHW",
    # Healthcare
    "UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR",
    # Industrials / Energy
    "CAT", "DE", "BA", "GE", "XOM", "CVX", "COP",
    # Consumer
    "WMT", "COST", "HD", "MCD", "NKE", "KO", "PEP", "DIS", "SBUX",
]


def full_universe() -> list[str]:
    return CORE_STOCKS + CRYPTO_ETFS


def is_crypto_etf(symbol: str) -> bool:
    return symbol.upper() in {s.upper() for s in CRYPTO_ETFS}
