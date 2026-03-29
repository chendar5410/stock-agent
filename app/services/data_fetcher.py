"""Historical valuation data fetcher.

Primary source:  Financial Modeling Prep (FMP) v3 API.
                 Requires FMP_API_KEY environment variable.
Fallback source: yfinance — used ONLY when FMP is unavailable or fails.
                 Every fallback is logged at WARNING level.

Period enforcement
------------------
Coverage is validated at TWO points:
  1. _get_price()           — price series must span >= 70% of requested days.
  2. fetch_valuation_series — FINAL metric series must span >= 70% of requested
                              days.  This catches the common case where the
                              price history is sufficient but the quarterly
                              fundamentals (only 5-6 quarters from yfinance)
                              cause the P/E or P/S series to be truncated after
                              the TTM rolling window and dropna().

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
        "FMP_API_KEY is not set. All requests will fall back to yfinance. "
        "Set FMP_API_KEY to use Financial Modeling Prep as the primary source."
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

# Minimum fraction of requested calendar days that a series must cover.
# Checked on both the raw price series AND the final metric series.
_PERIOD_COVERAGE_THRESHOLD = 0.70


# ── Input validation ──────────────────────────────────────────────────────────

def validate_ticker(symbol: str) -> str:
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
    if period not in VALID_PERIODS:
        raise ValueError(
            f"'{period}' is not a valid period. "
            f"Choose from: {', '.join(sorted(VALID_PERIODS))}."
        )
    return period


def validate_metric(metric: str) -> str:
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


def _series_span_days(s: pd.Series) -> int:
    """Return calendar days between the first and last index entry."""
    if len(s) < 2:
        return 0
    return (pd.Timestamp(s.index[-1]) - pd.Timestamp(s.index[0])).days


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
# ═══════════════════════════════════════════════════════════════════════════════

class FMPError(Exception):
    """Raised when FMP cannot return usable data."""


class InsufficientCoverageError(ValueError):
    """Raised when the final metric series is too short for the requested period.

    Carries a ``debug`` dict so callers (e.g. the router) can surface structured
    diagnostic information to the client without parsing the error message string.
    """

    def __init__(self, message: str, debug: dict) -> None:
        super().__init__(message)
        self.debug = debug


def _fmp_get(path: str, params: dict | None = None) -> object:
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
    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=days)

    data = _fmp_get(
        f"/historical-price-full/{symbol}",
        {
            "from":       start_dt.strftime("%Y-%m-%d"),
            "to":         end_dt.strftime("%Y-%m-%d"),
            "serietype":  "line",
        },
    )

    if not isinstance(data, dict) or "historical" not in data:
        raise FMPError(f"FMP /historical-price-full/{symbol} returned unexpected structure.")

    records = data["historical"]
    if not records:
        raise FMPError(f"FMP returned empty price history for '{symbol}'.")

    df = pd.DataFrame(records)
    if "date" not in df.columns or "close" not in df.columns:
        raise FMPError(f"FMP price records for '{symbol}' missing 'date' or 'close'.")

    df["date"] = pd.to_datetime(df["date"])
    series = df.sort_values("date").set_index("date")["close"].dropna()

    if len(series) < 5:
        raise FMPError(f"FMP returned only {len(series)} price point(s) for '{symbol}'.")

    return series


def _fmp_quarterly_income(symbol: str) -> pd.DataFrame:
    records = _fmp_get(
        f"/income-statement/{symbol}",
        {"period": "quarter", "limit": 44},
    )

    if not isinstance(records, list):
        raise FMPError(f"FMP /income-statement/{symbol} returned unexpected type.")

    df = pd.DataFrame(records)
    missing = [c for c in ("date", "netIncome", "revenue") if c not in df.columns]
    if missing:
        raise FMPError(f"FMP income-statement for '{symbol}' missing: {missing}.")

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    result = df[["netIncome", "revenue"]]

    # ── TRACE ──────────────────────────────────────────────────────────────────
    print(f"\n[TRACE] _fmp_quarterly_income({symbol})")
    print(f"  rows={len(result)}")
    print(f"  min_date={result.index.min()}")
    print(f"  max_date={result.index.max()}")
    print(f"  first5={list(result.index[:5])}")
    print(f"  last5={list(result.index[-5:])}")
    # ───────────────────────────────────────────────────────────────────────────

    return result


def _fmp_shares(symbol: str) -> float:
    records = _fmp_get(f"/profile/{symbol}")

    if not isinstance(records, list) or not records:
        raise FMPError(f"FMP /profile/{symbol} returned no data.")

    profile = records[0]
    shares  = profile.get("sharesOutstanding")

    if not shares or shares <= 0:
        mkt   = profile.get("mktCap", 0)
        price = profile.get("price", 0)
        if mkt > 0 and price > 0:
            shares = mkt / price

    if not shares or shares <= 0:
        raise FMPError(f"FMP profile for '{symbol}' has no valid sharesOutstanding.")

    return float(shares)


_INDEX_PATHS: dict[str, str] = {
    "sp500":    "/sp500_constituent",
    "nasdaq":   "/nasdaq_constituent",
    "dowjones": "/dowjones_constituent",
}


def fmp_index_constituents(index: str = "sp500") -> list[str]:
    if index not in _INDEX_PATHS:
        raise ValueError(f"Unknown index '{index}'. Choose from: {list(_INDEX_PATHS)}.")
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
#  Never call these directly from fetch_valuation_series().
#  Only call from the bridge functions after FMP has failed.
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
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage().lower())

    def found_not_found_hint(self) -> bool:
        return any(kw in msg for msg in self.messages for kw in _YF_NOT_FOUND)


def _yf_history_safe(ticker: yf.Ticker, start_str: str, end_str: str) -> pd.DataFrame:
    """Fetch yfinance price history using ISO date strings."""
    capture = _YFLogCapture()
    yf_loggers = [
        logging.getLogger(n)
        for n in ("yfinance", "yfinance.base", "yfinance.utils", "yfinance.ticker")
    ]
    for lg in yf_loggers:
        lg.addHandler(capture)

    hist: pd.DataFrame = pd.DataFrame()
    exc_caught: Exception | None = None

    try:
        hist = ticker.history(
            start=start_str,
            end=end_str,
            auto_adjust=True,
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as exc:
        exc_caught = exc
    finally:
        for lg in yf_loggers:
            lg.removeHandler(capture)

    if exc_caught is not None:
        raise ValueError(f"yfinance fetch failed for '{ticker.ticker}': {exc_caught}.") from exc_caught

    if capture.found_not_found_hint():
        raise ValueError(
            f"yfinance: ticker '{ticker.ticker}' not found (possibly invalid or delisted)."
        )

    return hist


def _yf_price_history(symbol: str, days: int) -> pd.Series:
    """yfinance price fallback. Only call after FMP has raised FMPError."""
    logger.warning("[FALLBACK yfinance] price history for '%s' (%d days).", symbol, days)

    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    ticker   = yf.Ticker(symbol)
    hist     = _yf_history_safe(
        ticker,
        start_str=start_dt.strftime("%Y-%m-%d"),
        end_str=end_dt.strftime("%Y-%m-%d"),
    )

    if hist.empty:
        raise ValueError(
            f"yfinance returned no price history for '{symbol}'. "
            "The symbol may be invalid or delisted."
        )
    if len(hist) < 5:
        raise ValueError(
            f"yfinance returned only {len(hist)} trading day(s) for '{symbol}'."
        )

    s = hist["Close"]
    s.index = _strip_tz(s.index)
    return s


_NI_COLS  = [
    "Net Income",
    "Net Income Common Stockholders",
    "Net Income From Continuing Operations",
]
_REV_COLS = ["Total Revenue", "Revenue", "Net Revenue"]


def _yf_extract_ni_rev(fin_t: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Extract net-income and revenue from a transposed yfinance income statement."""
    ni_col  = next((c for c in _NI_COLS  if c in fin_t.columns), None)
    rev_col = next((c for c in _REV_COLS if c in fin_t.columns), None)
    # ── TRACE: show every available column so we can spot name mismatches ──────
    print(f"  [extract] all_cols={list(fin_t.columns)}")
    print(f"  [extract] ni_col_matched={ni_col}  rev_col_matched={rev_col}")
    # ───────────────────────────────────────────────────────────────────────────
    ni  = fin_t[ni_col].dropna()  if ni_col  else pd.Series(dtype=float)
    rev = fin_t[rev_col].dropna() if rev_col else pd.Series(dtype=float)
    return ni, rev


