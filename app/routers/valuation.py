"""Valuation API endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.services.chart_builder import build_compare_charts, build_single_chart
from app.services.data_fetcher import (
    VALID_PERIODS,
    MetricKey,
    fetch_valuation_series,
    validate_period,
    validate_ticker,
)

router = APIRouter(prefix="/api/valuation", tags=["valuation"])
logger = logging.getLogger(__name__)


@router.get("/chart")
async def get_valuation_chart(
    ticker: str = Query(..., description="Stock ticker symbol, e.g. AAPL"),
    metric: MetricKey = Query("forward_pe", description="Valuation metric"),
    period: str = Query(
        "5y", description=f"Time period — one of: {', '.join(sorted(VALID_PERIODS))}"
    ),
):
    """Return chart JSON and analytics for a single ticker."""
    # Validate inputs before touching yfinance
    try:
        validate_ticker(ticker)
        validate_period(period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        data = fetch_valuation_series(ticker, metric, period)
    except ValueError as exc:
        # Known data-availability problem — surface as 422 with the exact reason
        logger.warning("Data unavailable for %s/%s/%s: %s", ticker, metric, period, exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        logger.error("Runtime error for %s/%s: %s", ticker, metric, exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error for %s/%s", ticker, metric)
        raise HTTPException(status_code=500, detail=f"Unexpected server error: {exc}")

    chart_json = build_single_chart(data)
    return {
        "chart": chart_json,
        "summary": data["summary"],
        "zscore": data["zscore"],
        "current": data["current"],
        "stats": data["stats"],
        "symbol": data["symbol"],
        "label": data["label"],
    }


@router.get("/compare")
async def get_compare_charts(
    tickers: str = Query(..., description="Comma-separated list of tickers (max 6)"),
    metric: MetricKey = Query("forward_pe"),
    period: str = Query("5y"),
):
    """Return stacked valuation charts for multiple tickers."""
    # Validate period first
    try:
        validate_period(period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    raw = [t.strip() for t in tickers.split(",") if t.strip()]
    if not raw:
        raise HTTPException(status_code=422, detail="No tickers provided.")
    if len(raw) > 6:
        raise HTTPException(status_code=422, detail="Maximum 6 tickers allowed in compare mode.")

    # Validate all ticker symbols upfront; reject the whole request on any invalid input
    symbols: list[str] = []
    format_errors: list[str] = []
    for sym in raw:
        try:
            symbols.append(validate_ticker(sym))
        except ValueError as exc:
            format_errors.append(str(exc))
    if format_errors:
        raise HTTPException(status_code=422, detail=" | ".join(format_errors))

    # Fetch data; collect per-ticker errors but keep valid results
    results: list[dict] = []
    errors: list[str] = []
    for sym in symbols:
        try:
            results.append(fetch_valuation_series(sym, metric, period))
        except (ValueError, RuntimeError) as exc:
            logger.warning("Skipping %s in compare mode: %s", sym, exc)
            errors.append(f"{sym}: {exc}")
        except Exception as exc:
            logger.exception("Unexpected error for %s in compare", sym)
            errors.append(f"{sym}: unexpected error — {exc}")

    if not results:
        raise HTTPException(
            status_code=422,
            detail=(
                "No data could be retrieved for any of the provided tickers. "
                + " | ".join(errors)
            ),
        )

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
