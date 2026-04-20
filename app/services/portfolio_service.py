"""Portfolio math: positions, P&L, equity curve, trade execution into DB."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.db import session_scope
from app.models import AgentLog, EquitySnapshot, Portfolio, Position, Trade
from app.services.broker import Fill, get_broker

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────

def _get_portfolio(s: Session, name: str) -> Portfolio:
    p = s.query(Portfolio).filter_by(name=name).first()
    if not p:
        raise ValueError(f"Portfolio '{name}' not found (db not initialized?).")
    return p


def _positions_value(s: Session, portfolio_id: int, prices: dict[str, float]) -> float:
    total = 0.0
    for pos in s.query(Position).filter_by(portfolio_id=portfolio_id).all():
        price = prices.get(pos.symbol) or pos.avg_entry_price
        total += pos.quantity * price
    return total


# ── Execution ─────────────────────────────────────────────────────────────

def execute_buy(
    portfolio_name: str,
    symbol: str,
    dollars: float,
    reason: str,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
) -> Optional[Trade]:
    """Place a market buy via broker, record in DB, create/append position."""
    broker = get_broker()
    price = broker.get_price(symbol)
    if price is None or price <= 0:
        logger.warning("execute_buy(%s): no price available", symbol)
        return None

    quantity = round(dollars / price, 4)
    if quantity <= 0:
        return None

    # For the agent portfolio only, submit to broker. User portfolio is local-only.
    if portfolio_name == "agent":
        fill = broker.place_buy(symbol, quantity, reason)
        if fill is None:
            logger.warning("execute_buy: broker.place_buy returned None for %s", symbol)
            return None
        price = fill.price or price

    with session_scope() as s:
        p = _get_portfolio(s, portfolio_name)
        cost = quantity * price
        if cost > p.cash + 1e-6:
            # Shrink quantity to available cash
            quantity = round(p.cash / price, 4)
            cost = quantity * price
            if quantity <= 0:
                logger.info("execute_buy(%s): insufficient cash", symbol)
                return None

        p.cash -= cost

        pos = s.query(Position).filter_by(portfolio_id=p.id, symbol=symbol).first()
        slp = stop_loss_pct if stop_loss_pct is not None else settings.stop_loss_pct
        tpp = take_profit_pct if take_profit_pct is not None else settings.take_profit_pct
        new_stop = price * (1 - slp / 100.0)
        new_tp   = price * (1 + tpp / 100.0)

        if pos is None:
            pos = Position(
                portfolio_id=p.id, symbol=symbol,
                quantity=quantity, avg_entry_price=price,
                stop_loss=new_stop, take_profit=new_tp,
                thesis=reason[:500],
            )
            s.add(pos)
        else:
            total_qty = pos.quantity + quantity
            pos.avg_entry_price = (pos.avg_entry_price * pos.quantity + price * quantity) / total_qty
            pos.quantity = total_qty
            pos.stop_loss = pos.avg_entry_price * (1 - slp / 100.0)
            pos.take_profit = pos.avg_entry_price * (1 + tpp / 100.0)

        trade = Trade(
            portfolio_id=p.id, symbol=symbol, side="buy",
            quantity=quantity, price=price, reason=reason,
        )
        s.add(trade)
        s.add(AgentLog(category="order",
                       message=f"[{portfolio_name}] BUY {quantity:.2f} {symbol} @ ${price:.2f} — {reason}"))
        s.flush()
        return trade


def execute_sell(
    portfolio_name: str,
    symbol: str,
    quantity: Optional[float],
    reason: str,
) -> Optional[Trade]:
    """Close all or part of a position. quantity=None → close all."""
    broker = get_broker()
    price = broker.get_price(symbol)
    if price is None or price <= 0:
        logger.warning("execute_sell(%s): no price available", symbol)
        return None

    with session_scope() as s:
        p = _get_portfolio(s, portfolio_name)
        pos = s.query(Position).filter_by(portfolio_id=p.id, symbol=symbol).first()
        if pos is None or pos.quantity <= 0:
            return None

        qty = pos.quantity if (quantity is None or quantity >= pos.quantity) else quantity
        qty = round(qty, 4)
        if qty <= 0:
            return None

        if portfolio_name == "agent":
            fill = broker.place_sell(symbol, qty, reason)
            if fill is None:
                return None
            price = fill.price or price

        proceeds = qty * price
        realized = (price - pos.avg_entry_price) * qty
        p.cash += proceeds
        pos.quantity -= qty
        if pos.quantity <= 1e-6:
            s.delete(pos)

        trade = Trade(
            portfolio_id=p.id, symbol=symbol, side="sell",
            quantity=qty, price=price, reason=reason, realized_pnl=realized,
        )
        s.add(trade)
        s.add(AgentLog(category="order",
                       message=f"[{portfolio_name}] SELL {qty:.2f} {symbol} @ ${price:.2f} "
                               f"(PnL {realized:+.2f}) — {reason}"))
        s.flush()
        return trade


# ── Snapshots ─────────────────────────────────────────────────────────────

def snapshot_equity(portfolio_name: str, prices: Optional[dict[str, float]] = None) -> dict:
    """Compute and persist a fresh equity snapshot."""
    broker = get_broker()
    with session_scope() as s:
        p = _get_portfolio(s, portfolio_name)
        symbols = [pos.symbol for pos in s.query(Position).filter_by(portfolio_id=p.id).all()]
        if prices is None:
            prices = broker.get_prices(symbols) if symbols else {}
        pv = _positions_value(s, p.id, prices)
        equity = p.cash + pv
        snap = EquitySnapshot(
            portfolio_id=p.id, cash=p.cash, positions_value=pv, equity=equity,
        )
        s.add(snap)
        s.flush()
        return {
            "portfolio": portfolio_name,
            "timestamp": snap.timestamp.isoformat(),
            "cash": p.cash,
            "positions_value": pv,
            "equity": equity,
        }


def snapshot_both() -> dict[str, dict]:
    # Combine both portfolios' symbols for a single price batch
    broker = get_broker()
    with session_scope() as s:
        symbols: set[str] = set()
        for name in ("agent", "user"):
            p = _get_portfolio(s, name)
            for pos in s.query(Position).filter_by(portfolio_id=p.id).all():
                symbols.add(pos.symbol)
    prices = broker.get_prices(list(symbols)) if symbols else {}
    return {
        "agent": snapshot_equity("agent", prices),
        "user":  snapshot_equity("user",  prices),
    }


# ── Read APIs ─────────────────────────────────────────────────────────────

def get_state(portfolio_name: str) -> dict:
    """Full state of a portfolio: cash, positions with live prices, equity."""
    broker = get_broker()
    with session_scope() as s:
        p = _get_portfolio(s, portfolio_name)
        position_rows = s.query(Position).filter_by(portfolio_id=p.id).all()
        symbols = [pos.symbol for pos in position_rows]
        prices = broker.get_prices(symbols) if symbols else {}
        positions = []
        pv = 0.0
        for pos in position_rows:
            last = prices.get(pos.symbol) or pos.avg_entry_price
            val = pos.quantity * last
            pv += val
            cost = pos.avg_entry_price * pos.quantity
            positions.append({
                "symbol": pos.symbol,
                "quantity": pos.quantity,
                "avg_entry_price": pos.avg_entry_price,
                "last_price": last,
                "market_value": val,
                "unrealized_pnl": val - cost,
                "unrealized_pnl_pct": (last / pos.avg_entry_price - 1) * 100 if pos.avg_entry_price else 0,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "thesis": pos.thesis,
                "opened_at": pos.opened_at.isoformat(),
            })
        equity = p.cash + pv
        return {
            "name": p.name,
            "starting_capital": p.starting_capital,
            "cash": p.cash,
            "positions_value": pv,
            "equity": equity,
            "total_return_pct": (equity / p.starting_capital - 1) * 100,
            "positions": positions,
        }


def equity_curve(portfolio_name: str, days: int = 365) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=days)
    with session_scope() as s:
        p = _get_portfolio(s, portfolio_name)
        rows = (s.query(EquitySnapshot)
                  .filter(EquitySnapshot.portfolio_id == p.id,
                          EquitySnapshot.timestamp >= since)
                  .order_by(EquitySnapshot.timestamp.asc()).all())
        return [
            {"t": r.timestamp.isoformat(), "equity": r.equity, "cash": r.cash}
            for r in rows
        ]


def recent_trades(portfolio_name: str, limit: int = 50) -> list[dict]:
    with session_scope() as s:
        p = _get_portfolio(s, portfolio_name)
        rows = (s.query(Trade)
                  .filter_by(portfolio_id=p.id)
                  .order_by(Trade.executed_at.desc()).limit(limit).all())
        return [
            {
                "symbol":       t.symbol,
                "side":         t.side,
                "quantity":     t.quantity,
                "price":        t.price,
                "executed_at":  t.executed_at.isoformat(),
                "reason":       t.reason,
                "realized_pnl": t.realized_pnl,
            }
            for t in rows
        ]


def trades_on_day(portfolio_name: str, d: date) -> list[dict]:
    start = datetime.combine(d, time.min)
    end   = start + timedelta(days=1)
    with session_scope() as s:
        p = _get_portfolio(s, portfolio_name)
        rows = (s.query(Trade)
                  .filter(Trade.portfolio_id == p.id,
                          Trade.executed_at >= start,
                          Trade.executed_at < end)
                  .order_by(Trade.executed_at.asc()).all())
        return [
            {"symbol": t.symbol, "side": t.side, "quantity": t.quantity,
             "price": t.price, "reason": t.reason, "realized_pnl": t.realized_pnl}
            for t in rows
        ]


def day_pnl(portfolio_name: str, d: date) -> float:
    """Compare last equity snapshot of day d to previous day's last snapshot."""
    start = datetime.combine(d, time.min)
    end   = start + timedelta(days=1)
    with session_scope() as s:
        p = _get_portfolio(s, portfolio_name)
        today_last = (s.query(EquitySnapshot)
                        .filter(EquitySnapshot.portfolio_id == p.id,
                                EquitySnapshot.timestamp >= start,
                                EquitySnapshot.timestamp < end)
                        .order_by(EquitySnapshot.timestamp.desc()).first())
        if not today_last:
            return 0.0
        prev = (s.query(EquitySnapshot)
                  .filter(EquitySnapshot.portfolio_id == p.id,
                          EquitySnapshot.timestamp < start)
                  .order_by(EquitySnapshot.timestamp.desc()).first())
        prev_eq = prev.equity if prev else p.starting_capital
        return today_last.equity - prev_eq
