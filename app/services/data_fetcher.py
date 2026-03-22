"""Fetch historical valuation data from yfinance with demo-data fallback."""
from __future__ import annotations

import logging
import os
from typing import Literal

import numpy as np
import pandas as pd
import yfinance as yf

from app.services.demo_data import generate_series as _demo_series

logger = logging.getLogger(__name__)

MetricKey = Literal["forward_pe", "trailing_pe", "ev_ebitda", "price_sales"]

METRIC_LABELS: dict[MetricKey, str] = {
    "forward_pe": "Forward P/E",
    "trailing_pe": "Trailing P/E",
    "ev_ebitda": "EV/EBITDA",
    "price_sales": "Price/Sales",
}

PERIOD_DAYS: dict[str, int] = {
    "1y": 365,
    "2y": 730,
    "3y": 1095,
    "5y": 1825,
    "10y": 3650,
}


def _get_price_history(ticker: yf.Ticker, days: int) -> pd.Series:
    """Return daily closing prices for the past `days` days."""
    hist = ticker.history(period=f"{days}d", auto_adjust=True)
    if hist.empty:
        raise ValueError("No price history available.")
    return hist["Close"]


def _quarterly_eps_ttm(ticker: yf.Ticker) -> pd.Series | None:
    """Build a trailing-twelve-month EPS series from quarterly financials."""
    try:
        fin = ticker.quarterly_financials
        if fin is None or fin.empty:
            return None
        # Net Income row
        ni_row = None
        for label in ["Net Income", "Net Income Common Stockholders"]:
            if label in fin.index:
                ni_row = fin.loc[label]
                break
        if ni_row is None:
            return None
        shares_row = None
        info = ticker.fast_info
        shares = getattr(info, "shares", None) or getattr(info, "sharesOutstanding", None)
        if shares is None:
            return None
        ni_row = ni_row.sort_index()
        # Rolling 4-quarter sum → TTM EPS
        ni_ttm = ni_row.rolling(4).sum()
        ni_ttm = ni_ttm.dropna()
        eps_ttm = ni_ttm / shares
        eps_ttm.index = pd.to_datetime(eps_ttm.index)
        return eps_ttm
    except Exception as exc:
        logger.debug("Could not compute TTM EPS: %s", exc)
        return None


def _forward_eps_series(ticker: yf.Ticker, price: pd.Series) -> pd.Series | None:
    """Best-effort forward EPS from analyst estimates (single point) blended with history."""
    try:
        info = ticker.info
        fwd_eps = info.get("forwardEps")
        if fwd_eps and fwd_eps > 0:
            # Use a constant forward EPS so the ratio reflects price moves only
            fwd_pe = price / fwd_eps
            return fwd_pe
    except Exception as exc:
        logger.debug("Could not compute forward P/E: %s", exc)
    return None


def _ev_ebitda_series(ticker: yf.Ticker, price: pd.Series) -> pd.Series | None:
    """Build EV/EBITDA series using quarterly data."""
    try:
        info = ticker.info
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        total_debt = info.get("totalDebt", 0) or 0
        cash = info.get("totalCash", 0) or 0
        ebitda = info.get("ebitda")
        if not ebitda or ebitda <= 0 or not shares:
            return None
        # EV = market_cap + debt - cash; use daily price for market_cap
        ev = price * shares + total_debt - cash
        ev_ebitda = ev / ebitda
        return ev_ebitda
    except Exception as exc:
        logger.debug("Could not compute EV/EBITDA: %s", exc)
    return None


def _price_sales_series(ticker: yf.Ticker, price: pd.Series) -> pd.Series | None:
    """Build Price/Sales series."""
    try:
        info = ticker.info
        revenue_per_share = info.get("revenuePerShare")
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        revenue = info.get("totalRevenue")
        if revenue_per_share and revenue_per_share > 0:
            return price / revenue_per_share
        if revenue and revenue > 0 and shares and shares > 0:
            return (price * shares) / revenue
    except Exception as exc:
        logger.debug("Could not compute P/S: %s", exc)
    return None


def _trailing_pe_series(ticker: yf.Ticker, price: pd.Series) -> pd.Series | None:
    """Build trailing P/E from TTM EPS or info fallback."""
    eps_ttm = _quarterly_eps_ttm(ticker)
    if eps_ttm is not None and not eps_ttm.empty:
        # Reindex EPS to daily, forward-fill quarterly values
        eps_daily = eps_ttm.reindex(price.index, method="ffill")
        pe = price / eps_daily
        pe = pe.replace([np.inf, -np.inf], np.nan).dropna()
        if len(pe) > 10:
            return pe
    # fallback: use trailingEps from info
    try:
        trailing_eps = ticker.info.get("trailingEps")
        if trailing_eps and trailing_eps > 0:
            return price / trailing_eps
    except Exception:
        pass
    return None


