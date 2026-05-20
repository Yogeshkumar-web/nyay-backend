from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
import app.db.registry  # noqa: F401 - load SQLAlchemy relationship targets in workers

celery_app = Celery(
    "vakilsuite",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.workers.ocr_tasks",
        "app.workers.extraction_tasks",
        "app.workers.typing_tasks",
        "app.workers.export_tasks",
        "app.workers.scraper_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
    # Task result expiry — 24 hours
    result_expires=86400,
    # Retry policy defaults
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # ── Beat schedule ──────────────────────────────────────────────────────
    # Allahabad HC publishes cause lists by ~7 AM IST on working days.
    # We scrape at 7:30 AM IST Mon–Sat so lawyers see it when they open the app.
    # Timezone = Asia/Kolkata (set above via timezone setting).
    beat_schedule={
        "scrape-daily-cause-list": {
            "task": "app.workers.scraper_tasks.scrape_daily_cause_list",
            "schedule": crontab(hour=7, minute=30, day_of_week="mon-sat"),
            "options": {"queue": "default"},
        },
    },
)
