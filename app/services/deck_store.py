"""File-backed store for daily pre-market news decks.

Each deck lives in `decks/YYYY-MM-DD.json` so the calendar view can
list them and the user can revisit past mornings.
"""
from __future__ import annotations

import json
import re
from datetime import date as date_cls
from pathlib import Path
from typing import Any

DECKS_DIR = Path(__file__).parent.parent.parent / "decks"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _ensure_dir() -> None:
    DECKS_DIR.mkdir(parents=True, exist_ok=True)


def validate_date(date_str: str) -> str:
    """Return a canonical YYYY-MM-DD string or raise ValueError."""
    if not date_str:
        return date_cls.today().isoformat()
    if not DATE_RE.match(date_str):
        raise ValueError(f"Invalid date '{date_str}'. Expected YYYY-MM-DD.")
    try:
        date_cls.fromisoformat(date_str)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{date_str}': {exc}") from exc
    return date_str


def save_deck(deck: dict[str, Any]) -> Path:
    _ensure_dir()
    date_str = validate_date(deck.get("date", ""))
    deck["date"] = date_str
    path = DECKS_DIR / f"{date_str}.json"
    path.write_text(json.dumps(deck, ensure_ascii=False, indent=2))
    return path


def load_deck(date_str: str) -> dict[str, Any] | None:
    date_str = validate_date(date_str)
    path = DECKS_DIR / f"{date_str}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def delete_deck(date_str: str) -> bool:
    date_str = validate_date(date_str)
    path = DECKS_DIR / f"{date_str}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def list_decks() -> list[dict[str, Any]]:
    """Return decks newest-first with light metadata (no raw_summary payload)."""
    _ensure_dir()
    out: list[dict[str, Any]] = []
    for path in DECKS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        out.append(
            {
                "date": data.get("date") or path.stem,
                "title": data.get("title", ""),
                "market_tone": data.get("market_tone", ""),
                "watchlist_hits": len(data.get("watchlist_hits", []) or []),
                "movers": len(data.get("movers", []) or []),
                "earnings": len(data.get("earnings", []) or []),
            }
        )
    out.sort(key=lambda d: d["date"], reverse=True)
    return out
