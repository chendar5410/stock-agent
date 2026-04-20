"""Macro snapshot: VIX, DXY, TLT, oil, put/call — and regime classification.

All values via yfinance. Cached 60 seconds to avoid rate-limiting.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Optional

import yfinance as yf

from app.db import session_scope
from app.models import MacroSnapshot

logger = logging.getLogger(__name__)

# yfinance symbols
VIX_SYM  = "^VIX"
DXY_SYM  = "DX-Y.NYB"   # US Dollar Index
TLT_SYM  = "TLT"        # 20y+ Treasury ETF — inverse proxy for long-end yields
OIL_SYM  = "CL=F"       # WTI crude futures
SPY_SYM  = "SPY"

_CACHE: dict[str, tuple[float, float]] = {}
_CACHE_TTL = 60.0


@dataclass
class MacroData:
    vix: Optional[float] = None
    dxy: Optional[float] = None
    tlt: Optional[float] = None
    oil: Optional[float] = None
    put_call_ratio: Optional[float] = None
    regime: str = "neutral"
    notes: list[str] = None

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []


def _quote(symbol: str) -> Optional[float]:
    now = time.time()
    cached = _CACHE.get(symbol)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]
    try:
        t = yf.Ticker(symbol)
        p = float(t.fast_info.last_price)
        if p > 0:
            _CACHE[symbol] = (p, now)
            return p
    except Exception as exc:
        logger.debug("macro quote %s failed: %s", symbol, exc)
    return None


def _spy_put_call_ratio() -> Optional[float]:
    """Approximate put/call ratio from SPY's nearest expiration options open interest."""
    try:
        t = yf.Ticker(SPY_SYM)
        expirations = t.options
        if not expirations:
            return None
        chain = t.option_chain(expirations[0])
        put_oi  = float(chain.puts["openInterest"].fillna(0).sum())
        call_oi = float(chain.calls["openInterest"].fillna(0).sum())
        if call_oi == 0:
            return None
        return round(put_oi / call_oi, 3)
    except Exception as exc:
        logger.debug("put/call ratio failed: %s", exc)
        return None


def _classify_regime(m: MacroData) -> tuple[str, list[str]]:
    """Classify market regime from macro readings. Returns (regime, notes)."""
    notes: list[str] = []
    score = 0

    if m.vix is not None:
        if m.vix >= 32:
            score -= 3; notes.append(f"VIX {m.vix:.1f} — panic/risk-off")
        elif m.vix >= 25:
            score -= 2; notes.append(f"VIX {m.vix:.1f} — elevated fear")
        elif m.vix >= 18:
            score -= 1; notes.append(f"VIX {m.vix:.1f} — mild caution")
        elif m.vix < 14:
            score += 1; notes.append(f"VIX {m.vix:.1f} — complacent / risk-on")

    if m.put_call_ratio is not None:
        if m.put_call_ratio >= 1.3:
            score -= 1; notes.append(f"Put/Call {m.put_call_ratio:.2f} — bearish hedging")
        elif m.put_call_ratio <= 0.7:
            score += 1; notes.append(f"Put/Call {m.put_call_ratio:.2f} — bullish skew")

    # DXY absolute level is less informative than trend; flag extremes only.
    if m.dxy is not None:
        if m.dxy >= 108:
            score -= 1; notes.append(f"DXY {m.dxy:.1f} — strong dollar, EM/tech headwind")
        elif m.dxy <= 98:
            score += 1; notes.append(f"DXY {m.dxy:.1f} — weak dollar, risk-on tailwind")

    if score <= -2:
        regime = "risk_off"
    elif score >= 2:
        regime = "risk_on"
    else:
        regime = "neutral"
    return regime, notes


def snapshot(persist: bool = True) -> MacroData:
    """Take a fresh macro snapshot. Optionally persist to DB."""
    m = MacroData(
        vix=_quote(VIX_SYM),
        dxy=_quote(DXY_SYM),
        tlt=_quote(TLT_SYM),
        oil=_quote(OIL_SYM),
        put_call_ratio=_spy_put_call_ratio(),
    )
    m.regime, m.notes = _classify_regime(m)

    if persist:
        try:
            with session_scope() as s:
                s.add(MacroSnapshot(
                    vix=m.vix, dxy=m.dxy, tlt=m.tlt, oil=m.oil,
                    put_call_ratio=m.put_call_ratio, regime=m.regime,
                ))
        except Exception as exc:
            logger.warning("Persist macro snapshot failed: %s", exc)
    return m


def snapshot_dict() -> dict:
    d = asdict(snapshot(persist=False))
    return d


def snapshot_json() -> str:
    return json.dumps(asdict(snapshot(persist=False)), default=str)
