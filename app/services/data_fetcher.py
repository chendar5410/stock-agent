"""Historical valuation data fetcher.

Primary source:  Financial Modeling Prep (FMP) v3 API.
                 Requires FMP_API_KEY environment variable.
                 Uses /historical-price-full, /income-statement, /profile,
                 and index-constituent endpoints (/sp500_constituent, etc.).

Fallback source: yfinance — used ONLY when FMP is unavailable or returns an
                 error.  Every fallback invocation is logged at WARNING level
                 so operators know the secondary path is active.  yfinance is
                 never called silently or as a default.

Supported metrics: trailing_pe, price_sales.
Supported periods: 1y, 2y, 3y, 5y, 10y.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Literal

import httpx
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# ── FMP config ────────────────────────────────────────────────────────────────

FMP_BASE = "https://financialmodelingprep.com/api/v3"
FMP_API_KEY: str = os.environ.get("FMP_API_KEY", "").strip()

if not FMP_API_KEY:
    logger.warning(
        "FMP_API_KEY is not set. Requests will fall back to yfinance. "
        "Set FMP_API_KEY to use Financial Modeling Prep as the primary data source."
    )

# ── Constants ─────────────────────────────────────────────────────────────────

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

_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")
REQUEST_TIMEOUT = 20  # seconds


# ── Input validation ──────────────────────────────────────────────────────────

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


# ── Shared helpers ────────────────────────────────────────────────────────────

def _strip_tz(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    idx = pd.to_datetime(idx)
    return idx.tz_convert(None) if idx.tz is not None else idx


def _clean_series(series: pd.Series, symbol: str, metric: str) -> pd.Series:
    """Drop non-positive values and outliers. Raises ValueError if < 20 points remain."""
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


# ═══════════════════════════════════════════════════════════════════════════════
#  FMP — PRIMARY DATA SOURCE
#
#  Endpoint map:
#    prices      GET /historical-price-full/{symbol}?from=&to=&serietype=line
#    income      GET /income-statement/{symbol}?period=quarter&limit=44
#    profile     GET /profile/{symbol}               (shares outstanding)
#    index lists GET /sp500_constituent | /nasdaq_constituent | /dowjones_constituent
# ═══════════════════════════════════════════════════════════════════════════════

class FMPError(Exception):
    """Raised when FMP cannot return usable data."""


def _fmp_get(path: str, params: dict | None = None) -> object:
    """
    Execute a GET against the FMP v3 API.

    Raises FMPError on:
      - FMP_API_KEY not configured
      - Network / connection errors
      - Non-200 HTTP status
      - FMP {"Error Message": ...} payloads
      - Empty list responses (unknown ticker)
    """
    if not FMP_API_KEY:
        raise FMPError("FMP_API_KEY is not configured.")

    p = dict(params or {})
    p["apikey"] = FMP_API_KEY

    try:
        resp = httpx.get(f"{FMP_BASE}{path}", params=p, timeout=REQUEST_TIMEOUT)
    except httpx.RequestError as exc:
        raise FMPError(f"Network error reaching FMP: {exc}") from exc

    if resp.status_code != 200:
        raise FMPError(f"FMP returned HTTP {resp.status_code} for {path}.")

    data = resp.json()

    if isinstance(data, dict) and "Error Message" in data:
        raise FMPError(f"FMP error: {data['Error Message']}")

    if isinstance(data, list) and len(data) == 0:
        raise FMPError(f"FMP returned an empty list for '{path}'.")

    return data


def _fmp_price_history(symbol: str, days: int) -> pd.Series:
    """
    Fetch daily closing prices from FMP /historical-price-full.

    Raises FMPError when data is unavailable or the response structure is wrong.
    """
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)

    data = _fmp_get(
        f"/historical-price-full/{symbol}",
        {
            "from": start_dt.strftime("%Y-%m-%d"),
            "to": end_dt.strftime("%Y-%m-%d"),
            "serietype": "line",
        },
    )

    if not isinstance(data, dict) or "historical" not in data:
        raise FMPError(
            f"FMP /historical-price-full/{symbol} returned unexpected structure."
        )

    records = data["historical"]
    if not records:
        raise FMPError(f"FMP returned empty price history for '{symbol}'.")

    df = pd.DataFrame(records)
    if "date" not in df.columns or "close" not in df.columns:
        raise FMPError(
            f"FMP price records for '{symbol}' are missing 'date' or 'close' columns."
        )

    df["date"] = pd.to_datetime(df["date"])
    series = df.sort_values("date").set_index("date")["close"].dropna()

    if len(series) < 5:
        raise FMPError(
            f"FMP returned only {len(series)} price point(s) for '{symbol}'. "
            "Insufficient history."
        )

    return series


def _fmp_quarterly_income(symbol: str) -> pd.DataFrame:
    """
    Fetch quarterly income statements from FMP /income-statement.

    Returns a DataFrame indexed by period-end date with columns:
      netIncome, revenue
    Raises FMPError when data is unavailable or required columns are absent.
    """
    records = _fmp_get(
        f"/income-statement/{symbol}",
        {"period": "quarter", "limit": 44},
    )

    if not isinstance(records, list):
        raise FMPError(
            f"FMP /income-statement/{symbol} returned unexpected type "
            f"({type(records).__name__})."
        )

    df = pd.DataFrame(records)
    missing = [c for c in ("date", "netIncome", "revenue") if c not in df.columns]
    if missing:
        raise FMPError(
            f"FMP income-statement for '{symbol}' is missing columns: {missing}."
        )

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    return df[["netIncome", "revenue"]]


def _fmp_shares(symbol: str) -> float:
    """
    Fetch shares outstanding from FMP /profile.

    Raises FMPError when unavailable or non-positive.
    """
    records = _fmp_get(f"/profile/{symbol}")

    if not isinstance(records, list) or not records:
        raise FMPError(f"FMP /profile/{symbol} returned no data.")

    profile = records[0]
    shares = profile.get("sharesOutstanding")

    # Fall back to mktCap / price if sharesOutstanding is absent
    if not shares or shares <= 0:
        mkt = profile.get("mktCap", 0)
        price = profile.get("price", 0)
        if mkt > 0 and price > 0:
            shares = mkt / price

    if not shares or shares <= 0:
        raise FMPError(
            f"FMP profile for '{symbol}' has no valid sharesOutstanding "
            f"(got {profile.get('sharesOutstanding')!r})."
        )

    return float(shares)


# ── FMP index constituent lists ───────────────────────────────────────────────

_INDEX_PATHS: dict[str, str] = {
    "sp500":    "/sp500_constituent",
    "nasdaq":   "/nasdaq_constituent",
    "dowjones": "/dowjones_constituent",
}


def fmp_index_constituents(index: str = "sp500") -> list[str]:
    """
    Return ticker symbols that are members of *index*.

    index: "sp500" | "nasdaq" | "dowjones"

    Returns an empty list (not an error) if FMP_API_KEY is absent or the
    request fails — caller decides how to handle an empty result.
    """
    if index not in _INDEX_PATHS:
        raise ValueError(
            f"Unknown index '{index}'. Choose from: {list(_INDEX_PATHS)}."
        )

    if not FMP_API_KEY:
        return []

    try:
        records = _fmp_get(_INDEX_PATHS[index])
        return [r["symbol"] for r in records if isinstance(r, dict) and "symbol" in r]
    except FMPError as exc:
        logger.warning("Could not fetch '%s' constituents from FMP: %s", index, exc)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#  yfinance — EXPLICIT SECONDARY FALLBACK
#
#  Rules:
#    - Never call these functions directly from fetch_valuation_series().
#    - Only call them from the _get_*() bridge functions below, after FMP has
#      raised FMPError.
#    - Every function logs a WARNING so operators know the fallback is active.
# ═══════════════════════════════════════════════════════════════════════════════

_YF_NOT_FOUND = (
    "no data found",
    "possibly delisted",
    "no timezone found",
    "symbol may be",
    "no price data found",
    "data not available",
)


class _YFLogCapture(logging.Handler):
    """Capture yfinance log lines to detect 'ticker not found' hints."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage().lower())

    def found_not_found_hint(self) -> bool:
        return any(kw in msg for msg in self.messages for kw in _YF_NOT_FOUND)


