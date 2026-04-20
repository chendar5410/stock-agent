"""Weekly stock picker.

Scoring:
  - Stocks: z-score of forward P/E over 5y (more negative = cheaper = better).
           Fallback to trailing P/E, then to 20d momentum if fundamentals fail.
  - Crypto ETFs: 20-day price momentum (% change) ranked vs the crypto cohort —
                 fundamentals are N/A for ETFs tracking underlying assets.

Output: top-N composite list written to DB with week_of = Monday of current week,
        plus a human-readable thesis per pick.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import yfinance as yf

from app.db import session_scope
from app.models import AgentLog, Pick
from app.services import universe
from app.services.broker import get_broker

logger = logging.getLogger(__name__)

N_STOCK_PICKS  = 5
N_CRYPTO_PICKS = 2


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _zscore_forward_pe(symbol: str) -> Optional[dict]:
    """Fundamental score using the existing valuation pipeline."""
    try:
        from app.services.data_fetcher import fetch_valuation_series
        result = fetch_valuation_series(symbol, metric="forward_pe", period="5y")
        return {
            "metric": "forward_pe",
            "zscore": float(result["zscore"]),
            "score":  -float(result["zscore"]),   # cheaper = higher score
            "thesis": result["summary"],
        }
    except Exception as exc:
        logger.debug("forward_pe z-score unavailable for %s: %s", symbol, exc)
    return None


def _momentum_score(symbol: str, days: int = 20) -> Optional[dict]:
    """Fallback: 20-day price momentum."""
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period=f"{days + 10}d", auto_adjust=True)
        if hist.empty or len(hist) < days:
            return None
        closes = hist["Close"].dropna()
        mom = float((closes.iloc[-1] / closes.iloc[-days] - 1.0) * 100.0)
        return {
            "metric": "momentum_20d",
            "zscore": None,
            "score":  mom,      # higher momentum = higher score (for crypto)
            "thesis": f"{symbol} momentum: {mom:+.1f}% over last {days} trading days.",
        }
    except Exception as exc:
        logger.debug("momentum score failed for %s: %s", symbol, exc)
    return None


def _rank_stocks() -> list[dict]:
    candidates: list[dict] = []
    for sym in universe.CORE_STOCKS:
        scored = _zscore_forward_pe(sym) or _momentum_score(sym)
        if scored is None:
            continue
        scored["symbol"] = sym
        candidates.append(scored)
    # Higher score = better (cheap valuation or strong momentum)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:N_STOCK_PICKS]


def _rank_cryptos() -> list[dict]:
    candidates: list[dict] = []
    for sym in universe.CRYPTO_ETFS:
        scored = _momentum_score(sym, days=20)
        if scored is None:
            continue
        scored["symbol"] = sym
        candidates.append(scored)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:N_CRYPTO_PICKS]


def run_picker() -> list[dict]:
    """Produce this week's picks and persist to DB. Idempotent per week."""
    broker = get_broker()
    wk = _monday_of(date.today())

    logger.info("[PICKER] running for week_of=%s", wk)

    with session_scope() as s:
        # Skip if already ran this week
        existing = s.query(Pick).filter_by(week_of=wk).all()
        if existing:
            logger.info("[PICKER] picks for week %s already exist (%d) — skipping", wk, len(existing))
            return [_pick_to_dict(p) for p in existing]

    stocks  = _rank_stocks()
    cryptos = _rank_cryptos()

    all_picks: list[dict] = []
    rank = 1
    for p in stocks + cryptos:
        price = broker.get_price(p["symbol"]) or 0.0
        p["rank"] = rank
        p["price_at_pick"] = price
        p["week_of"] = wk
        all_picks.append(p)
        rank += 1

    with session_scope() as s:
        for p in all_picks:
            s.add(Pick(
                week_of=p["week_of"],
                symbol=p["symbol"],
                rank=p["rank"],
                score=float(p["score"]),
                thesis=p.get("thesis"),
                metric=p.get("metric"),
                zscore=p.get("zscore"),
                price_at_pick=p["price_at_pick"],
            ))
        s.add(AgentLog(
            category="pick",
            message=f"Weekly picks generated for {wk}: {[p['symbol'] for p in all_picks]}",
        ))

    logger.info("[PICKER] done: %s", [p["symbol"] for p in all_picks])
    return all_picks


def _pick_to_dict(p: Pick) -> dict:
    return {
        "symbol":        p.symbol,
        "rank":          p.rank,
        "score":         p.score,
        "thesis":        p.thesis,
        "metric":        p.metric,
        "zscore":        p.zscore,
        "price_at_pick": p.price_at_pick,
        "week_of":       str(p.week_of),
    }


def latest_picks() -> list[dict]:
    wk = _monday_of(date.today())
    with session_scope() as s:
        rows = s.query(Pick).filter_by(week_of=wk).order_by(Pick.rank).all()
        if not rows:
            # Look back further in case today is before Monday's run
            rows = (s.query(Pick)
                      .order_by(Pick.week_of.desc(), Pick.rank)
                      .limit(N_STOCK_PICKS + N_CRYPTO_PICKS).all())
        return [_pick_to_dict(p) for p in rows]
