# ValuationChart — Historical Valuation Analysis Tool

A Koyfin-style historical valuation chart tool built with **FastAPI + Plotly**.

## Features

### Single Chart View
- Type any ticker and instantly see its valuation history
- Overlays **mean**, **±1σ**, and **±2σ** bands as dashed horizontal lines
- Right-side labels for each band
- Color-coded current valuation marker (green = cheap, red = expensive)
- Z-score gauge bar showing where the stock sits in its own history
- Natural-language summary: _"AAPL is trading 1.6 standard deviations below its 5-year average Forward P/E…"_

### Compare Mode
- Enter up to 6 tickers (comma-separated)
- Stacked subplots — one per ticker — with identical band overlays
- Summary table ranked by z-score (cheapest → most expensive)

### Watchlist Mode
- Add/remove tickers to a persistent watchlist (saved to `watchlist.json`)
- Click **Refresh** to rank all watchlist tickers by z-score from cheapest to most expensive
- Configurable metric and time period per-refresh

### Metrics
| Key | Label |
|-----|-------|
| `forward_pe` | Forward P/E (default) |
| `trailing_pe` | Trailing P/E |
| `ev_ebitda` | EV/EBITDA |
| `price_sales` | Price/Sales |

### Time Ranges
1Y · 2Y · 3Y · **5Y** · 10Y

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run (live market data via yfinance)
uvicorn app.main:app --reload --port 8000

# Run in demo mode (offline / sandbox — uses synthetic data)
DEMO_MODE=1 uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** in your browser.

---

## Project Structure

```
stock-agent/
├── app/
│   ├── main.py                  # FastAPI app + routing
│   ├── routers/
│   │   ├── valuation.py         # /api/valuation/* endpoints
│   │   └── watchlist_router.py  # /api/watchlist/* endpoints
│   └── services/
│       ├── data_fetcher.py      # yfinance data + analytics (live + fallback)
│       ├── chart_builder.py     # Plotly chart construction
│       ├── demo_data.py         # Realistic synthetic data (offline mode)
│       └── watchlist.py         # File-backed watchlist CRUD
├── static/
│   ├── css/style.css            # Dark professional theme
│   └── js/app.js                # Single-page frontend logic
├── templates/
│   └── index.html               # Jinja2 HTML template
├── main.py                      # Entrypoint (python main.py)
└── requirements.txt
```

---

## API Reference

### `GET /api/valuation/chart`
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `ticker` | string | required | Stock symbol |
| `metric` | string | `forward_pe` | `forward_pe` \| `trailing_pe` \| `ev_ebitda` \| `price_sales` |
| `period` | string | `5y` | `1y` \| `2y` \| `3y` \| `5y` \| `10y` |

Returns chart JSON + zscore + summary + stats.

### `GET /api/valuation/compare`
| Param | Type | Description |
|-------|------|-------------|
| `tickers` | string | Comma-separated list (max 6) |
| `metric` | string | Same as above |
| `period` | string | Same as above |

### `GET /api/watchlist/ranked`
Returns watchlist sorted by z-score ascending (cheapest first).

### `POST /api/watchlist/add` · `DELETE /api/watchlist/remove/{ticker}`
Add / remove tickers from the persistent watchlist.

---

## Connecting Real Financial APIs

Replace the functions in `app/services/data_fetcher.py`:

- `_forward_eps_series()` → connect to a paid data provider (Refinitiv, Bloomberg, FactSet) for analyst consensus forward EPS history
- `_ev_ebitda_series()` / `_price_sales_series()` → use quarterly filing data for more accurate historical series

The rest of the app (chart building, z-score analytics, UI) is fully data-source agnostic.