def _yf_history_safe(ticker: yf.Ticker, days: int) -> pd.DataFrame:
    capture = _YFLogCapture()
    yf_loggers = [
        logging.getLogger(n)
        for n in ("yfinance", "yfinance.base", "yfinance.utils", "yfinance.ticker")
    ]
    for lg in yf_loggers:
        lg.addHandler(capture)

    end_dt, start_dt = datetime.now(), datetime.now() - timedelta(days=days)
    hist: pd.DataFrame = pd.DataFrame()
    exc_caught: Exception | None = None

    try:
        hist = ticker.history(
            start=start_dt, end=end_dt, auto_adjust=True, timeout=REQUEST_TIMEOUT
        )
    except Exception as exc:
        exc_caught = exc
    finally:
        for lg in yf_loggers:
            lg.removeHandler(capture)

    if exc_caught is not None:
        raise ValueError(
            f"yfinance fetch failed for '{ticker.ticker}': {exc_caught}."
        ) from exc_caught

    if capture.found_not_found_hint():
        raise ValueError(
            f"yfinance: ticker '{ticker.ticker}' not found "
            "(possibly invalid, delisted, or unavailable)."
        )

    return hist


def _yf_price_history(symbol: str, days: int) -> pd.Series:
    """yfinance price fallback. Only call after FMP has raised FMPError."""
    logger.warning(
        "[FALLBACK yfinance] Fetching price history for '%s' (%d days).", symbol, days
    )
    ticker = yf.Ticker(symbol)
    hist = _yf_history_safe(ticker, days)

    if hist.empty:
        raise ValueError(
            f"yfinance returned no price history for '{symbol}'. "
            "The symbol may be invalid or delisted."
        )
    if len(hist) < 5:
        raise ValueError(
            f"yfinance returned only {len(hist)} trading day(s) for '{symbol}'. "
            "Insufficient history."
        )

    s = hist["Close"]
    s.index = _strip_tz(s.index)
    return s


