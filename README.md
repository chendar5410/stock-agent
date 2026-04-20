# Stock Agent — Portfolio + Valuation

Two tools in one FastAPI app:

1. **Portfolio Agent** (`/portfolio`) — a fully-autonomous paper-trading agent that
   picks US stocks + crypto ETFs weekly, manages positions intraday with macro
   overlay (VIX, DXY, TLT, oil, put/call), and writes a daily narrative summary.
   Tracks two parallel $100k portfolios (agent vs you) so you can compare.
2. **ValuationChart** (`/`) — the original Koyfin-style historical valuation tool.

---

## Quick Start — Portfolio Agent

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in keys (optional — see below)
uvicorn main:app --port 8000
```

Open **http://localhost:8000/portfolio**.

### Configuration (`.env`)

| Variable             | Purpose | Required? |
|----------------------|---------|-----------|
| `ALPACA_API_KEY` / `ALPACA_API_SECRET` | Real-time IEX quotes + paper trading | Recommended — sign up free at alpaca.markets |
| `ANTHROPIC_API_KEY`  | Claude-generated intraday decisions + Hebrew daily summaries | Optional — falls back to rules |
| `FMP_API_KEY`        | Extended fundamentals history for the picker | Optional |
| `STARTING_CAPITAL`   | Paper portfolio size (default 100000) | No |
| `MAX_POSITION_PCT` / `MAX_POSITIONS` / `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT` | Risk limits | No |

**Zero-config mode:** with no API keys the system still runs. It uses `yfinance`
quotes (not tick-level), the broker simulates fills locally, and the "reasoning"
layer falls back to deterministic rules. Fine for paper-only exploration.

### What runs when

| When | Job |
|------|-----|
| Every 1 min, Mon–Fri 09:30–16:00 ET | Equity snapshot (both portfolios) |
| Every 5 min, Mon–Fri 09:35–15:55 ET | Trader tick — macro refresh, risk checks, LLM decision, orders |
| 16:30 ET, weekdays | Daily summary (persisted to `data/portfolio.db`) |
| 20:00 ET, Sunday | Weekly stock picker — 5 stocks + 2 crypto ETFs |

Everything is persisted in SQLite (`./data/portfolio.db`) so closing + reopening
the server preserves the portfolio.

### Control surface

The dashboard has buttons for **Run tick**, **Run picker**, **Generate summary**
— useful to bootstrap on day 1 instead of waiting for cron. There is also a
small form to log your own trades to the "you" portfolio for comparison.

### Endpoints

- `GET  /api/portfolio/status` — broker, market-open, limits
- `GET  /api/portfolio/compare` — side-by-side state (agent vs user)
- `GET  /api/portfolio/state/{agent|user}` — single portfolio state
- `GET  /api/portfolio/equity/{name}?days=365` — equity curve
- `GET  /api/portfolio/trades/{name}?limit=50` — recent trades
- `GET  /api/portfolio/picks?weeks=1` — weekly picks
- `GET  /api/portfolio/macro` — live macro snapshot
- `GET  /api/portfolio/summaries?limit=14` — daily summaries
- `POST /api/portfolio/run/{tick|picker|summary|equity}` — trigger job now
- `POST /api/portfolio/user/trade` — log a manual trade on the user portfolio

---

## ValuationChart — Historical Valuation Analysis Tool

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
