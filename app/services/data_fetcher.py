"""Fetch historical valuation data from yfinance.

Supported metrics: trailing_pe, price_sales.
Supported periods: 1y, 2y, 3y, 5y, 10y.

All public functions raise ValueError for invalid input or unavailable data.
Nothing returns None and nothing falls back to synthetic data.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Only these two metrics are supported.
MetricKey = Literal["trailing_pe", "price_sales"]

METRIC_LABELS: dict[str, str] = {
    "trailing_pe": "Trailing P/E",
    "price_sales": "Price/Sales",
}

PERIOD_DAYS: dict[str, int] = {
    "1y": 365,
    "2y": 730,
    "3y": 1095,
    "5y": 1825,
    "10y": 3650,
}

VALID_PERIODS = frozenset(PERIOD_DAYS.keys())
VALID_METRICS = frozenset(METRIC_LABELS.keys())

# Accepts standard exchange symbols: AAPL, BRK.B, BRK-B, etc.
_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")

REQUEST_TIMEOUT = 20  # seconds

# yfinance log keywords that indicate a ticker was not found.
_YF_NOT_FOUND = (
    "no data found",
    "possibly delisted",
    "no timezone found",
    "symbol may be",
    "no price data found",
    "data not available",
)


# ── yfinance log capture ─────────────────────────────────────────────────────

class _YFLogCapture(logging.Handler):
    """Capture yfinance log output to detect 'ticker not found' messages."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage().lower())

    def found_not_found_hint(self) -> bool:
        return any(
            keyword in msg
            for msg in self.messages
            for keyword in _YF_NOT_FOUND
        )