def _yf_quarterly_income(symbol: str) -> pd.DataFrame:
    """
    yfinance quarterly income fallback. Only call after FMP has raised FMPError.

    Returns a DataFrame indexed by period-end date with columns:
      netIncome, revenue  (one or both may be NaN if yfinance lacks the data).
    """
    logger.warning(
        "[FALLBACK yfinance] Fetching quarterly income for '%s'.", symbol
    )
    ticker = yf.Ticker(symbol)

    try:
        fin = ticker.quarterly_financials
    except Exception as exc:
        raise ValueError(
            f"yfinance quarterly_financials failed for '{symbol}': {exc}."
        ) from exc

    if fin is None or fin.empty:
        raise ValueError(
            f"yfinance has no quarterly financials for '{symbol}'. "
            "Likely an ETF, SPAC, or foreign stock without US filings."
        )

    fin.columns = _strip_tz(pd.to_datetime(fin.columns))
    # quarterly_financials is metrics × periods; transpose to periods × metrics
    fin_t = fin.T.sort_index()

    ni_candidates  = ["Net Income", "Net Income Common Stockholders",
                      "Net Income From Continuing Operations"]
    rev_candidates = ["Total Revenue", "Revenue", "Net Revenue"]

    ni_col  = next((c for c in ni_candidates  if c in fin_t.columns), None)
    rev_col = next((c for c in rev_candidates if c in fin_t.columns), None)

    if ni_col is None and rev_col is None:
        raise ValueError(
            f"yfinance quarterly financials for '{symbol}' contain neither "
            "Net Income nor Revenue."
        )

    result = pd.DataFrame(index=fin_t.index)
    result["netIncome"] = fin_t[ni_col]  if ni_col  else np.nan
    result["revenue"]   = fin_t[rev_col] if rev_col else np.nan
    return result.dropna(how="all")