def _yf_ttm_income(symbol: str) -> tuple[dict, str]:
    """
    yfinance fallback: build TTM net-income + revenue series.

    Two-tier approach:
      1. quarterly_income_stmt / quarterly_financials → rolling-4 TTM
         (high temporal resolution, but yfinance typically returns only 5-6
         quarters, so the TTM series covers only 1-2 recent quarters).
      2. income_stmt / financials (annual) → each fiscal-year total IS a TTM
         value at the fiscal-year-end date; yfinance usually provides 4-5 years.

    The two are merged so that annual data covers the historical span and
    quarterly TTM provides precision for the most recent periods.  The result
    typically covers 4-5 years — enough for 1y through 5y requests.
    """
    logger.warning("[FALLBACK yfinance] TTM income for '%s'.", symbol)
    ticker = yf.Ticker(symbol)

    # ── 1. Quarterly rolling TTM ─────────────────────────────────────────────
    q_ni_ttm  = pd.Series(dtype=float)
    q_rev_ttm = pd.Series(dtype=float)
    for attr in ("quarterly_income_stmt", "quarterly_financials"):
        try:
            fin = getattr(ticker, attr, None)
            if fin is None or (hasattr(fin, "empty") and fin.empty):
                print(f"[TRACE] _yf_ttm_income({symbol}) quarterly attr={attr}: empty/None")
                continue
            fin.columns = _strip_tz(pd.to_datetime(fin.columns))
            fin_t = fin.T.sort_index()
            ni, rev = _yf_extract_ni_rev(fin_t)
            # ── TRACE ─────────────────────────────────────────────────────────
            print(f"[TRACE] _yf_ttm_income({symbol}) quarterly attr={attr}")
            print(f"  raw ni rows={len(ni)}  raw rev rows={len(rev)}")
            if not ni.empty:
                print(f"  ni dates: {list(ni.index)}")
            # ──────────────────────────────────────────────────────────────────
            if len(ni) > 0:
                ttm = ni.rolling(4, min_periods=4).sum().dropna()
                print(f"  ni_ttm after rolling(4): rows={len(ttm)}  dates={list(ttm.index)}")
                if len(ttm) > len(q_ni_ttm):
                    q_ni_ttm = ttm
                    logger.debug(
                        "yfinance %s: %d quarterly TTM ni points for %s",
                        attr, len(ttm), symbol,
                    )
            if len(rev) > 0:
                ttm = rev.rolling(4, min_periods=4).sum().dropna()
                if len(ttm) > len(q_rev_ttm):
                    q_rev_ttm = ttm
        except Exception as exc:
            print(f"[TRACE] _yf_ttm_income({symbol}) quarterly attr={attr} EXCEPTION: {exc}")
            logger.debug("yfinance %s unavailable for %s: %s", attr, symbol, exc)

    # ── 2. Annual TTM (fiscal-year total = TTM at fiscal-year-end) ───────────
    a_ni  = pd.Series(dtype=float)
    a_rev = pd.Series(dtype=float)
    for attr in ("income_stmt", "financials"):
        try:
            fin = getattr(ticker, attr, None)
            if fin is None or (hasattr(fin, "empty") and fin.empty):
                print(f"[TRACE] _yf_ttm_income({symbol}) annual attr={attr}: empty/None")
                continue
            fin.columns = _strip_tz(pd.to_datetime(fin.columns))
            fin_t = fin.T.sort_index()
            ni, rev = _yf_extract_ni_rev(fin_t)
            # ── TRACE ─────────────────────────────────────────────────────────
            print(f"[TRACE] _yf_ttm_income({symbol}) annual attr={attr}")
            print(f"  raw ni rows={len(ni)}  raw rev rows={len(rev)}")
            if not ni.empty:
                print(f"  ni dates: {list(ni.index)}")
            # ──────────────────────────────────────────────────────────────────
            if len(ni) > len(a_ni):
                a_ni = ni
                logger.debug(
                    "yfinance %s: %d annual ni points for %s", attr, len(ni), symbol
                )
            if len(rev) > len(a_rev):
                a_rev = rev
        except Exception as exc:
            print(f"[TRACE] _yf_ttm_income({symbol}) annual attr={attr} EXCEPTION: {exc}")
            logger.debug("yfinance %s unavailable for %s: %s", attr, symbol, exc)

    # ── 3. Merge: annual provides historical base, quarterly TTM is recent ───
    def _merge(q_ttm: pd.Series, a_vals: pd.Series) -> pd.Series:
        """Keep annual points that pre-date the first quarterly TTM by >45 days,
        then append the quarterly TTM series.  This extends the timeline backwards
        without double-counting the same fiscal period."""
        if q_ttm.empty and a_vals.empty:
            return pd.Series(dtype=float)
        if q_ttm.empty:
            return a_vals.sort_index()
        if a_vals.empty:
            return q_ttm.sort_index()
        cutoff = q_ttm.index[0] - pd.Timedelta(days=45)
        prior  = a_vals[a_vals.index <= cutoff]
        merged = pd.concat([prior, q_ttm]).sort_index()
        return merged[~merged.index.duplicated(keep="last")]

    ni_ttm  = _merge(q_ni_ttm,  a_ni)
    rev_ttm = _merge(q_rev_ttm, a_rev)

    # ── TRACE ──────────────────────────────────────────────────────────────────
    print(f"\n[TRACE] _yf_ttm_income({symbol}) MERGED RESULT")
    print(f"  ni_ttm rows={len(ni_ttm)}")
    if not ni_ttm.empty:
        print(f"  ni_ttm min={ni_ttm.index.min()}  max={ni_ttm.index.max()}")
        print(f"  ni_ttm first5={list(ni_ttm.index[:5])}")
        print(f"  ni_ttm last5={list(ni_ttm.index[-5:])}")
    print(f"  rev_ttm rows={len(rev_ttm)}")
    if not rev_ttm.empty:
        print(f"  rev_ttm min={rev_ttm.index.min()}  max={rev_ttm.index.max()}")
        print(f"  rev_ttm first5={list(rev_ttm.index[:5])}")
        print(f"  rev_ttm last5={list(rev_ttm.index[-5:])}")
    # ───────────────────────────────────────────────────────────────────────────

    if ni_ttm.empty and rev_ttm.empty:
        raise ValueError(
            f"yfinance has no income data for '{symbol}'. "
            "Likely an ETF, SPAC, or foreign stock without US filings."
        )

    logger.warning(
        "[FALLBACK yfinance] TTM income for '%s': ni=%d pts (%s→%s), rev=%d pts",
        symbol,
        len(ni_ttm),
        ni_ttm.index[0].date() if not ni_ttm.empty else "—",
        ni_ttm.index[-1].date() if not ni_ttm.empty else "—",
        len(rev_ttm),
    )
    return {"ni_ttm": ni_ttm, "rev_ttm": rev_ttm}, "yfinance"


