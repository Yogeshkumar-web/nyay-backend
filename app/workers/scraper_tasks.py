"""
Cause-list scraper Celery task.

Scheduling (beat):
  Weekdays 7:30 AM IST — auto-triggered by celery beat
  On-demand — triggered by POST /cause-listings/refresh (admin only)

Retry policy:
  max_retries=3, countdown doubles each retry (5 min → 10 min → 20 min)
"""

import asyncio
import logging
from datetime import date
from typing import Optional

from celery import Task

from app.db.session import AsyncSessionLocal
from app.features.cause_listings.service import CauseListingService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Async runner — always creates a fresh event loop so we never clash with
# an existing loop (happens on some Celery pool modes).
# ─────────────────────────────────────────────────────────────────────────────


async def _run_scrape(target_date: date) -> dict:
    async with AsyncSessionLocal() as db:
        service = CauseListingService(db)
        return await service.process_daily_scrape(target_date)


def _run_sync(target_date: date) -> dict:
    """Run the async scraper in a brand-new event loop (safe in all Celery pools)."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_run_scrape(target_date))
    finally:
        loop.close()
        asyncio.set_event_loop(None)


# ─────────────────────────────────────────────────────────────────────────────
# Celery task
# ─────────────────────────────────────────────────────────────────────────────


@celery_app.task(
    name="app.workers.scraper_tasks.scrape_daily_cause_list",
    bind=True,
    max_retries=3,
    # Retry back-off: attempt 1 → 300 s, 2 → 600 s, 3 → 1200 s
    default_retry_delay=300,
    # Acknowledge only after the task finishes (prevents silent drops)
    acks_late=True,
)
def scrape_daily_cause_list(self: Task, target_date_str: Optional[str] = None) -> dict:
    """
    Scrape the Allahabad HC cause list for the given date (ISO format) or today.

    Args:
        target_date_str: "YYYY-MM-DD" string, or None for today.

    Returns:
        {"status": "completed", "date": "YYYY-MM-DD", "stats": {...}}
    """
    if target_date_str:
        try:
            target_date = date.fromisoformat(target_date_str)
        except ValueError:
            logger.error("scraper_task_bad_date", extra={"raw": target_date_str})
            return {"status": "error", "reason": "invalid date format"}
    else:
        target_date = date.today()

    logger.info(
        "scraper_task_start",
        extra={"date": str(target_date), "attempt": self.request.retries + 1},
    )

    try:
        stats = _run_sync(target_date)

        logger.info(
            "scraper_task_complete",
            extra={"date": str(target_date), **stats},
        )
        return {
            "status": "completed",
            "date": target_date.isoformat(),
            "stats": stats,
        }

    except Exception as exc:
        logger.exception(
            "scraper_task_failed",
            extra={
                "date": str(target_date),
                "attempt": self.request.retries + 1,
                "error": str(exc),
            },
        )
        # Exponential back-off: 300 s * (2 ** retries)
        countdown = 300 * (2**self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)
