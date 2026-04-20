"""Broker abstraction.

Two backends:
  - AlpacaBroker: real paper-trading account, real-time IEX quotes.
  - LocalBroker:  in-process paper broker using the DB. Live price via yfinance
                  fast_info (short-latency, not tick-level).

Selection is automatic based on app.config.settings.use_alpaca.
Both expose: get_price(symbol), get_prices(symbols), is_market_open(),
            place_buy(symbol, qty, reason), place_sell(symbol, qty, reason).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import yfinance as yf

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Fill:
    symbol: str
    side: str          # buy | sell
    quantity: float
    price: float
    executed_at: datetime


class Broker(Protocol):
    backend: str
    def get_price(self, symbol: str) -> float | None: ...
    def get_prices(self, symbols: list[str]) -> dict[str, float]: ...
    def is_market_open(self) -> bool: ...
    def place_buy(self, symbol: str, quantity: float, reason: str) -> Fill | None: ...
    def place_sell(self, symbol: str, quantity: float, reason: str) -> Fill | None: ...


# ═══════════════════════════════════════════════════════════════════════════════
#  Alpaca
# ═══════════════════════════════════════════════════════════════════════════════

class AlpacaBroker:
    backend = "alpaca"

    def __init__(self) -> None:
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.live import StockDataStream  # noqa: F401 (available for future WS)

        self._trading = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_api_secret,
            paper=True,
        )
        self._data = StockHistoricalDataClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_api_secret,
        )
        self._feed = settings.alpaca_data_feed

    # ── Quotes ────────────────────────────────────────────────────────────
    def get_price(self, symbol: str) -> float | None:
        try:
            from alpaca.data.requests import StockLatestTradeRequest
            from alpaca.data.enums import DataFeed

            feed = DataFeed.SIP if self._feed == "sip" else DataFeed.IEX
            req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=feed)
            resp = self._data.get_stock_latest_trade(req)
            trade = resp.get(symbol)
            if trade and trade.price:
                return float(trade.price)
        except Exception as exc:
            logger.warning("Alpaca get_price failed for %s: %s — falling back to yfinance", symbol, exc)
        return _yfinance_price(symbol)

    def get_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        try:
            from alpaca.data.requests import StockLatestTradeRequest
            from alpaca.data.enums import DataFeed

            feed = DataFeed.SIP if self._feed == "sip" else DataFeed.IEX
            req = StockLatestTradeRequest(symbol_or_symbols=symbols, feed=feed)
            resp = self._data.get_stock_latest_trade(req)
            out: dict[str, float] = {}
            for sym in symbols:
                trade = resp.get(sym)
                if trade and trade.price:
                    out[sym] = float(trade.price)
            # Fill any missing with yfinance
            missing = [s for s in symbols if s not in out]
            for s in missing:
                p = _yfinance_price(s)
                if p is not None:
                    out[s] = p
            return out
        except Exception as exc:
            logger.warning("Alpaca batch quote failed: %s — falling back to yfinance", exc)
            return {s: p for s in symbols if (p := _yfinance_price(s)) is not None}

    # ── Clock ─────────────────────────────────────────────────────────────
    def is_market_open(self) -> bool:
        try:
            clock = self._trading.get_clock()
            return bool(clock.is_open)
        except Exception as exc:
            logger.warning("Alpaca clock check failed: %s", exc)
            return _fallback_market_open()

    # ── Orders ────────────────────────────────────────────────────────────
    def _market_order(self, symbol: str, qty: float, side: str, reason: str) -> Fill | None:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        if qty <= 0:
            return None
        # Alpaca fractional shares are allowed for many stocks but require qty rounding
        qty = round(qty, 4)
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            client_order_id=f"agent-{side}-{symbol}-{int(datetime.now().timestamp())}",
        )
        try:
            order = self._trading.submit_order(req)
            # Alpaca fills are async; use the last trade as the recorded price
            price = self.get_price(symbol) or 0.0
            logger.info("[ALPACA] %s %s qty=%s price≈%.2f reason=%s id=%s",
                        side.upper(), symbol, qty, price, reason, order.id)
            return Fill(symbol=symbol, side=side, quantity=qty, price=price,
                        executed_at=datetime.now(timezone.utc))
        except Exception as exc:
            logger.error("Alpaca %s order failed for %s: %s", side, symbol, exc)
            return None

    def place_buy(self, symbol: str, quantity: float, reason: str) -> Fill | None:
        return self._market_order(symbol, quantity, "buy", reason)

    def place_sell(self, symbol: str, quantity: float, reason: str) -> Fill | None:
        return self._market_order(symbol, quantity, "sell", reason)


# ═══════════════════════════════════════════════════════════════════════════════
#  Local paper broker (DB-backed, yfinance fills)
# ═══════════════════════════════════════════════════════════════════════════════

class LocalBroker:
    backend = "local"

    def get_price(self, symbol: str) -> float | None:
        return _yfinance_price(symbol)

    def get_prices(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in symbols:
            p = _yfinance_price(s)
            if p is not None:
                out[s] = p
        return out

    def is_market_open(self) -> bool:
        return _fallback_market_open()

    def place_buy(self, symbol: str, quantity: float, reason: str) -> Fill | None:
        price = self.get_price(symbol)
        if price is None or quantity <= 0:
            return None
        return Fill(symbol=symbol, side="buy", quantity=quantity, price=price,
                    executed_at=datetime.now(timezone.utc))

    def place_sell(self, symbol: str, quantity: float, reason: str) -> Fill | None:
        price = self.get_price(symbol)
        if price is None or quantity <= 0:
            return None
        return Fill(symbol=symbol, side="sell", quantity=quantity, price=price,
                    executed_at=datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════════════════════
#  Shared helpers
# ═══════════════════════════════════════════════════════════════════════════════

_PRICE_CACHE: dict[str, tuple[float, float]] = {}  # symbol -> (price, ts)
_CACHE_TTL = 10.0  # seconds


def _yfinance_price(symbol: str) -> float | None:
    """Best-effort live quote via yfinance.fast_info.last_price (cached 10s)."""
    import time
    now = time.time()
    cached = _PRICE_CACHE.get(symbol)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]
    try:
        t = yf.Ticker(symbol)
        p = float(t.fast_info.last_price)
        if p > 0:
            _PRICE_CACHE[symbol] = (p, now)
            return p
    except Exception:
        pass
    return None


def _fallback_market_open() -> bool:
    """Approximate US market hours: Mon–Fri 09:30–16:00 America/New_York."""
    import pytz
    now = datetime.now(pytz.timezone("America/New_York"))
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 30) <= minutes < (16 * 60)


# ═══════════════════════════════════════════════════════════════════════════════
#  Factory
# ═══════════════════════════════════════════════════════════════════════════════

_broker: Broker | None = None


def get_broker() -> Broker:
    global _broker
    if _broker is not None:
        return _broker
    if settings.use_alpaca:
        try:
            _broker = AlpacaBroker()
            logger.info("Broker: Alpaca paper-trading (feed=%s)", settings.alpaca_data_feed)
            return _broker
        except Exception as exc:
            logger.warning("Alpaca init failed (%s) — using LocalBroker", exc)
    _broker = LocalBroker()
    logger.info("Broker: LocalBroker (yfinance quotes)")
    return _broker
