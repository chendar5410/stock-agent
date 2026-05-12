"""
Turn a raw pre-market news summary (e.g. pasted from X) into a structured
slide deck via the Claude API.

The deck JSON shape produced here is the same shape consumed by the
frontend slideshow renderer in `static/js/news.js`.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("NEWS_DECK_MODEL", "claude-sonnet-4-6")
MAX_RAW_CHARS = 16_000


SYSTEM_PROMPT = """\
You are a senior equities desk analyst writing a pre-market briefing for a
learning trader. The user pastes a raw pre-market news summary (often from
an X / Twitter thread, sometimes messy, with emojis, links, line breaks).

Your job: transform it into a clean, didactic, structured slide deck.

Style rules:
- Keep slide bullets short (max ~16 words each). Slides are read at a glance.
- Plain English, no hedging filler. Numbers matter — preserve % moves, $ levels,
  earnings beats/misses, guidance changes, macro prints.
- "why_it_matters" is the learning layer: explain the market mechanism or
  second-order effect in one sentence (e.g. "Rising yields compress long-
  duration tech multiples"). Do NOT just restate the headline.
- Glossary: pick 4-8 jargon / acronyms a beginner-to-intermediate trader
  might stumble on (CPI, hawkish, guidance cut, multiple compression,
  ATH, etc.). Define plainly.
- If the source text is sparse, still produce a deck — leave arrays empty
  rather than inventing news.
- Tickers: ALWAYS uppercase, no $ prefix.

Output: STRICT JSON only, matching this schema. No prose before/after.

{
  "title": "string — headline for the day, e.g. 'Pre-Market Brief — May 12'",
  "overview": "string — 2-3 sentence TL;DR of the morning's tone",
  "market_tone": "risk-on" | "risk-off" | "mixed" | "cautious",
  "key_levels": [
    {"label": "S&P 500 futures", "value": "+0.4%", "note": "optional context"}
  ],
  "macro": [
    {"headline": "...", "detail": "...", "why_it_matters": "..."}
  ],
  "earnings": [
    {"ticker": "AAPL", "headline": "...", "detail": "...", "why_it_matters": "..."}
  ],
  "movers": [
    {"ticker": "...", "direction": "up" | "down", "change": "+5.2%",
     "headline": "...", "why_it_matters": "..."}
  ],
  "sectors": [
    {"name": "Semis", "direction": "up" | "down" | "mixed", "note": "..."}
  ],
  "watch_today": [
    "string — e.g. '10:00 ET — JOLTS print', 'NVDA earnings AMC'"
  ],
  "glossary": [
    {"term": "CPI", "definition": "Consumer Price Index — monthly inflation gauge."}
  ]
}
"""


USER_TEMPLATE = """\
Date: {date}
{watchlist_block}

Raw morning summary (verbatim — may contain emojis, links, bad formatting):
---
{raw_text}
---

Produce the structured deck JSON now.\
"""


def _watchlist_block(watchlist: list[str]) -> str:
    if not watchlist:
        return "My watchlist: (none provided)"
    return (
        "My watchlist (flag any of these tickers prominently if mentioned): "
        + ", ".join(watchlist)
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first {...} block out of the model response and parse it."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model response did not contain JSON object")
    return json.loads(text[start : end + 1])


def _normalize(deck: dict[str, Any], date: str, raw_text: str,
               watchlist: list[str]) -> dict[str, Any]:
    """Fill in missing fields and compute the watchlist tie-in cross-reference."""
    out: dict[str, Any] = {
        "date": date,
        "title": deck.get("title") or f"Pre-Market Brief — {date}",
        "overview": deck.get("overview", ""),
        "market_tone": deck.get("market_tone", "mixed"),
        "key_levels": deck.get("key_levels", []) or [],
        "macro": deck.get("macro", []) or [],
        "earnings": deck.get("earnings", []) or [],
        "movers": deck.get("movers", []) or [],
        "sectors": deck.get("sectors", []) or [],
        "watch_today": deck.get("watch_today", []) or [],
        "glossary": deck.get("glossary", []) or [],
        "raw_summary": raw_text,
        "watchlist": watchlist,
    }

    wl_set = {t.upper() for t in watchlist}
    hits: list[dict[str, str]] = []
    for item in out["earnings"]:
        t = (item.get("ticker") or "").upper()
        if t in wl_set:
            hits.append({"ticker": t, "section": "earnings",
                         "headline": item.get("headline", "")})
    for item in out["movers"]:
        t = (item.get("ticker") or "").upper()
        if t in wl_set:
            hits.append({"ticker": t, "section": "movers",
                         "headline": item.get("headline", "")})
    out["watchlist_hits"] = hits
    return out


def build_deck(raw_text: str, date: str, watchlist: list[str],
               model: str | None = None) -> dict[str, Any]:
    """
    Call Claude to convert a raw news summary into a structured deck.

    Raises:
        RuntimeError: if ANTHROPIC_API_KEY is missing or the SDK is not
            installed or the API call fails.
        ValueError: if the raw text is empty or the response is not JSON.
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise ValueError("Raw summary text is empty.")
    if len(raw_text) > MAX_RAW_CHARS:
        raw_text = raw_text[:MAX_RAW_CHARS]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it before generating a deck."
        )

    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The `anthropic` package is not installed. Run "
            "`pip install -r requirements.txt`."
        ) from exc

    client = Anthropic(api_key=api_key)
    user_msg = USER_TEMPLATE.format(
        date=date,
        watchlist_block=_watchlist_block(watchlist),
        raw_text=raw_text,
    )

    try:
        resp = client.messages.create(
            model=model or DEFAULT_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:
        logger.exception("Claude API call failed")
        raise RuntimeError(f"Claude API call failed: {exc}") from exc

    text = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    )
    if not text.strip():
        raise ValueError("Claude returned an empty response.")

    try:
        parsed = _extract_json(text)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse model JSON. Raw response:\n%s", text)
        raise ValueError(f"Could not parse JSON from Claude response: {exc}") from exc

    return _normalize(parsed, date, raw_text, watchlist)
