"""Fetch historical valuation data from yfinance.

All public functions raise ValueError for invalid input or unavailable data,
and RuntimeError for unexpected failures. Nothing returns None or falls back
to synthetic data — callers receive a real series or an explicit exception.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

import numpy as np
import pandas as pd
import yfinance as yf

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

VALID_PERIODS = frozenset(PERIOD_DAYS.keys())

# Accepts standard exchange symbols: AAPL, BRK.B, BRK-B, 005930.KS, etc.
_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")

REQUEST_TIMEOUT = 20  # seconds passed to yfinance history calls


# ── Input validation ────────────────────────────────────────────────────────

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


# ── Internal helpers ────────────────────────────────────────────────────────

def _strip_tz(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Remove timezone info from a DatetimeIndex, converting UTC first if needed."""
    idx = pd.to_datetime(idx)
    return idx.tz_convert(None) if idx.tz is not None else idx


def _get_price_history(ticker: yf.Ticker, days: int) -> pd.Series:
    """
    Return daily closing prices for the past `days` days.

    Raises ValueError if the ticker is unknown, delisted, or unreachable.
    """
    try:
        hist = ticker.history(period=f"{days}d", auto_adjust=True, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        raise ValueError(
            f"Failed to fetch price data: {exc}. "
            "Check your network connection and verify the ticker symbol."
        ) from exc

    if hist.empty:
        raise ValueError(
            "No price history found. The ticker may be invalid, delisted, "
            "or not yet traded. Please check the symbol and try again."
        )

    s = hist["Close"]
    s.index = _strip_tz(s.index)
    return s


def _get_quarterly_financials(ticker: yf.Ticker) -> pd.DataFrame:
    """
    Return quarterly financials DataFrame with tz-naive column dates.

    Raises ValueError if unavailable (ETFs, SPACs, foreign stocks without GAAP filings).
    """
    try:
        fin = ticker.quarterly_financials
    except Exception as exc:
        raise ValueError(
            f"Failed to fetch quarterly financials: {exc}."
        ) from exc

    if fin is None or fin.empty:
        raise ValueError(
            "Quarterly financial statements are not available for this ticker. "
            "This is common for ETFs, SPACs, and foreign stocks without US filings. "
            "Try Price/Sales or a different ticker."
        )

    fin.columns = _strip_tz(pd.to_datetime(fin.columns))
    return fin


def _row(fin: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    """Return the first matching row from a financials DataFrame sorted by date, or None."""
    for name in candidates:
        if name in fin.index:
            return fin.loc[name].sort_index().dropna()
    return None


def _get_shares(ticker: yf.Ticker) -> float:
    """
    Return shares outstanding.

    Raises ValueError if unavailable.
    """
    # fast_info is cheaper than info
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
        "Shares outstanding data is not available for this ticker. "
        "Cannot compute market-cap-based valuation metrics."
    )


# ── Quarterly TTM series builders ───────────────────────────────────────────

def _quarterly_eps_ttm(ticker: yf.Ticker) -> pd.Series:
    """
    Build a trailing-twelve-month EPS series from quarterly net income.

    Raises ValueError if earnings data is unavailable or history is too short.
    """
    fin = _get_quarterly_financials(ticker)

    ni = _row(fin, [
        "Net Income",
        "Net Income Common Stockholders",
        "Net Income From Continuing Operations",
    ])
    if ni is None:
        raise ValueError(
            "Net Income not found in quarterly financials. "
            "Cannot compute earnings-based valuation metrics for this ticker."
        )

    shares = _get_shares(ticker)

    ni_ttm = ni.rolling(4, min_periods=4).sum().dropna()
    if ni_ttm.empty:
        raise ValueError(
            "Fewer than 4 quarters of earnings history are available. "
            "Try a shorter time period or choose a different metric."
        )

    eps_ttm = ni_ttm / shares
    eps_ttm.index = _strip_tz(pd.to_datetime(eps_ttm.index))
    return eps_ttm


def _quarterly_ebitda_ttm(ticker: yf.Ticker) -> pd.Series:
    """
    Build a trailing-twelve-month EBITDA series from quarterly financials.

    Tries a direct EBITDA row first, then computes Operating Income + D&A.
    Raises ValueError if neither path yields sufficient data.
    """
    fin = _get_quarterly_financials(ticker)

    # Attempt 1: direct EBITDA row
    ebitda = _row(fin, ["EBITDA", "Ebitda"])
    if ebitda is not None:
        ttm = ebitda.rolling(4, min_periods=4).sum().dropna()
        if not ttm.empty:
            ttm.index = _strip_tz(pd.to_datetime(ttm.index))
            return ttm

    # Attempt 2: Operating Income + D&A
    ebit = _row(fin, ["Operating Income", "EBIT", "Operating Profit"])
    if ebit is None:
        raise ValueError(
            "EBITDA and Operating Income data not found in quarterly financials. "
            "EV/EBITDA cannot be computed for this ticker. Try Price/Sales instead."
        )

    da = _row(fin, [
        "Reconciled Depreciation",
        "Depreciation And Amortization",
        "Depreciation Amortization Depletion",
        "Depreciation",
    ])

    if da is not None:
        combined = ebit.add(da.reindex(ebit.index, fill_value=0), fill_value=0)
        logger.debug("Built EBITDA from Operating Income + D&A for %s", ticker.ticker)
    else:
        logger.warning(
            "D&A not found for %s; using Operating Income as EBITDA proxy", ticker.ticker
        )
        combined = ebit

    ttm = combined.rolling(4, min_periods=4).sum().dropna()
    if ttm.empty:
        raise ValueError(
            "Fewer than 4 quarters of EBITDA history are available. "
            "Try a shorter time period or use a different metric."
        )

    ttm.index = _strip_tz(pd.to_datetime(ttm.index))
    return ttm


def _quarterly_revenue_ttm(ticker: yf.Ticker) -> pd.Series:
    """
    Build a trailing-twelve-month Revenue series from quarterly financials.

    Raises ValueError if revenue data is unavailable.
    """
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
            "Fewer than 4 quarters of revenue history are available. "
            "Try a shorter time period or use a different metric."
        )

    ttm.index = _strip_tz(pd.to_datetime(ttm.index))
    return ttm


