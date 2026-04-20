"""SQLAlchemy ORM models."""
from __future__ import annotations

from datetime import datetime, date

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Date, Text,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Portfolio(Base):
    __tablename__ = "portfolios"
    id               = Column(Integer, primary_key=True)
    name             = Column(String(16), unique=True, nullable=False)  # agent | user
    starting_capital = Column(Float, nullable=False)
    cash             = Column(Float, nullable=False)
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)

    positions = relationship("Position", back_populates="portfolio", cascade="all, delete-orphan")
    trades    = relationship("Trade",    back_populates="portfolio", cascade="all, delete-orphan")


class Position(Base):
    __tablename__ = "positions"
    id              = Column(Integer, primary_key=True)
    portfolio_id    = Column(Integer, ForeignKey("portfolios.id"), nullable=False)
    symbol          = Column(String(16), nullable=False, index=True)
    quantity        = Column(Float, nullable=False)
    avg_entry_price = Column(Float, nullable=False)
    opened_at       = Column(DateTime, default=datetime.utcnow, nullable=False)
    stop_loss       = Column(Float, nullable=True)
    take_profit     = Column(Float, nullable=True)
    thesis          = Column(Text, nullable=True)

    portfolio = relationship("Portfolio", back_populates="positions")

    __table_args__ = (UniqueConstraint("portfolio_id", "symbol", name="uq_portfolio_symbol"),)


class Trade(Base):
    __tablename__ = "trades"
    id            = Column(Integer, primary_key=True)
    portfolio_id  = Column(Integer, ForeignKey("portfolios.id"), nullable=False)
    symbol        = Column(String(16), nullable=False, index=True)
    side          = Column(String(4), nullable=False)   # buy | sell
    quantity      = Column(Float, nullable=False)
    price         = Column(Float, nullable=False)
    executed_at   = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    reason        = Column(Text, nullable=True)
    realized_pnl  = Column(Float, nullable=True)

    portfolio = relationship("Portfolio", back_populates="trades")


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    id              = Column(Integer, primary_key=True)
    portfolio_id    = Column(Integer, ForeignKey("portfolios.id"), nullable=False)
    timestamp       = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    cash            = Column(Float, nullable=False)
    positions_value = Column(Float, nullable=False)
    equity          = Column(Float, nullable=False)

    __table_args__ = (Index("ix_equity_portfolio_ts", "portfolio_id", "timestamp"),)


class Pick(Base):
    __tablename__ = "picks"
    id             = Column(Integer, primary_key=True)
    week_of        = Column(Date, nullable=False, index=True)   # Monday of week
    symbol         = Column(String(16), nullable=False)
    rank           = Column(Integer, nullable=False)
    score          = Column(Float, nullable=False)
    thesis         = Column(Text, nullable=True)
    metric         = Column(String(32), nullable=True)
    zscore         = Column(Float, nullable=True)
    price_at_pick  = Column(Float, nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow, nullable=False)


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    id                  = Column(Integer, primary_key=True)
    date                = Column(Date, unique=True, nullable=False, index=True)
    summary_markdown    = Column(Text, nullable=False)
    macro_snapshot_json = Column(Text, nullable=True)
    regime              = Column(String(16), nullable=True)
    trade_count         = Column(Integer, default=0)
    pnl_day             = Column(Float, default=0.0)
    created_at          = Column(DateTime, default=datetime.utcnow, nullable=False)


class MacroSnapshot(Base):
    __tablename__ = "macro_snapshots"
    id              = Column(Integer, primary_key=True)
    timestamp       = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    vix             = Column(Float, nullable=True)
    dxy             = Column(Float, nullable=True)
    tlt             = Column(Float, nullable=True)
    oil             = Column(Float, nullable=True)
    put_call_ratio  = Column(Float, nullable=True)
    regime          = Column(String(16), nullable=True)


class AgentLog(Base):
    __tablename__ = "agent_logs"
    id         = Column(Integer, primary_key=True)
    timestamp  = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    level      = Column(String(8), default="info")
    category   = Column(String(32), nullable=False)   # intraday | pick | summary | order | macro
    message    = Column(Text, nullable=False)
    data_json  = Column(Text, nullable=True)
