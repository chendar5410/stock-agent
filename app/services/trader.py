"""Trader engine — the agent's decision loop.

Called by the scheduler every N minutes during market hours.
Sequence:
  1. Refresh broker quotes for current positions.
  2. Take a fresh macro snapshot (VIX, DXY, TLT, oil, put/call) → regime.
  3. Enforce hard rules: stop-loss, take-profit, VIX-driven risk-off.
  4. Ask reasoning layer for candidate actions given picks + macro.
  5. Execute resulting buys/sells against the agent portfolio.
  6. Write an equity snapshot.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from app.config import settings
from app.db import session_scope
from app.models import AgentLog, Pick, Position
from app.services import macro, picker, portfolio_service, reasoning
from app.services.broker import get_broker

logger = logging.getLogger(__name__)


def _build_context() -> dict:
    broker = get_broker()
    m = macro.snapshot(persist=False)
    state = portfolio_service.get_state("agent")

    # Latest picks for the week (generate if missing)
    picks = picker.latest_picks()
    if not picks:
        try:
            picks = picker.run_picker()
        except Exception as exc:
            logger.warning("Picker failed in trader bootstrap: %s", exc)
            picks = []

    candidates = [
        {"symbol": p["symbol"], "rank": p["rank"], "score": p["score"],
         "metric": p["metric"], "zscore": p["zscore"], "thesis": p["thesis"]}
        for p in picks
    ]

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "cash":      state["cash"],
        "equity":    state["equity"],
        "positions": [
            {
                "symbol":          pos["symbol"],
                "quantity":        pos["quantity"],
                "avg_entry_price": pos["avg_entry_price"],
                "last_price":      pos["last_price"],
                "unrealized_pnl":  pos["unrealized_pnl"],
                "stop_loss":       pos["stop_loss"],
                "take_profit":     pos["take_profit"],
            }
            for pos in state["positions"]
        ],
        "candidates": candidates,
        "macro": {
            "vix": m.vix, "dxy": m.dxy, "tlt": m.tlt, "oil": m.oil,
            "put_call_ratio": m.put_call_ratio, "regime": m.regime, "notes": m.notes,
        },
        "limits": {
            "max_position_pct": settings.max_position_pct,
            "max_positions":    settings.max_positions,
            "stop_loss_pct":    settings.stop_loss_pct,
            "take_profit_pct":  settings.take_profit_pct,
        },
    }


def tick() -> dict:
    """One agent tick. Returns a dict describing what happened."""
    broker = get_broker()
    if not broker.is_market_open():
        logger.debug("[TRADER] market closed — skipping tick")
        return {"skipped": "market_closed"}

    # Persist a macro snapshot at the start of each tick
    macro.snapshot(persist=True)

    ctx = _build_context()
    actions = reasoning.intraday_decide(ctx)

    executed: list[dict] = []
    for a in actions:
        try:
            if a.action == "buy" and a.dollars > 0:
                t = portfolio_service.execute_buy("agent", a.symbol, a.dollars, a.reason)
                if t is not None:
                    executed.append({"side": "buy", "symbol": a.symbol,
                                     "dollars": a.dollars, "reason": a.reason})
            elif a.action == "sell":
                t = portfolio_service.execute_sell("agent", a.symbol, None, a.reason)
                if t is not None:
                    executed.append({"side": "sell", "symbol": a.symbol, "reason": a.reason})
        except Exception as exc:
            logger.error("Execute action failed (%s %s): %s", a.action, a.symbol, exc)

    portfolio_service.snapshot_equity("agent")
    portfolio_service.snapshot_equity("user")

    if executed:
        with session_scope() as s:
            s.add(AgentLog(category="intraday",
                           message=f"tick executed {len(executed)} actions",
                           data_json=str(executed)))

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "regime":    ctx["macro"]["regime"],
        "actions":   len(actions),
        "executed":  executed,
    }


def generate_daily_summary(for_date: date | None = None) -> str:
    """Produce and persist the end-of-day narrative summary for `for_date`."""
    d = for_date or date.today()
    broker = get_broker()
    m = macro.snapshot(persist=False)

    state   = portfolio_service.get_state("agent")
    trades  = portfolio_service.trades_on_day("agent", d)
    pnl     = portfolio_service.day_pnl("agent", d)

    ctx = {
        "date":         str(d),
        "equity":       state["equity"],
        "cash":         state["cash"],
        "positions":    state["positions"],
        "trades_today": trades,
        "pnl_day":      pnl,
        "macro": {
            "vix": m.vix, "dxy": m.dxy, "tlt": m.tlt, "oil": m.oil,
            "put_call_ratio": m.put_call_ratio, "regime": m.regime, "notes": m.notes,
        },
    }

    text = reasoning.daily_summary(ctx)

    from app.models import DailySummary, MacroSnapshot  # avoid cycles
    import json
    with session_scope() as s:
        existing = s.query(DailySummary).filter_by(date=d).first()
        if existing:
            existing.summary_markdown = text
            existing.trade_count = len(trades)
            existing.pnl_day = pnl
            existing.regime = m.regime
            existing.macro_snapshot_json = json.dumps({
                "vix": m.vix, "dxy": m.dxy, "tlt": m.tlt, "oil": m.oil,
                "put_call_ratio": m.put_call_ratio, "notes": m.notes,
            })
        else:
            s.add(DailySummary(
                date=d,
                summary_markdown=text,
                macro_snapshot_json=json.dumps({
                    "vix": m.vix, "dxy": m.dxy, "tlt": m.tlt, "oil": m.oil,
                    "put_call_ratio": m.put_call_ratio, "notes": m.notes,
                }),
                regime=m.regime,
                trade_count=len(trades),
                pnl_day=pnl,
            ))
        s.add(AgentLog(category="summary",
                       message=f"daily summary persisted for {d} (trades={len(trades)}, pnl={pnl:+.2f})"))

    return text
