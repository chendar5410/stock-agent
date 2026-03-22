"""
Realistic demo/fallback valuation data.

Used when yfinance cannot reach the internet (e.g. in sandboxes or CI).
The generator seeds on the ticker name so data is deterministic and
consistent across calls.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np

# Pre-set profiles: (mean_pe, std_pe, current_drift)
# drift is how many std-devs above/below mean the current price is
_PROFILES: dict[str, tuple[float, float, float]] = {
    "AAPL":  (26.0, 6.0,  -0.4),
    "MSFT":  (31.0, 7.0,   0.6),
    "GOOGL": (22.0, 5.0,  -1.2),
    "AMZN":  (55.0, 18.0,  0.3),
    "NVDA":  (40.0, 20.0,  2.1),
    "META":  (20.0, 7.0,  -0.8),
    "TSLA":  (70.0, 35.0, -1.5),
    "NFLX":  (33.0, 10.0,  0.9),
    "JPM":   (12.0, 2.5,  -0.3),
    "WMT":   (24.0, 4.0,   0.2),
}

# EV/EBITDA profiles
_EV_PROFILES: dict[str, tuple[float, float, float]] = {
    "AAPL":  (20.0, 4.0,  -0.5),
    "MSFT":  (25.0, 6.0,   0.4),
    "GOOGL": (14.0, 4.0,  -1.0),
    "AMZN":  (30.0, 10.0,  0.2),
    "NVDA":  (35.0, 15.0,  2.0),
    "META":  (12.0, 4.0,  -0.7),
    "TSLA":  (60.0, 25.0, -1.4),
    "JPM":   (10.0, 2.0,  -0.2),
}

# P/S profiles
_PS_PROFILES: dict[str, tuple[float, float, float]] = {
    "AAPL":  (7.0,  1.5,  -0.3),
    "MSFT":  (12.0, 3.0,   0.5),
    "GOOGL": (5.5,  1.5,  -1.1),
    "AMZN":  (3.0,  0.8,   0.3),
    "NVDA":  (20.0, 10.0,  1.8),
    "META":  (5.0,  2.0,  -0.9),
    "TSLA":  (10.0, 4.0,  -1.3),
    "NFLX":  (5.0,  1.5,   0.7),
}


def _seed_for(symbol: str) -> int:
    return int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)


def generate_series(
    symbol: str,
    metric: str,
    days: int = 1825,
) -> tuple[list[str], list[float]]:
    """
    Generate a plausible mean-reverting valuation series.
    Returns (dates, values).
    """
    sym = symbol.upper()
    rng = np.random.default_rng(_seed_for(sym))

    # Pick profile
    if metric in ("forward_pe", "trailing_pe"):
        profiles = _PROFILES
    elif metric == "ev_ebitda":
        profiles = _EV_PROFILES
    else:
        profiles = _PS_PROFILES

    if sym in profiles:
        mean_val, std_val, end_drift = profiles[sym]
    else:
        # Generate plausible defaults seeded on ticker hash
        base = (_seed_for(sym) % 20) + 10
        mean_val = float(base)
        std_val  = mean_val * 0.20
        end_drift = rng.uniform(-2.0, 2.0)

    # OU process parameters
    theta = 0.015   # mean-reversion speed
    sigma = std_val * 0.04  # daily noise
    dt    = 1.0

    # Start slightly away from mean
    start_drift = rng.uniform(-1.5, 1.5)
    x = mean_val + start_drift * std_val

    values: list[float] = []
    for _ in range(days):
        dx = theta * (mean_val - x) * dt + sigma * rng.standard_normal() * dt**0.5
        x  = max(x + dx, mean_val * 0.2)  # floor at 20% of mean
        values.append(round(x, 4))

    # Force the last ~60 trading days to drift toward end_drift target
    target = mean_val + end_drift * std_val
    for i in range(1, 61):
        idx = -(61 - i)
        blend = i / 60
        values[idx] = round(values[idx] * (1 - blend * 0.5) + target * blend * 0.5, 4)
    # Snap last value cleanly
    values[-1] = round(target, 4)

    # Build date index (business days only, approximate)
    today = date.today()
    start = today - timedelta(days=int(days * 1.45))  # overshoot to get enough bdays
    all_dates: list[date] = []
    d = start
    while d <= today:
        if d.weekday() < 5:  # Mon–Fri
            all_dates.append(d)
        d += timedelta(days=1)

    # Trim to match len(values)
    if len(all_dates) > len(values):
        all_dates = all_dates[-len(values):]
    elif len(all_dates) < len(values):
        values = values[-len(all_dates):]

    return [str(d) for d in all_dates], values
