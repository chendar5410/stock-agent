"""Watchlist API endpoints."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.data_fetcher import MetricKey, fetch_valuation_series
from app.services.watchlist import add_ticker, get_watchlist, remove_ticker

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])
logger = logging.getLogger(__name__)


class TickerBody(BaseModel):
    ticker: str


@router.get("")
async def list_watchlist():
    return {"tickers": get_watchlist()}


@router.post("/add")
async def watchlist_add(body: TickerBody):
    tickers = add_ticker(body.ticker)
    return {"tickers": tickers}


@router.delete("/remove/{ticker}")
async def watchlist_remove(ticker: str):
    tickers = remove_ticker(ticker)
    return {"tickers": tickers}


@router.get("/ranked")
async def watchlist_ranked(
    metric: MetricKey = "forward_pe",
    period: str = "5y",
):
    """Return watchlist tickers ranked by z-score (cheapest first)."""
    tickers = get_watchlist()
    if not tickers:
        return {"ranked": [], "errors": []}

    results = []
    errors = []

    async def _fetch(sym: str):
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, fetch_valuation_series, sym, metric, period)
            results.append(
                {
                    "symbol": data["symbol"],
                    "label": data["label"],
                    "current": data["current"],
                    "zscore": data["zscore"],
                    "mean": data["stats"]["mean"],
                    "std": data["stats"]["std"],
                    "summary": data["summary"],
                }
            )
        except Exception as exc:
            errors.append(f"{sym}: {exc}")

    await asyncio.gather(*[_fetch(t) for t in tickers])
    ranked = sorted(results, key=lambda x: x["zscore"])
    return {"ranked": ranked, "errors": errors}
