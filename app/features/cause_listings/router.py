"""
Cause Listings router
─────────────────────
GET  /cause-listings            — paginated listings for a date (reads DB)
GET  /cause-listings/my-cases   — listings matching the caller's active cases
POST /cause-listings/scrape     — any user: queue a background scrape for a date
POST /cause-listings/refresh    — admin only: same as /scrape (kept for admin panel)
"""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Query

from app.core.dependencies import CurrentUser, DB
from app.core.exceptions import ForbiddenError
from app.features.cause_listings.schemas import (
    CauseListingResponse,
    RefreshRequest,
    ScrapeRequest,
)
from app.features.cause_listings.service import CauseListingService
from app.features.users.models import UserRole
from app.workers.scraper_tasks import scrape_daily_cause_list

router = APIRouter(tags=["Cause Listings"])
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# POST /cause-listings/scrape   (any authenticated user)
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/cause-listings/scrape",
    summary="Trigger cause-list scrape for a date (queued background job)",
)
async def trigger_scrape(
    body: ScrapeRequest,
    current_user: CurrentUser,
    db: DB,
):
    """
    Queues a Celery task that navigates the AHC website, downloads the PDF,
    parses it, matches cases, and sends notifications.

    Returns a job_id the frontend can poll via GET /api/v1/jobs/{job_id}.
    Expected completion time: ~40-60 seconds.
    """
    target_date = body.date if body.date else date.today()
    task = scrape_daily_cause_list.delay(target_date.isoformat())

    logger.info(
        "cause_listing_scrape_queued",
        extra={
            "user_id": str(current_user.id),
            "date": str(target_date),
            "job_id": task.id,
        },
    )

    return {
        "success": True,
        "data": {
            "job_id": task.id,
            "date": target_date,
            "message": (
                f"Scraping cause list for {target_date.strftime('%d %b %Y')}. "
                "This takes ~40 seconds. Poll /jobs/{job_id} for status."
            ),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /cause-listings
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/cause-listings", summary="Get cause list for a date")
async def get_cause_listings(
    current_user: CurrentUser,
    db: DB,
    target_date: Optional[date] = Query(None, alias="date"),
    court_number: Optional[str] = Query(None, max_length=20),
    search: Optional[str] = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    if not target_date:
        target_date = date.today()

    service = CauseListingService(db)
    listings = await service.get_listings_for_date(
        target_date,
        court_number,
        search,
        limit=limit,
        offset=offset,
    )

    logger.info(
        "cause_listings_fetched",
        extra={
            "user_id": str(current_user.id),
            "date": str(target_date),
            "count": len(listings),
        },
    )

    return {
        "success": True,
        "data": {
            "listings": [
                CauseListingResponse.model_validate(listing).model_dump()
                for listing in listings
            ],
            "date": target_date,
            "limit": limit,
            "offset": offset,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /cause-listings/my-cases
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/cause-listings/my-cases",
    summary="Get listings matching the logged-in lawyer's active cases",
)
async def get_my_matches(
    current_user: CurrentUser,
    db: DB,
    target_date: Optional[date] = Query(None, alias="date"),
):
    if not target_date:
        target_date = date.today()

    service = CauseListingService(db)
    matches = await service.get_my_matches(target_date, current_user)

    logger.info(
        "cause_listings_my_matches",
        extra={
            "user_id": str(current_user.id),
            "date": str(target_date),
            "count": len(matches),
        },
    )

    return {
        "success": True,
        "data": {
            "matches": [
                CauseListingResponse.model_validate(m).model_dump() for m in matches
            ],
            "date": target_date,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /cause-listings/refresh  (admin only — kept for admin panel)
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/cause-listings/refresh",
    summary="Admin: trigger manual cause-list scrape",
)
async def refresh_cause_list(
    body: RefreshRequest,
    current_user: CurrentUser,
    db: DB,
):
    if current_user.role != UserRole.admin:
        raise ForbiddenError("Admin access required")

    target_date = body.date if body.date else date.today()
    task = scrape_daily_cause_list.delay(target_date.isoformat())

    logger.warning(
        "cause_listing_manual_refresh_triggered",
        extra={
            "user_id": str(current_user.id),
            "date": str(target_date),
            "job_id": task.id,
        },
    )

    return {
        "success": True,
        "data": {
            "job_id": task.id,
            "date": target_date,
            "message": f"Scrape queued for {target_date}",
        },
    }
