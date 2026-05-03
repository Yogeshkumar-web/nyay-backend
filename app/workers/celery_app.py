from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "vakilsuite",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
    "app.workers.ocr_tasks",
    "app.workers.extraction_tasks",
    "app.workers.typing_tasks",
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
    # Beat schedule (Sprint 6: cause list scraper)
    beat_schedule={},
)