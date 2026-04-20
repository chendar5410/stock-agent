"""SQLite engine + session factory."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)

SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)


def engine():
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context-managed DB session — commits on success, rolls back on error."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db() -> None:
    """Create tables + seed portfolios if missing. Safe to call repeatedly."""
    from app.models import Base, Portfolio
    Base.metadata.create_all(_engine)
    with session_scope() as s:
        for name in ("agent", "user"):
            exists = s.query(Portfolio).filter_by(name=name).first()
            if not exists:
                s.add(Portfolio(
                    name=name,
                    starting_capital=settings.starting_capital,
                    cash=settings.starting_capital,
                ))