def _yf_shares(symbol: str) -> float:
    """yfinance shares fallback. Only call after FMP has raised FMPError."""
    logger.warning(
        "[FALLBACK yfinance] Fetching shares outstanding for '%s'.", symbol
    )
    ticker = yf.Ticker(symbol)

    try:
        fi = ticker.fast_info
        shares = getattr(fi, "shares", None) or getattr(fi, "sharesOutstanding", None)
        if shares and shares > 0:
            return float(shares)
    except Exception as exc:
        logger.debug("yfinance fast_info shares unavailable for %s: %s", symbol, exc)

    try:
        info = ticker.info
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        if shares and shares > 0:
            return float(shares)
    except Exception as exc:
        logger.debug("yfinance info shares unavailable for %s: %s", symbol, exc)

    raise ValueError(
        f"yfinance has no shares outstanding data for '{symbol}'."
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Bridge functions — FMP first, explicit yfinance fallback
#
#  Each function returns (data, source_string).
#  source_string is "fmp" when FMP succeeded, "yfinance" when the fallback ran.
# ═══════════════════════════════════════════════════════════════════════════════

def _get_price(symbol: str, days: int) -> tuple[pd.Series, str]:
    try:
        return _fmp_price_history(symbol, days), "fmp"
    except FMPError as exc:
        logger.warning(
            "FMP price fetch failed for '%s' — using yfinance fallback. Reason: %s",
            symbol, exc,
        )
    return _yf_price_history(symbol, days), "yfinance"


def _get_quarterly_income(symbol: str) -> tuple[pd.DataFrame, str]:
    try:
        return _fmp_quarterly_income(symbol), "fmp"
    except FMPError as exc:
        logger.warning(
            "FMP income fetch failed for '%s' — using yfinance fallback. Reason: %s",
            symbol, exc,
        )
    return _yf_quarterly_income(symbol), "yfinance"


def _get_shares(symbol: str) -> tuple[float, str]:
    try:
        return _fmp_shares(symbol), "fmp"
    except FMPError as exc:
        logger.warning(
            "FMP shares fetch failed for '%s' — using yfinance fallback. Reason: %s",
            symbol, exc,
        )
    return _yf_shares(symbol), "yfinance"


# ═══════════════════════════════════════════════════════════════════════════════
#  Metric series builders
# ═══════════════════════════════════════════════════════════════════════════════

def _trailing_pe_series(
    symbol: str, price: pd.Series
) -> tuple[pd.Series, list[str]]:
    """Build daily Trailing P/E. Returns (series, [income_source, shares_source])."""
    income_df, inc_src = _get_quarterly_income(symbol)

    ni = income_df.get("netIncome", pd.Series(dtype=float)).dropna().sort_index()
    if ni.empty:
        raise ValueError(
            f"Net income data is unavailable for '{symbol}'. "
            "Cannot compute Trailing P/E. Try Price/Sales instead."
        )

    shares, sh_src = _get_shares(symbol)

    ni_ttm = ni.rolling(4, min_periods=4).sum().dropna()
    if ni_ttm.empty:
        raise ValueError(
            f"Fewer than 4 quarters of earnings available for '{symbol}'. "
            "Try a shorter period or use Price/Sales."
        )

    eps_ttm = ni_ttm / shares
    eps_ttm.index = _strip_tz(pd.to_datetime(eps_ttm.index))
    eps_daily = eps_ttm.reindex(price.index, method="ffill")
    pe = price / eps_daily
    return pe.replace([np.inf, -np.inf], np.nan).dropna(), [inc_src, sh_src]


def _price_sales_series(
    symbol: str, price: pd.Series
) -> tuple[pd.Series, list[str]]:
    """Build daily Price/Sales. Returns (series, [income_source, shares_source])."""
    income_df, inc_src = _get_quarterly_income(symbol)

    rev = income_df.get("revenue", pd.Series(dtype=float)).dropna().sort_index()
    if rev.empty:
        raise ValueError(
            f"Revenue data is unavailable for '{symbol}'. "
            "Cannot compute Price/Sales."
        )

    shares, sh_src = _get_shares(symbol)

    rev_ttm = rev.rolling(4, min_periods=4).sum().dropna()
    if rev_ttm.empty:
        raise ValueError(
            f"Fewer than 4 quarters of revenue available for '{symbol}'. "
            "Try a shorter period."
        )

    rev_ttm.index = _strip_tz(pd.to_datetime(rev_ttm.index))
    rev_daily = rev_ttm.reindex(price.index, method="ffill")
    market_cap = price * shares
    ps = market_cap / rev_daily
    return ps.replace([np.inf, -np.inf], np.nan).dropna(), [inc_src, sh_src]


# ── Result builder ────────────────────────────────────────────────────────────

def _build_result(
    symbol: str, metric: str, period: str, series: pd.Series, source: str
) -> dict:
    label = METRIC_LABELS[metric]
    mean = float(series.mean())
    std  = float(series.std())
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
            "mean":   round(mean, 4),
            "std":    round(std, 4),
            "plus1":  round(mean + std, 4),
            "minus1": round(mean - std, 4),
            "plus2":  round(mean + 2 * std, 4),
            "minus2": round(mean - 2 * std, 4),
        },
        "current": round(current, 4),
        "zscore":  round(zscore, 3),
        "summary": summary,
        "source":  source,   # "fmp" | "yfinance" | "mixed"
    }


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_valuation_series(
    symbol: str,
    metric: str,
    period: str = "5y",
) -> dict:
    """
    Fetch and return a historical valuation series for a ticker.

    Data source priority
    --------------------
    1. FMP (primary)      — requires FMP_API_KEY env var.
    2. yfinance (fallback) — used only when FMP fails; always logged at WARNING.

    The returned dict includes a ``source`` key:
      "fmp"      — all data came from FMP.
      "yfinance" — all data came from yfinance.
      "mixed"    — price and fundamentals came from different sources.

    Raises ValueError  for invalid input, unknown tickers, or unavailable data.
    Raises RuntimeError for unexpected pipeline failures.
    """
    symbol = validate_ticker(symbol)
    period = validate_period(period)
    metric = validate_metric(metric)
    days   = PERIOD_DAYS[period]

    logger.info("Fetching %s / %s / %s (%d days)", symbol, metric, period, days)

    price, price_src = _get_price(symbol, days)

    try:
        if metric == "trailing_pe":
            series, fund_sources = _trailing_pe_series(symbol, price)
        elif metric == "price_sales":
            series, fund_sources = _price_sales_series(symbol, price)
        else:
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

    all_sources = [price_src, *fund_sources]
    if all(s == "fmp"      for s in all_sources):
        source = "fmp"
    elif all(s == "yfinance" for s in all_sources):
        source = "yfinance"
    else:
        source = "mixed"

    logger.info(
        "OK %s/%s/%s: %d pts | current=%.2f | z=%+.2f | source=%s",
        symbol, metric, period, len(series),
        series.iloc[-1],
        (series.iloc[-1] - series.mean()) / series.std(),
        source,
    )

    return _build_result(symbol, metric, period, series, source)


# ── Summary text ──────────────────────────────────────────────────────────────

def _generate_summary(
    symbol: str, label: str, period: str,
    mean: float, std: float, current: float, zscore: float,
) -> str:
    period_label = {
        "1y": "1-year", "2y": "2-year", "3y": "3-year",
        "5y": "5-year", "10y": "10-year",
    }.get(period, period)

    abs_z     = abs(zscore)
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
