"""REST API for the portfolio dashboard.

Endpoints are deliberately flat and JSON-only so the frontend can poll them
every few seconds.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.db import session_scope
from app.models import AgentLog, DailySummary, Pick
from app.services import macro, picker, portfolio_service, scheduler, trader
from app.services.broker import get_broker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


# ── Schemas ───────────────────────────────────────────────────────────────

class ManualTradeIn(BaseModel):
    symbol:   str   = Field(..., min_length=1, max_length=10)
    side:     str   = Field(..., pattern="^(buy|sell)$")
    dollars:  Optional[float] = None         # required for buy
    quantity: Optional[float] = None         # optional for sell; None = all
    reason:   str   = Field(default="manual user trade", max_length=400)


# ── Status / config ───────────────────────────────────────────────────────

@router.get("/status")
def status():
    broker = get_broker()
    return {
        "broker":      broker.backend,
        "market_open": broker.is_market_open(),
        "use_alpaca":  settings.use_alpaca,
        "use_claude":  settings.use_claude,
        "timezone":    settings.timezone,
        "limits": {
            "max_position_pct": settings.max_position_pct,
            "max_positions":    settings.max_positions,
            "stop_loss_pct":    settings.stop_loss_pct,
            "take_profit_pct":  settings.take_profit_pct,
            "vix_defensive":    settings.vix_defensive,
            "vix_risk_off":     settings.vix_risk_off,
        },
    }


# ── Portfolio state ───────────────────────────────────────────────────────

@router.get("/state/{name}")
def state(name: str):
    if name not in ("agent", "user"):
        raise HTTPException(400, "name must be 'agent' or 'user'")
    try:
        return portfolio_service.get_state(name)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/compare")
def compare():
    """Side-by-side view for the dashboard."""
    return {
        "agent": portfolio_service.get_state("agent"),
        "user":  portfolio_service.get_state("user"),
    }


@router.get("/equity/{name}")
def equity_curve(name: str, days: int = Query(default=365, ge=1, le=3650)):
    if name not in ("agent", "user"):
        raise HTTPException(400, "name must be 'agent' or 'user'")
    return {"curve": portfolio_service.equity_curve(name, days=days)}


@router.get("/trades/{name}")
def trades(name: str, limit: int = Query(default=50, ge=1, le=500)):
    if name not in ("agent", "user"):
        raise HTTPException(400, "name must be 'agent' or 'user'")
    return {"trades": portfolio_service.recent_trades(name, limit=limit)}


# ── Picks ─────────────────────────────────────────────────────────────────

@router.get("/picks")
def picks(weeks: int = Query(default=1, ge=1, le=12)):
    with session_scope() as s:
        rows = (s.query(Pick)
                  .order_by(Pick.week_of.desc(), Pick.rank)
                  .limit(weeks * 8).all())
        return {
            "picks": [
                {
                    "week_of":       str(p.week_of),
                    "symbol":        p.symbol,
                    "rank":          p.rank,
                    "score":         p.score,
                    "thesis":        p.thesis,
                    "metric":        p.metric,
                    "zscore":        p.zscore,
                    "price_at_pick": p.price_at_pick,
                }
                for p in rows
            ]
        }


# ── Macro snapshot ────────────────────────────────────────────────────────

@router.get("/macro")
def macro_snapshot():
    m = macro.snapshot(persist=False)
    return {
        "vix":            m.vix,
        "dxy":            m.dxy,
        "tlt":            m.tlt,
        "oil":            m.oil,
        "put_call_ratio": m.put_call_ratio,
        "regime":         m.regime,
        "notes":          m.notes,
    }


# ── Daily summaries & logs ────────────────────────────────────────────────

@router.get("/summaries")
def summaries(limit: int = Query(default=14, ge=1, le=90)):
    with session_scope() as s:
        rows = (s.query(DailySummary)
                  .order_by(DailySummary.date.desc()).limit(limit).all())
        return {
            "summaries": [
                {
                    "date":     str(r.date),
                    "regime":   r.regime,
                    "pnl_day":  r.pnl_day,
                    "trades":   r.trade_count,
                    "markdown": r.summary_markdown,
                }
                for r in rows
            ]
        }


@router.get("/logs")
def logs(limit: int = Query(default=100, ge=1, le=500),
         category: Optional[str] = None):
    with session_scope() as s:
        q = s.query(AgentLog).order_by(AgentLog.timestamp.desc())
        if category:
            q = q.filter(AgentLog.category == category)
        rows = q.limit(limit).all()
        return {
            "logs": [
                {"t": r.timestamp.isoformat(), "level": r.level,
                 "category": r.category, "message": r.message}
                for r in rows
            ]
        }


# ── Manual actions (user portfolio / control buttons) ─────────────────────

@router.post("/user/trade")
def user_trade(t: ManualTradeIn):
    """Record a manual trade on the user portfolio (not the agent)."""
    try:
        symbol = t.symbol.strip().upper()
        if t.side == "buy":
            if not t.dollars or t.dollars <= 0:
                raise HTTPException(400, "'dollars' required for buy")
            trade = portfolio_service.execute_buy("user", symbol, t.dollars, t.reason)
        else:
            trade = portfolio_service.execute_sell("user", symbol, t.quantity, t.reason)
        if trade is None:
            raise HTTPException(400, "trade could not be executed "
                                     "(insufficient cash, no position, or price unavailable)")
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/run/{job}")
def run_job(job: str):
    """Manually trigger a scheduled job. Useful to bootstrap picks or summary."""
    try:
        result = scheduler.run_now(job)
        return {"ok": True, "result": result}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.exception("run_job(%s) failed", job)
        raise HTTPException(500, str(exc))