def _yf_shares(symbol: str) -> float:
    """yfinance shares fallback. Only call after FMP has raised FMPError."""
    logger.warning("[FALLBACK yfinance] shares outstanding for '%s'.", symbol)
    ticker = yf.Ticker(symbol)

    try:
        fi = ticker.fast_info
        shares = getattr(fi, "shares", None) or getattr(fi, "sharesOutstanding", None)
        if shares and shares > 0:
            return float(shares)
    except Exception as exc:
        logger.debug("yfinance fast_info shares unavailable for %s: %s", symbol, exc)

    try:
        info   = ticker.info
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        if shares and shares > 0:
            return float(shares)
    except Exception as exc:
        logger.debug("yfinance info shares unavailable for %s: %s", symbol, exc)

    raise ValueError(f"yfinance has no shares outstanding data for '{symbol}'.")


# ═══════════════════════════════════════════════════════════════════════════════
#  Bridge functions — FMP first, explicit yfinance fallback.
#  Coverage is enforced here for the price series.
# ═══════════════════════════════════════════════════════════════════════════════

def _get_price(symbol: str, days: int) -> tuple[pd.Series, str]:
    """
    Fetch price history with coverage enforcement on the price series.

    If FMP fails OR its series spans < _PERIOD_COVERAGE_THRESHOLD of requested
    days, discard it and fall back to yfinance.
    If yfinance also returns too-short a series, raise ValueError.

    NOTE: this check covers the PRICE series only.  The final metric series
    is separately validated in fetch_valuation_series() after TTM computation.
    """
    min_days = int(days * _PERIOD_COVERAGE_THRESHOLD)

    # ── 1. FMP ────────────────────────────────────────────────────────────────
    try:
        fmp_series = _fmp_price_history(symbol, days)
        actual = _series_span_days(fmp_series)
        if actual >= min_days:
            logger.info("FMP price OK for '%s': %d days (need %d).", symbol, actual, min_days)
            return fmp_series, "fmp"
        logger.warning(
            "FMP price for '%s' spans only %d days (need \u2265%d). "
            "Discarding — falling back to yfinance.",
            symbol, actual, min_days,
        )
    except FMPError as exc:
        logger.warning(
            "FMP price fetch failed for '%s' — falling back to yfinance. Reason: %s",
            symbol, exc,
        )

    # ── 2. yfinance ───────────────────────────────────────────────────────────
    yf_series = _yf_price_history(symbol, days)
    actual = _series_span_days(yf_series)
    if actual < min_days:
        raise ValueError(
            f"Price data for '{symbol}' covers only {actual} calendar days "
            f"({pd.Timestamp(yf_series.index[0]).date()} \u2192 "
            f"{pd.Timestamp(yf_series.index[-1]).date()}), "
            f"but the requested period needs \u2265{min_days} days "
            f"({_PERIOD_COVERAGE_THRESHOLD:.0%} of {days}). "
            "Both FMP and yfinance returned insufficient price history. "
            "Try a shorter period."
        )
    return yf_series, "yfinance"