# ── Per-metric series builders ───────────────────────────────────────────────

def _forward_eps_series(ticker: yf.Ticker, price: pd.Series) -> pd.Series:
    """
    Build Forward P/E series.

    Uses TTM EPS (from quarterly filings) as the historical denominator.
    If a current forwardEps estimate is available, it overrides the last point
    to reflect analyst consensus for the current period.

    Raises ValueError if EPS data is unavailable.
    """
    eps_ttm = _quarterly_eps_ttm(ticker)

    eps_daily = eps_ttm.reindex(price.index, method="ffill")

    # Override last point with analyst forwardEps if available and positive
    try:
        fwd_eps = ticker.info.get("forwardEps")
        if fwd_eps and float(fwd_eps) > 0:
            eps_daily.iloc[-1] = float(fwd_eps)
            logger.debug("Applied forwardEps override for %s: %.4f", ticker.ticker, fwd_eps)
    except Exception as exc:
        logger.debug("forwardEps override skipped for %s: %s", ticker.ticker, exc)

    pe = price / eps_daily
    pe = pe.replace([np.inf, -np.inf], np.nan).dropna()
    return pe


def _trailing_pe_series(ticker: yf.Ticker, price: pd.Series) -> pd.Series:
    """
    Build Trailing P/E series from TTM EPS (quarterly net income / shares).

    Raises ValueError if EPS data is unavailable.
    """
    eps_ttm = _quarterly_eps_ttm(ticker)

    eps_daily = eps_ttm.reindex(price.index, method="ffill")
    pe = price / eps_daily
    pe = pe.replace([np.inf, -np.inf], np.nan).dropna()
    return pe


def _ev_ebitda_series(ticker: yf.Ticker, price: pd.Series) -> pd.Series:
    """
    Build EV/EBITDA series.

    EV = (daily price × shares) + total debt − cash.
    EBITDA denominator is a quarterly TTM series, forward-filled to daily.

    Raises ValueError if EBITDA or balance sheet data is unavailable.
    """
    ebitda_ttm = _quarterly_ebitda_ttm(ticker)
    shares = _get_shares(ticker)

    try:
        info = ticker.info
    except Exception as exc:
        raise ValueError(f"Could not fetch company info for EV computation: {exc}") from exc

    total_debt = float(info.get("totalDebt") or 0)
    cash = float(info.get("totalCash") or 0)

    ebitda_daily = ebitda_ttm.reindex(price.index, method="ffill")

    ev = price * shares + total_debt - cash
    ev_ebitda = ev / ebitda_daily
    ev_ebitda = ev_ebitda.replace([np.inf, -np.inf], np.nan).dropna()
    return ev_ebitda


def _price_sales_series(ticker: yf.Ticker, price: pd.Series) -> pd.Series:
    """
    Build Price/Sales series.

    Market cap = daily price × shares outstanding.
    Revenue denominator is a quarterly TTM series, forward-filled to daily.

    Raises ValueError if revenue or shares data is unavailable.
    """
    rev_ttm = _quarterly_revenue_ttm(ticker)
    shares = _get_shares(ticker)

    rev_daily = rev_ttm.reindex(price.index, method="ffill")

    market_cap = price * shares
    ps = market_cap / rev_daily
    ps = ps.replace([np.inf, -np.inf], np.nan).dropna()
    return ps


