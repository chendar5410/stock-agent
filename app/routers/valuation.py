"""Valuation API endpoints."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.chart_builder import build_compare_charts, build_single_chart
from app.services.data_fetcher import MetricKey, fetch_valuation_series

router = APIRouter(prefix="/api/valuation", tags=["valuation"])
logger = logging.getLogger(__name__)


@router.get("/chart")
async def get_valuation_chart(
    ticker: str = Query(..., description="Stock ticker symbol"),
    metric: MetricKey = Query("forward_pe", description="Valuation metric"),
    period: str = Query("5y", description="Time period: 1y, 2y, 3y, 5y, 10y"),
):
    """Return chart JSON and analytics for a single ticker."""
    try:
        data = fetch_valuation_series(ticker, metric, period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Error fetching %s/%s", ticker, metric)
        raise HTTPException(status_code=500, detail=f"Data error: {exc}")

    chart_json = build_single_chart(data)
    return {
        "chart": chart_json,
        "summary": data["summary"],
        "zscore": data["zscore"],
        "current": data["current"],
        "stats": data["stats"],
        "symbol": data["symbol"],
        "label": data["label"],
        "demo": data.get("demo", False),
    }


@router.get("/compare")
async def get_compare_charts(
    tickers: str = Query(..., description="Comma-separated list of tickers"),
    metric: MetricKey = Query("forward_pe"),
    period: str = Query("5y"),
):
    """Return stacked charts for multiple tickers."""
    symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not symbols:
        raise HTTPException(status_code=422, detail="No tickers provided.")
    if len(symbols) > 6:
        raise HTTPException(status_code=422, detail="Maximum 6 tickers for compare mode.")

    results = []
    errors = []
    for sym in symbols:
        try:
            results.append(fetch_valuation_series(sym, metric, period))
        except Exception as exc:
            errors.append(f"{sym}: {exc}")

    if not results:
        raise HTTPException(status_code=422, detail="Could not fetch data. " + " | ".join(errors))

    chart_json = build_compare_charts(results)
    return {
        "chart": chart_json,
        "results": [
            {
                "symbol": d["symbol"],
                "label": d["label"],
                "current": d["current"],
                "zscore": d["zscore"],
                "summary": d["summary"],
                "stats": d["stats"],
            }
            for d in results
        ],
        "errors": errors,
    }