def _fmp_ttm_income(symbol: str) -> tuple[dict, str]:
    """FMP quarterly data → rolling-4 TTM net income + revenue."""
    df = _fmp_quarterly_income(symbol)

    ni  = df.get("netIncome", pd.Series(dtype=float)).dropna().sort_index()
    rev = df.get("revenue",   pd.Series(dtype=float)).dropna().sort_index()

    ni_ttm  = ni.rolling(4,  min_periods=4).sum().dropna()
    rev_ttm = rev.rolling(4, min_periods=4).sum().dropna()

    # ── TRACE ──────────────────────────────────────────────────────────────────
    print(f"\n[TRACE] _fmp_ttm_income({symbol})")
    print(f"  raw ni rows={len(ni)}  raw rev rows={len(rev)}")
    if len(ni) < 23:
        print(f"  WARNING: raw ni rows={len(ni)} < 23 — fewer than 20 TTM points will result; 5y needs >=23 quarters")
    print(f"  ni_ttm rows={len(ni_ttm)}")
    if not ni_ttm.empty:
        print(f"  ni_ttm min={ni_ttm.index.min()}  max={ni_ttm.index.max()}")
        print(f"  ni_ttm first5={list(ni_ttm.index[:5])}")
        print(f"  ni_ttm last5={list(ni_ttm.index[-5:])}")
    print(f"  rev_ttm rows={len(rev_ttm)}")
    if not rev_ttm.empty:
        print(f"  rev_ttm min={rev_ttm.index.min()}  max={rev_ttm.index.max()}")
    # ───────────────────────────────────────────────────────────────────────────

    if ni_ttm.empty and rev_ttm.empty:
        raise FMPError(
            f"FMP returned insufficient quarterly data for TTM computation "
            f"for '{symbol}' (need \u22654 quarters)."
        )
    logger.info(
        "FMP TTM income for '%s': ni=%d pts (%s→%s), rev=%d pts",
        symbol,
        len(ni_ttm),
        ni_ttm.index[0].date() if not ni_ttm.empty else "—",
        ni_ttm.index[-1].date() if not ni_ttm.empty else "—",
        len(rev_ttm),
    )
    return {"ni_ttm": ni_ttm, "rev_ttm": rev_ttm}, "fmp"