# ── Series cleaning ──────────────────────────────────────────────────────────

def _clean_series(series: pd.Series, symbol: str, metric: MetricKey) -> pd.Series:
    """
    Remove infinities, NaNs, negative values, and statistical outliers.

    Raises ValueError if fewer than 20 valid data points remain — this usually
    means the metric is not applicable (e.g. negative earnings throughout).
    """
    series = series.replace([np.inf, -np.inf], np.nan).dropna()
    series = series[series > 0]

    # Remove values beyond 5× IQR of the 5th–95th percentile range
    q1, q3 = series.quantile(0.05), series.quantile(0.95)
    iqr = q3 - q1
    if iqr > 0:
        series = series[(series >= q1 - 5 * iqr) & (series <= q3 + 5 * iqr)]

    if len(series) < 20:
        metric_label = METRIC_LABELS.get(metric, metric)
        raise ValueError(
            f"Only {len(series)} valid data points remain for {symbol} {metric_label} "
            "after cleaning. This usually means the company had negative earnings "
            "throughout the selected period, or the time range is too short. "
            "Try a different metric (e.g. Price/Sales) or a shorter time period."
        )

    return series


# ── Result builder ───────────────────────────────────────────────────────────

def _build_result(symbol: str, metric: MetricKey, period: str, series: pd.Series) -> dict:
    """Compute statistics and assemble the final response dict."""
    label = METRIC_LABELS[metric]
    mean = float(series.mean())
    std = float(series.std())
    current = float(series.iloc[-1])
    zscore = (current - mean) / std if std > 0 else 0.0
    summary = _generate_summary(symbol, label, period, mean, std, current, zscore)

    if hasattr(series.index[0], "strftime"):
        dates = [d.strftime("%Y-%m-%d") for d in series.index]
    else:
        dates = [str(d) for d in series.index]

    return {
        "symbol": symbol,
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
    }


# ── Public API ───────────────────────────────────────────────────────────────

def fetch_valuation_series(
    symbol: str,
    metric: MetricKey,
    period: str = "5y",
) -> dict:
    """
    Fetch and return a historical valuation series for a ticker.

    Validates inputs, fetches live price + fundamental data from yfinance,
    computes the requested metric, cleans the series, and returns statistics.

    Raises:
        ValueError  – invalid input, unknown ticker, unavailable metric,
                      insufficient data, or negative-earnings company.
        RuntimeError – unexpected error in the data pipeline.

    Returns a dict with keys:
        symbol, metric, label, period, dates, values, stats, current, zscore, summary
    """
    symbol = validate_ticker(symbol)
    period = validate_period(period)
    days = PERIOD_DAYS[period]

    logger.info("Fetching %s / %s / %s (%d days)", symbol, metric, period, days)

    ticker = yf.Ticker(symbol)
    price = _get_price_history(ticker, days)

    try:
        if metric == "forward_pe":
            series = _forward_eps_series(ticker, price)
        elif metric == "trailing_pe":
            series = _trailing_pe_series(ticker, price)
        elif metric == "ev_ebitda":
            series = _ev_ebitda_series(ticker, price)
        elif metric == "price_sales":
            series = _price_sales_series(ticker, price)
        else:
            raise ValueError(f"Unknown metric: '{metric}'.")
    except ValueError:
        raise  # propagate with original message
    except Exception as exc:
        logger.exception("Unexpected error computing %s for %s", metric, symbol)
        raise RuntimeError(
            f"An unexpected error occurred while computing "
            f"{METRIC_LABELS.get(metric, metric)} for {symbol}: {exc}"
        ) from exc

    series = _clean_series(series, symbol, metric)

    logger.info(
        "OK %s/%s: %d points | current=%.2f | z=%+.2f",
        symbol, metric, len(series), series.iloc[-1],
        (series.iloc[-1] - series.mean()) / series.std(),
    )

    return _build_result(symbol, metric, period, series)


# ── Summary text ─────────────────────────────────────────────────────────────

def _generate_summary(
    symbol: str, label: str, period: str, mean: float, std: float,
    current: float, zscore: float,
) -> str:
    period_label = {
        "1y": "1-year", "2y": "2-year", "3y": "3-year",
        "5y": "5-year", "10y": "10-year",
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
        return (
            f"{symbol} is trading {assessment} "
            f"its {period_label} average {label} of {mean:.1f}x."
        )

    return (
        f"{symbol} is trading {abs_z:.1f} standard deviation{'s' if abs_z != 1 else ''} "
        f"{direction} its {period_label} average {label}, "
        f"which suggests it is {assessment.split(' relative to')[0].split(' compared to')[0]} "
        f"relative to its own history. "
        f"Current: {current:.1f}x | Mean: {mean:.1f}x | Z-score: {zscore:+.2f}."
    )
