"""Central configuration — reads from environment (.env is auto-loaded)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Alpaca
    alpaca_api_key:    str = _env("ALPACA_API_KEY")
    alpaca_api_secret: str = _env("ALPACA_API_SECRET")
    alpaca_base_url:   str = _env("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    alpaca_data_feed:  str = _env("ALPACA_DATA_FEED", "iex")

    # Claude
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY")
    anthropic_model:   str = _env("ANTHROPIC_MODEL", "claude-opus-4-7")

    # Portfolio
    starting_capital:  float = _env_float("STARTING_CAPITAL", 100_000.0)
    max_position_pct:  float = _env_float("MAX_POSITION_PCT", 10.0)
    max_positions:     int   = _env_int("MAX_POSITIONS", 12)
    stop_loss_pct:     float = _env_float("STOP_LOSS_PCT", 5.0)
    take_profit_pct:   float = _env_float("TAKE_PROFIT_PCT", 15.0)
    vix_defensive:     float = _env_float("VIX_DEFENSIVE", 25.0)
    vix_risk_off:      float = _env_float("VIX_RISK_OFF", 32.0)

    # System
    data_dir:          str = _env("DATA_DIR", "./data")
    timezone:          str = _env("TIMEZONE", "America/New_York")

    @property
    def use_alpaca(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_api_secret)

    @property
    def use_claude(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def db_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p / "portfolio.db"


settings = Settings()
