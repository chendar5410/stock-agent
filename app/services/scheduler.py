"""APScheduler wiring. Starts on FastAPI startup, stops on shutdown.

Schedules (timezone = settings.timezone, default America/New_York):
  - Every 5 min, Mon-Fri 09:35-15:55 → trader.tick()
  - Every 1 min, Mon-Fri 09:30-16:00 → equity snapshot
  - Weekdays 16:30 → trader.generate_daily_summary()
  - Sunday 20:00    → picker.run_picker()
"""
from __future__ import annotations

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.db import session_scope
from app.models import AgentLog
from app.services import picker, portfolio_service, trader

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


def _safe(fn, label: str):
    """Wrap a job with logging + exception shielding."""
    def _wrap():
        try:
            logger.info("[JOB:%s] start", label)
            fn()
            logger.info("[JOB:%s] done", label)
        except Exception as exc:
            logger.exception("[JOB:%s] error: %s", label, exc)
            try:
                with session_scope() as s:
                    s.add(AgentLog(level="error", category="job",
                                   message=f"{label} failed: {exc}"))
            except Exception:
                pass
    return _wrap


def _tick_job():
    trader.tick()


def _equity_job():
    portfolio_service.snapshot_both()


def _summary_job():
    trader.generate_daily_summary()


def _picker_job():
    picker.run_picker()


def start() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        sched = BackgroundScheduler(timezone=settings.timezone)

        # Trader tick — every 5 minutes during market hours
        sched.add_job(
            _safe(_tick_job, "tick"),
            CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5",
                        timezone=settings.timezone),
            id="trader_tick", max_instances=1, coalesce=True,
        )

        # Equity snapshot — every minute during market hours
        sched.add_job(
            _safe(_equity_job, "equity"),
            CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*",
                        timezone=settings.timezone),
            id="equity_tick", max_instances=1, coalesce=True,
        )

        # Daily summary — 16:30 ET, weekdays
        sched.add_job(
            _safe(_summary_job, "daily_summary"),
            CronTrigger(day_of_week="mon-fri", hour=16, minute=30,
                        timezone=settings.timezone),
            id="daily_summary", max_instances=1, coalesce=True,
        )

        # Weekly picker — Sunday 20:00 ET (well before Monday open)
        sched.add_job(
            _safe(_picker_job, "weekly_picks"),
            CronTrigger(day_of_week="sun", hour=20, minute=0,
                        timezone=settings.timezone),
            id="weekly_picks", max_instances=1, coalesce=True,
        )

        sched.start()
        _scheduler = sched
        logger.info("Scheduler started (timezone=%s) with jobs: %s",
                    settings.timezone, [j.id for j in sched.get_jobs()])


def shutdown() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None


def run_now(job: str) -> dict:
    """Trigger a job manually (for UI/debug buttons)."""
    if job == "tick":
        return trader.tick() or {}
    if job == "picker":
        return {"picks": picker.run_picker()}
    if job == "summary":
        return {"summary": trader.generate_daily_summary()}
    if job == "equity":
        return portfolio_service.snapshot_both()
    raise ValueError(f"unknown job '{job}'")