def _build_result(symbol: str, metric: MetricKey, period: str, series: pd.Series) -> dict:
    """Compute stats and build the return dict from a clean series."""
    label = METRIC_LABELS[metric]
    mean = float(series.mean())
    std = float(series.std())
    current = float(series.iloc[-1])
    zscore = (current - mean) / std if std > 0 else 0.0
    summary = _generate_summary(symbol, label, period, mean, std, current, zscore)

    dates: list[str]
    if hasattr(series.index[0], "strftime"):
        dates = [d.strftime("%Y-%m-%d") for d in series.index]
    else:
        dates = [str(d) for d in series.index]

    return {
        "symbol": symbol.upper(),
        "metric": metric,
        "label": label,
        "period": period,
        "dates": dates,
        "values": [round(v, 4) for v in series.tolist()],
        "stats": {
            "mean": round(mean, 4),
            "std": round(std, 4),
            "plus1": round(mean + std, 4),
            "minus1": round(mean - std, 4),
            "plus2": round(mean + 2 * std, 4),
            "minus2": round(mean - 2 * std, 4),
        },
        "current": round(current, 4),
        "zscore": round(zscore, 3),
        "summary": summary,
        "demo": False,
    }


def _fetch_live(symbol: str, metric: MetricKey, days: int) -> pd.Series | None:
    """Try to fetch live data; return None on any network/data error."""
    try:
        ticker = yf.Ticker(symbol.upper())
        price = _get_price_history(ticker, days)
        price.index = pd.to_datetime(price.index).tz_localize(None)

        if metric == "forward_pe":
            series = _forward_eps_series(ticker, price)
        elif metric == "trailing_pe":
            series = _trailing_pe_series(ticker, price)
        elif metric == "ev_ebitda":
            series = _ev_ebitda_series(ticker, price)
        elif metric == "price_sales":
            series = _price_sales_series(ticker, price)
        else:
            return None

        if series is None or series.empty:
            return None

        # Clean
        series = series.replace([np.inf, -np.inf], np.nan).dropna()
        q1, q3 = series.quantile(0.05), series.quantile(0.95)
        iqr = q3 - q1
        series = series[(series >= q1 - 5 * iqr) & (series <= q3 + 5 * iqr)]
        series = series[series > 0]
        return series if len(series) >= 10 else None
    except Exception as exc:
        logger.debug("Live fetch failed for %s/%s: %s", symbol, metric, exc)
        return None


def fetch_valuation_series(
    symbol: str,
    metric: MetricKey,
    period: str = "5y",
) -> dict:
    """
    Fetch historical valuation series for a ticker.

    Tries live yfinance data first; falls back to realistic demo data when
    the network is unavailable (sandbox / offline mode).

    Returns a dict with keys:
        symbol, metric, label, dates, values, stats, current, zscore, summary, demo
    """
    days = PERIOD_DAYS.get(period, 1825)
    use_demo = os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes")

    series: pd.Series | None = None
    if not use_demo:
        series = _fetch_live(symbol, metric, days)

    if series is not None:
        result = _build_result(symbol, metric, period, series)
        return result

    # ── demo / offline fallback ──────────────────────────────────────────
    logger.info("Using demo data for %s / %s", symbol, metric)
    dates_raw, values_raw = _demo_series(symbol, metric, days)
    series = pd.Series(values_raw, index=pd.to_datetime(dates_raw))
    result = _build_result(symbol, metric, period, series)
    result["demo"] = True
    return result


def _generate_summary(
    symbol: str, label: str, period: str, mean: float, std: float, current: float, zscore: float
) -> str:
    period_label = {
        "1y": "1-year", "2y": "2-year", "3y": "3-year", "5y": "5-year", "10y": "10-year"
    }.get(period, period)

    abs_z = abs(zscore)
    direction = "above" if zscore > 0 else "below"

    if abs_z < 0.5:
        assessment = "roughly in line with"
    elif abs_z < 1.0:
        valuation = "slightly expensive" if zscore > 0 else "slightly cheap"
        assessment = f"{valuation} relative to"
    elif abs_z < 2.0:
        valuation = "expensive" if zscore > 0 else "cheap"
        assessment = f"{valuation} relative to"
    else:
        valuation = "significantly overvalued" if zscore > 0 else "significantly undervalued"
        assessment = f"{valuation} compared to"

    if abs_z < 0.5:
        tail = f"its {period_label} average {label} of {mean:.1f}x."
    else:
        tail = (
            f"its {period_label} average {label}. "
            f"Current: {current:.1f}x vs. mean: {mean:.1f}x "
            f"(z-score: {zscore:+.2f})."
        )

    if abs_z < 0.5:
        return f"{symbol} is trading {assessment} {tail}"
    else:
        return (
            f"{symbol} is trading {abs_z:.1f} standard deviation{'s' if abs_z != 1 else ''} "
            f"{direction} its {period_label} average {label}, "
            f"which suggests it is {assessment.split(' relative to')[0].split(' compared to')[0]} "
            f"relative to its own history. "
            f"Current: {current:.1f}x | Mean: {mean:.1f}x | Z-score: {zscore:+.2f}."
        )