def _get_ttm_income(symbol: str) -> tuple[dict, str]:
    """Fetch TTM net income + revenue series.  FMP first, yfinance fallback."""
    # ── TRACE ──────────────────────────────────────────────────────────────────
    print(f"\n[TRACE] _get_ttm_income({symbol}) — trying FMP first")
    # ───────────────────────────────────────────────────────────────────────────
    try:
        result = _fmp_ttm_income(symbol)
        print(f"[TRACE] _get_ttm_income({symbol}) — FMP succeeded")
        return result
    except FMPError as exc:
        print(f"[TRACE] _get_ttm_income({symbol}) — FMP failed: {exc}")
        logger.warning(
            "FMP TTM income failed for '%s' — falling back to yfinance. Reason: %s",
            symbol, exc,
        )
    print(f"[TRACE] _get_ttm_income({symbol}) — falling back to yfinance")
    return _yf_ttm_income(symbol)


def _get_shares(symbol: str) -> tuple[float, str]:
    try:
        return _fmp_shares(symbol), "fmp"
    except FMPError as exc:
        logger.warning(
            "FMP shares fetch failed for '%s' — falling back to yfinance. Reason: %s",
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
    # ── TRACE ──────────────────────────────────────────────────────────────────
    print(f"\n[TRACE] _trailing_pe_series({symbol})")
    print(f"  price rows={len(price)}  min={price.index.min()}  max={price.index.max()}")
    # ───────────────────────────────────────────────────────────────────────────

    ttm, inc_src = _get_ttm_income(symbol)

    ni_ttm = ttm.get("ni_ttm", pd.Series(dtype=float)).dropna().sort_index()
    if ni_ttm.empty:
        raise ValueError(
            f"Net income TTM data is unavailable for '{symbol}'. "
            "Cannot compute Trailing P/E. Try Price/Sales instead."
        )

    shares, sh_src = _get_shares(symbol)

    eps_ttm = ni_ttm / shares
    eps_ttm.index = _strip_tz(pd.to_datetime(eps_ttm.index))

    # ── TRACE ──────────────────────────────────────────────────────────────────
    print(f"[TRACE] _trailing_pe_series({symbol}) eps_ttm BEFORE reindex")
    print(f"  eps_ttm rows={len(eps_ttm)}  min={eps_ttm.index.min()}  max={eps_ttm.index.max()}")
    print(f"  eps_ttm first5={list(eps_ttm.index[:5])}")
    print(f"  eps_ttm last5={list(eps_ttm.index[-5:])}")
    # ───────────────────────────────────────────────────────────────────────────

    # FAIL EARLY: reindex+ffill produces NaN for every price date before
    # eps_ttm.index.min().  dropna() then silently removes those dates,
    # leaving a series that only covers the tail of the requested period.
    # Check now so we raise a clear error instead of returning a short chart.
    price_span  = _series_span_days(price)
    min_needed  = int(price_span * _PERIOD_COVERAGE_THRESHOLD)
    eps_first   = pd.Timestamp(eps_ttm.index.min())
    price_last  = pd.Timestamp(price.index.max())
    ttm_cover   = (price_last - eps_first).days
    print(f"[TRACE] _trailing_pe_series({symbol}) TTM COVERAGE CHECK")
    print(f"  price_span={price_span} days  min_needed={min_needed} days")
    print(f"  eps_first={eps_first.date()}  ttm_cover={ttm_cover} days")
    print(f"  verdict={'PASS' if ttm_cover >= min_needed else 'FAIL'}")
    if ttm_cover < min_needed:
        raise ValueError(
            f"EPS TTM coverage insufficient for '{symbol}': "
            f"earliest TTM point is {eps_first.date()} "
            f"({ttm_cover} days before end of price window), "
            f"but {min_needed} days are required. "
            f"eps_ttm has {len(eps_ttm)} point(s): "
            f"{eps_ttm.index.min().date()} \u2192 {eps_ttm.index.max().date()}. "
            "Set FMP_API_KEY for extended financial history (up to 44 quarters), "
            "or select a shorter period."
        )

    eps_daily = eps_ttm.reindex(price.index, method="ffill")

    # ── TRACE ──────────────────────────────────────────────────────────────────
    nan_count = eps_daily.isna().sum()
    print(f"[TRACE] _trailing_pe_series({symbol}) eps_daily AFTER reindex+ffill")
    print(f"  eps_daily rows={len(eps_daily)}  NaN count={nan_count}")
    print(f"  eps_daily first non-NaN date={eps_daily.first_valid_index()}")
    # ───────────────────────────────────────────────────────────────────────────

    pe = price / eps_daily
    pe_clean = pe.replace([np.inf, -np.inf], np.nan).dropna()

    # ── TRACE ──────────────────────────────────────────────────────────────────
    print(f"[TRACE] _trailing_pe_series({symbol}) pe AFTER dropna")
    print(f"  pe rows={len(pe_clean)}")
    if not pe_clean.empty:
        print(f"  pe min_date={pe_clean.index.min()}  max_date={pe_clean.index.max()}")
        print(f"  pe first5={list(pe_clean.index[:5])}")
        print(f"  pe last5={list(pe_clean.index[-5:])}")
    # ───────────────────────────────────────────────────────────────────────────

    return pe_clean, [inc_src, sh_src]


def _price_sales_series(
    symbol: str, price: pd.Series
) -> tuple[pd.Series, list[str]]:
    """Build daily Price/Sales. Returns (series, [income_source, shares_source])."""
    ttm, inc_src = _get_ttm_income(symbol)

    rev_ttm = ttm.get("rev_ttm", pd.Series(dtype=float)).dropna().sort_index()
    if rev_ttm.empty:
        raise ValueError(
            f"Revenue TTM data is unavailable for '{symbol}'. "
            "Cannot compute Price/Sales."
        )

    shares, sh_src = _get_shares(symbol)

    rev_ttm.index = _strip_tz(pd.to_datetime(rev_ttm.index))

    # FAIL EARLY: same coverage guard as _trailing_pe_series.
    price_span = _series_span_days(price)
    min_needed = int(price_span * _PERIOD_COVERAGE_THRESHOLD)
    rev_first  = pd.Timestamp(rev_ttm.index.min())
    price_last = pd.Timestamp(price.index.max())
    ttm_cover  = (price_last - rev_first).days
    print(f"[TRACE] _price_sales_series({symbol}) REV TTM COVERAGE CHECK")
    print(f"  price_span={price_span} days  min_needed={min_needed} days")
    print(f"  rev_first={rev_first.date()}  ttm_cover={ttm_cover} days")
    print(f"  verdict={'PASS' if ttm_cover >= min_needed else 'FAIL'}")
    if ttm_cover < min_needed:
        raise ValueError(
            f"Revenue TTM coverage insufficient for '{symbol}': "
            f"earliest TTM point is {rev_first.date()} "
            f"({ttm_cover} days before end of price window), "
            f"but {min_needed} days are required. "
            f"rev_ttm has {len(rev_ttm)} point(s): "
            f"{rev_ttm.index.min().date()} \u2192 {rev_ttm.index.max().date()}. "
            "Set FMP_API_KEY for extended financial history (up to 44 quarters), "
            "or select a shorter period."
        )

    rev_daily = rev_ttm.reindex(price.index, method="ffill")
    market_cap = price * shares
    ps = market_cap / rev_daily
    return ps.replace([np.inf, -np.inf], np.nan).dropna(), [inc_src, sh_src]


# ── Result builder ────────────────────────────────────────────────────────────

def _build_result(
    symbol: str, metric: str, period: str, series: pd.Series, source: str
) -> dict:
    label   = METRIC_LABELS[metric]
    mean    = float(series.mean())
    std     = float(series.std())
    current = float(series.iloc[-1])
    zscore  = (current - mean) / std if std > 0 else 0.0
    summary = _generate_summary(symbol, label, period, mean, std, current, zscore)

    dates = (
        [d.strftime("%Y-%m-%d") for d in series.index]
        if hasattr(series.index[0], "strftime")
        else [str(d) for d in series.index]
    )

    requested_days = PERIOD_DAYS[period]
    actual_days    = _series_span_days(series)

    return {
        "symbol":  symbol,
        "metric":  metric,
        "label":   label,
        "period":  period,
        "points":  len(series),
        "dates":   dates,
        "values":  [round(v, 4) for v in series.tolist()],
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
        "source":  source,
        "debug": {
            "selected_period": period,
            "metric":          metric,
            "requested_days":  requested_days,
            "actual_days":     actual_days,
            "point_count":     len(series),
            "start_date":      dates[0],
            "end_date":        dates[-1],
            "source":          source,
            "x_len":           len(dates),
            "x_min":           dates[0],
            "x_max":           dates[-1],
            "x_first5":        dates[:5],
            "x_last5":         dates[-5:],
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_valuation_series(
    symbol: str,
    metric: str,
    period: str = "5y",
) -> dict:
    """
    Fetch and return a historical valuation series for a ticker.

    Coverage is validated at two levels:
      1. _get_price(): price series span >= 70% of requested days.
      2. Here, after TTM computation: final metric series span >= 70%.
         This catches the case where prices are adequate but quarterly
         fundamentals from yfinance (typically 5-6 quarters) limit the
         P/E or P/S series to only ~1-2 quarters of TTM history.

    Raises ValueError  for invalid input, bad tickers, or insufficient coverage.
    Raises RuntimeError for unexpected pipeline failures.
    """
    symbol = validate_ticker(symbol)
    period = validate_period(period)
    metric = validate_metric(metric)
    days   = PERIOD_DAYS[period]

    # ── API-level entry log ───────────────────────────────────────────────────
    logger.info(
        "REQUEST  symbol=%s  metric=%s  period=%s  requested_days=%d",
        symbol, metric, period, days,
    )

    # ── TRACE ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"[TRACE] fetch_valuation_series  symbol={symbol}  metric={metric}  period={period}  requested_days={days}")
    print(f"{'='*60}")
    # ───────────────────────────────────────────────────────────────────────────

    price, price_src = _get_price(symbol, days)

    logger.info(
        "PRICE    symbol=%s  source=%s  span=%d days  points=%d  "
        "start=%s  end=%s",
        symbol, price_src, _series_span_days(price), len(price),
        pd.Timestamp(price.index[0]).date(),
        pd.Timestamp(price.index[-1]).date(),
    )

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

    # ── TRACE ──────────────────────────────────────────────────────────────────
    print(f"\n[TRACE] fetch_valuation_series({symbol}) AFTER _clean_series")
    print(f"  period={period}  requested_days={days}")
    print(f"  final series rows={len(series)}")
    if not series.empty:
        print(f"  final series min_date={series.index.min()}")
        print(f"  final series max_date={series.index.max()}")
        print(f"  final series first5={list(series.index[:5])}")
        print(f"  final series last5={list(series.index[-5:])}")
        span = _series_span_days(series)
        min_needed = int(days * _PERIOD_COVERAGE_THRESHOLD)
        print(f"  span_days={span}  min_needed={min_needed}  passes={'YES' if span >= min_needed else 'NO'}")
    # ───────────────────────────────────────────────────────────────────────────

    # Compute source label BEFORE the coverage check so it appears in error messages.
    all_sources = [price_src, *fund_sources]
    if all(s == "fmp"       for s in all_sources):
        source = "fmp"
    elif all(s == "yfinance" for s in all_sources):
        source = "yfinance"
    else:
        source = "mixed"

    # ── Final series coverage check ───────────────────────────────────────────
    # The price coverage check in _get_price is not sufficient on its own:
    # yfinance quarterly_income_stmt typically returns only 5-6 quarters, so
    # after the 4-quarter TTM rolling window and dropna() the metric series
    # can start 1-2 quarters ago — even though 3+ years of prices exist.
    # Example: 3y request → 3y of prices ✓ → but only 5 yfinance quarters →
    #          TTM first valid in Q3 2025 → .dropna() leaves ~124 pts / 5 months.
    # NOTE: reindex/ffill + dropna silently shrinks the series to the range
    # covered by quarterly data; this check enforces the 70% floor explicitly.
    final_span  = _series_span_days(series)
    min_final   = int(days * _PERIOD_COVERAGE_THRESHOLD)
    sdates      = [str(pd.Timestamp(d).date()) for d in series.index]
    if final_span < min_final:
        err_debug = {
            "selected_period": period,
            "metric":          metric,
            "requested_days":  days,
            "actual_days":     final_span,
            "point_count":     len(series),
            "x_len":           len(series),
            "x_min":           sdates[0],
            "x_max":           sdates[-1],
            "x_first5":        sdates[:5],
            "x_last5":         sdates[-5:],
            "source":          source,
        }
        raise InsufficientCoverageError(
            f"{METRIC_LABELS[metric]} for '{symbol}' covers only {final_span} "
            f"calendar days after TTM computation "
            f"({sdates[0]} \u2192 {sdates[-1]}), "
            f"but period '{period}' ({days} days) requires \u2265{min_final} days "
            f"({_PERIOD_COVERAGE_THRESHOLD:.0%} threshold). "
            f"Points: {len(series)}. Source: {source}. "
            "The quarterly financial history is too limited for this period. "
            "Set FMP_API_KEY for extended data (up to 44 quarters), or select a "
            "shorter period.",
            debug=err_debug,
        )

    # ── API-level response log ────────────────────────────────────────────────
    logger.info(
        "RESPONSE symbol=%s  source=%s  metric=%s  period=%s  "
        "start=%s  end=%s  actual_days=%d  points=%d",
        symbol, source, metric, period,
        pd.Timestamp(series.index[0]).date(),
        pd.Timestamp(series.index[-1]).date(),
        final_span, len(series),
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
