"""News deck API endpoints — generate, list, fetch, delete daily briefs."""
from __future__ import annotations

import asyncio
import logging
from datetime import date as date_cls

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.deck_store import (
    delete_deck,
    list_decks,
    load_deck,
    save_deck,
    validate_date,
)
from app.services.news_deck import build_deck
from app.services.watchlist import get_watchlist

router = APIRouter(prefix="/api/news", tags=["news"])
logger = logging.getLogger(__name__)


class GenerateBody(BaseModel):
    raw_text: str = Field(..., min_length=1, description="Pasted X / morning summary")
    date: str | None = Field(default=None, description="YYYY-MM-DD; defaults to today")
    overwrite: bool = Field(default=False)


@router.post("/generate")
async def generate(body: GenerateBody):
    """Generate (and persist) a structured deck from raw morning summary text."""
    try:
        date_str = validate_date(body.date or date_cls.today().isoformat())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not body.overwrite and load_deck(date_str) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A deck for {date_str} already exists. Pass overwrite=true to replace it.",
        )

    watchlist = get_watchlist()

    loop = asyncio.get_running_loop()
    try:
        deck = await loop.run_in_executor(
            None, build_deck, body.raw_text, date_str, watchlist
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected deck-generation error")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    save_deck(deck)
    return deck


@router.get("/decks")
async def decks_index():
    return {"decks": list_decks()}


@router.get("/decks/{date_str}")
async def deck_get(date_str: str):
    try:
        date_str = validate_date(date_str)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    deck = load_deck(date_str)
    if deck is None:
        raise HTTPException(status_code=404, detail=f"No deck for {date_str}.")
    return deck


@router.delete("/decks/{date_str}")
async def deck_delete(date_str: str):
    try:
        date_str = validate_date(date_str)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    deleted = delete_deck(date_str)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No deck for {date_str}.")
    return {"deleted": date_str}
