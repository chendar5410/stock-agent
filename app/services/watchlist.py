"""Simple file-backed watchlist with z-score ranking."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

WATCHLIST_FILE = Path(__file__).parent.parent.parent / "watchlist.json"


def _load() -> list[str]:
    if WATCHLIST_FILE.exists():
        try:
            return json.loads(WATCHLIST_FILE.read_text())
        except Exception:
            pass
    return []


def _save(tickers: list[str]) -> None:
    WATCHLIST_FILE.write_text(json.dumps(tickers))


def get_watchlist() -> list[str]:
    return _load()


def add_ticker(symbol: str) -> list[str]:
    tickers = _load()
    symbol = symbol.upper().strip()
    if symbol not in tickers:
        tickers.append(symbol)
        _save(tickers)
    return tickers


def remove_ticker(symbol: str) -> list[str]:
    tickers = _load()
    symbol = symbol.upper().strip()
    tickers = [t for t in tickers if t != symbol]
    _save(tickers)
    return tickers