def _yf_history_safe(ticker: yf.Ticker, days: int) -> pd.DataFrame:
    """
    Fetch price history using explicit start/end dates.

    yfinance only accepts a fixed set of period strings ("1y", "2y", …).
    Passing "730d" or "3650d" is silently ignored and falls back to a default.
    Using start= / end= datetime params is the correct way to fetch an
    arbitrary date range and guarantees the period the user requested.

    Raises ValueError on network failure or if yfinance logs a not-found hint.
    """
    capture = _YFLogCapture()
    yf_loggers = [
        logging.getLogger("yfinance"),
        logging.getLogger("yfinance.base"),
        logging.getLogger("yfinance.utils"),
        logging.getLogger("yfinance.ticker"),
    ]
    for lg in yf_loggers:
        lg.addHandler(capture)

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    hist = pd.DataFrame()
    exc_caught: Exception | None = None
    try:
        hist = ticker.history(
            start=start_dt,
            end=end_dt,
            auto_adjust=True,
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as exc:
        exc_caught = exc
    finally:
        for lg in yf_loggers:
            lg.removeHandler(capture)

    if exc_caught is not None:
        raise ValueError(
            f"Failed to fetch price data for '{ticker.ticker}': {exc_caught}. "
            "Check your network connection and verify the ticker symbol."
        ) from exc_caught

    if capture.found_not_found_hint():
        detail = " | ".join(capture.messages) or "(no detail)"
        logger.warning("yfinance not-found hint for %s: %s", ticker.ticker, detail)
        raise ValueError(
            f"Ticker '{ticker.ticker}' was not found. "
            "The symbol may be invalid, delisted, or unavailable in this data source."
        )

    return hist


# ── Input validation ─────────────────────────────────────────────────────────

def validate_ticker(symbol: str) -> str:
    """Normalise and validate a ticker symbol. Raises ValueError on bad input."""
    s = symbol.strip().upper()
    if not s:
        raise ValueError("Ticker symbol cannot be empty.")
    if not _TICKER_RE.match(s):
        raise ValueError(
            f"'{symbol}' is not a valid ticker symbol. "
            "Use only letters, digits, dots, or hyphens (max 10 characters)."
        )
    return s


def validate_period(period: str) -> str:
    """Validate time period string. Raises ValueError on bad input."""
    if period not in VALID_PERIODS:
        raise ValueError(
            f"'{period}' is not a valid period. "
            f"Choose from: {', '.join(sorted(VALID_PERIODS))}."
        )
    return period


def validate_metric(metric: str) -> str:
    """Validate metric key. Raises ValueError on unsupported metric."""
    if metric not in VALID_METRICS:
        raise ValueError(
            f"'{metric}' is not a supported metric. "
            f"Choose from: {', '.join(sorted(VALID_METRICS))}."
        )
    return metric


# ── Internal helpers ─────────────────────────────────────────────────────────

def _strip_tz(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Remove timezone from a DatetimeIndex."""
    idx = pd.to_datetime(idx)
    return idx.tz_convert(None) if idx.tz is not None else idx


def _get_price_history(ticker: yf.Ticker, days: int) -> pd.Series:
    """
    Return daily closing prices for exactly `days` calendar days back from today.

    Raises ValueError if the ticker is unknown, delisted, unreachable,
    or has fewer than 5 trading days of history.
    """
    hist = _yf_history_safe(ticker, days)

    if hist.empty:
        raise ValueError(
            f"No price history returned for '{ticker.ticker}'. "
            "The symbol may be invalid, delisted, or not yet traded."
        )

    if len(hist) < 5:
        raise ValueError(
            f"Only {len(hist)} trading day(s) found for '{ticker.ticker}'. "
            "Insufficient history to compute a valuation series."
        )

    s = hist["Close"]
    s.index = _strip_tz(s.index)
    return s


def _get_quarterly_financials(ticker: yf.Ticker) -> pd.DataFrame:
    """
    Return quarterly financials with tz-naive column dates.

    Raises ValueError for ETFs, SPACs, and tickers without GAAP filings.
    """
    try:
        fin = ticker.quarterly_financials
    except Exception as exc:
        raise ValueError(f"Failed to fetch quarterly financials: {exc}.") from exc

    if fin is None or fin.empty:
        raise ValueError(
            "Quarterly financial statements are not available for this ticker. "
            "This is common for ETFs, SPACs, and foreign stocks without US filings."
        )

    fin.columns = _strip_tz(pd.to_datetime(fin.columns))
    return fin


def _row(fin: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    for name in candidates:
        if name in fin.index:
            return fin.loc[name].sort_index().dropna()
    return None


def _get_shares(ticker: yf.Ticker) -> float:
    """Return shares outstanding. Raises ValueError if unavailable."""
    try:
        fi = ticker.fast_info
        shares = getattr(fi, "shares", None) or getattr(fi, "sharesOutstanding", None)
        if shares and shares > 0:
            return float(shares)
    except Exception as exc:
        logger.debug("fast_info shares unavailable for %s: %s", ticker.ticker, exc)

    try:
        info = ticker.info
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        if shares and shares > 0:
            return float(shares)
    except Exception as exc:
        logger.debug("info shares unavailable for %s: %s", ticker.ticker, exc)

    raise ValueError(
        "Shares outstanding data is not available for this ticker."
    )


# ── Metric series builders ────────────────────────────────────────────────────

def _quarterly_eps_ttm(ticker: yf.Ticker) -> pd.Series:
    """TTM EPS from quarterly net income. Raises ValueError if unavailable."""
    fin = _get_quarterly_financials(ticker)

    ni = _row(fin, [
        "Net Income",
        "Net Income Common Stockholders",
        "Net Income From Continuing Operations",
    ])
    if ni is None:
        raise ValueError(
            "Net Income not found in quarterly financials. "
            "Cannot compute Trailing P/E for this ticker."
        )

    shares = _get_shares(ticker)
    ni_ttm = ni.rolling(4, min_periods=4).sum().dropna()
    if ni_ttm.empty:
        raise ValueError(
            "Fewer than 4 quarters of earnings history available. "
            "Try a shorter period or use Price/Sales instead."
        )

    eps_ttm = ni_ttm / shares
    eps_ttm.index = _strip_tz(pd.to_datetime(eps_ttm.index))
    return eps_ttm


def _quarterly_revenue_ttm(ticker: yf.Ticker) -> pd.Series:
    """TTM Revenue from quarterly financials. Raises ValueError if unavailable."""
    fin = _get_quarterly_financials(ticker)

    rev = _row(fin, ["Total Revenue", "Revenue", "Net Revenue"])
    if rev is None:
        raise ValueError(
            "Revenue data not found in quarterly financials. "
            "Price/Sales cannot be computed for this ticker."
        )

    ttm = rev.rolling(4, min_periods=4).sum().dropna()
    if ttm.empty:
        raise ValueError(
            "Fewer than 4 quarters of revenue history available. "
            "Try a shorter period."
        )

    ttm.index = _strip_tz(pd.to_datetime(ttm.index))
    return ttm


def _trailing_pe_series(ticker: yf.Ticker, price: pd.Series) -> pd.Series:
    """Price / TTM EPS, forward-filled from quarterly to daily."""
    eps_ttm = _quarterly_eps_ttm(ticker)
    eps_daily = eps_ttm.reindex(price.index, method="ffill")
    pe = price / eps_daily
    return pe.replace([np.inf, -np.inf], np.nan).dropna()


def _price_sales_series(ticker: yf.Ticker, price: pd.Series) -> pd.Series:
    """Market cap / TTM Revenue, forward-filled from quarterly to daily."""
    rev_ttm = _quarterly_revenue_ttm(ticker)
    shares = _get_shares(ticker)
    rev_daily = rev_ttm.reindex(price.index, method="ffill")
    market_cap = price * shares
    ps = market_cap / rev_daily
    return ps.replace([np.inf, -np.inf], np.nan).dropna()


# ── Series cleaning ───────────────────────────────────────────────────────────

def _clean_series(series: pd.Series, symbol: str, metric: str) -> pd.Series:
    """
    Drop non-positive values and outliers. Raises ValueError if < 20 points remain.
    """
    series = series.replace([np.inf, -np.inf], np.nan).dropna()
    series = series[series > 0]

    q1, q3 = series.quantile(0.05), series.quantile(0.95)
    iqr = q3 - q1
    if iqr > 0:
        series = series[(series >= q1 - 5 * iqr) & (series <= q3 + 5 * iqr)]

    if len(series) < 20:
        label = METRIC_LABELS.get(metric, metric)
        raise ValueError(
            f"Only {len(series)} valid data points remain for {symbol} {label}. "
            "The company may have had negative earnings throughout this period. "
            "Try Price/Sales or a shorter time period."
        )

    return series


# ── Result builder ────────────────────────────────────────────────────────────

def _build_result(
    symbol: str, metric: str, period: str, series: pd.Series
) -> dict:
    label = METRIC_LABELS[metric]
    mean = float(series.mean())
    std = float(series.std())
    current = float(series.iloc[-1])
    zscore = (current - mean) / std if std > 0 else 0.0
    summary = _generate_summary(symbol, label, period, mean, std, current, zscore)

    dates = (
        [d.strftime("%Y-%m-%d") for d in series.index]
        if hasattr(series.index[0], "strftime")
        else [str(d) for d in series.index]
    )

    return {
        "symbol": symbol,
        "metric": metric,
        "label": label,
        "period": period,
        "points": len(series),
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
    }


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_valuation_series(
    symbol: str,
    metric: str,
    period: str = "5y",
) -> dict:
    """
    Fetch and return a historical valuation series for a ticker.

    Raises ValueError for invalid input, unknown tickers, or unavailable metrics.
    Raises RuntimeError for unexpected pipeline failures.

    Returns a dict with keys:
        symbol, metric, label, period, points,
        dates, values, stats, current, zscore, summary
    """
    symbol = validate_ticker(symbol)
    period = validate_period(period)
    metric = validate_metric(metric)
    days = PERIOD_DAYS[period]

    logger.info("Fetching %s / %s / %s (%d days)", symbol, metric, period, days)

    ticker = yf.Ticker(symbol)
    price = _get_price_history(ticker, days)

    try:
        if metric == "trailing_pe":
            series = _trailing_pe_series(ticker, price)
        elif metric == "price_sales":
            series = _price_sales_series(ticker, price)
        else:
            # validate_metric already blocks this; belt-and-suspenders guard.
            raise ValueError(f"Unsupported metric: '{metric}'.")
    except ValueError:
        raise
    except Exception as exc:
        logger.exception("Unexpected error computing %s for %s", metric, symbol)
        raise RuntimeError(
            f"Unexpected error computing {METRIC_LABELS.get(metric, metric)} "
            f"for {symbol}: {exc}"
        ) from exc

    series = _clean_series(series, symbol, metric)

    logger.info(
        "OK %s/%s/%s: %d pts | current=%.2f | z=%+.2f",
        symbol, metric, period, len(series),
        series.iloc[-1],
        (series.iloc[-1] - series.mean()) / series.std(),
    )

    return _build_result(symbol, metric, period, series)


# ── Summary text ──────────────────────────────────────────────────────────────

def _generate_summary(
    symbol: str, label: str, period: str,
    mean: float, std: float, current: float, zscore: float,
) -> str:
    period_label = {
        "1y": "1-year", "2y": "2-year", "3y": "3-year",
        "5y": "5-year", "10y": "10-year",
    }.get(period, period)

    abs_z = abs(zscore)
    direction = "above" if zscore > 0 else "below"

    if abs_z < 0.5:
        return (
            f"{symbol} is trading roughly in line with "
            f"its {period_label} average {label} of {mean:.1f}x."
        )

    if abs_z < 1.0:
        val_word = "slightly expensive" if zscore > 0 else "slightly cheap"
    elif abs_z < 2.0:
        val_word = "expensive" if zscore > 0 else "cheap"
    else:
        val_word = "significantly overvalued" if zscore > 0 else "significantly undervalued"

    return (
        f"{symbol} is trading {abs_z:.1f} standard deviation{'s' if abs_z != 1 else ''} "
        f"{direction} its {period_label} average {label} ({val_word}). "
        f"Current: {current:.1f}x | Mean: {mean:.1f}x | Z-score: {zscore:+.2f}."
    )
