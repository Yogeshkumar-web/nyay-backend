"""
CauseListingService
────────────────────────────────────────────────────────────────────────────
Read APIs
  get_listings_for_date      — paginated cause list for a date
  get_my_matches             — listings matched to the caller's cases

Write API
  process_daily_scrape       — called by Celery task (auto daily + user-initiated)
  _process_listings_data     — match → upsert → notify → commit  (shared core)
"""

import logging
import re
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.features.cause_listings.models import CauseListing
from app.features.cause_listings.scraper import scrape_cause_list_for_date
from app.features.cases.models import Case, CaseStatus
from app.features.notifications.models import Notification, NotificationType
from app.features.users.models import User

logger = logging.getLogger(__name__)

_MATCH_THRESHOLD = 85
_NORM_RE = re.compile(r"[\s/\-\.]+")


class CauseListingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ─────────────────────────────────────────────────────────────────────────
    # Read APIs
    # ─────────────────────────────────────────────────────────────────────────

    async def get_listings_for_date(
        self,
        target_date: date,
        court_number: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[CauseListing]:
        query = select(CauseListing).where(CauseListing.listing_date == target_date)

        if court_number:
            query = query.where(CauseListing.court_number.ilike(f"%{court_number}%"))

        if search:
            query = query.where(
                (CauseListing.case_number.ilike(f"%{search}%"))
                | (CauseListing.case_title.ilike(f"%{search}%"))
                | (CauseListing.advocate_name.ilike(f"%{search}%"))
            )

        query = (
            query.order_by(CauseListing.court_number, CauseListing.serial_number)
            .limit(limit)
            .offset(offset)
        )
        return list((await self.db.execute(query)).scalars())

    async def get_my_matches(self, target_date: date, user: User) -> List[CauseListing]:
        query = (
            select(CauseListing)
            .join(Case, CauseListing.matched_case_id == Case.id)
            .where(
                CauseListing.listing_date == target_date,
                Case.lawyer_id == user.id,
            )
            .order_by(CauseListing.court_number, CauseListing.serial_number)
        )
        return list((await self.db.execute(query)).scalars())

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(value: Optional[str]) -> str:
        if not value:
            return ""
        return _NORM_RE.sub("", value.strip().lower())

    @staticmethod
    def _fuzzy_match(
        query: str,
        choices: List[str],
        threshold: int = _MATCH_THRESHOLD,
    ) -> Optional[str]:
        if not query or not choices:
            return None
        result = process.extractOne(query, choices, scorer=fuzz.ratio)
        if result and result[1] >= threshold:
            return result[0]
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Main scrape pipeline
    # ─────────────────────────────────────────────────────────────────────────

    async def process_daily_scrape(self, target_date: date) -> Dict[str, int]:
        """
        Full pipeline: scrape AHC PDF → match active cases → upsert DB → notify.
        Returns stats: {total, inserted, updated, matched}.

        NOTE: This takes ~40 s due to PDF download + parsing.
        Always call from a Celery task, never directly from a FastAPI handler.
        """
        logger.info("scrape_pipeline_start", extra={"date": str(target_date)})

        listings_data = await scrape_cause_list_for_date(target_date)
        if not listings_data:
            logger.warning("scrape_no_data", extra={"date": str(target_date)})
            return {"total": 0, "inserted": 0, "updated": 0, "matched": 0}

        return await self._process_listings_data(target_date, listings_data)

    # ─────────────────────────────────────────────────────────────────────────
    # Shared pipeline core
    # ─────────────────────────────────────────────────────────────────────────

    async def _process_listings_data(
        self, target_date: date, listings_data: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Given parsed row dicts from the scraper:
          1. Load active cases → build normalised case-number map
          2. Fuzzy-match each row
          3. Upsert rows (INSERT new, UPDATE existing keyed by case_number)
          4. Dispatch cause_list_match notifications for NEW matches
          5. Single commit
        """
        if not listings_data:
            return {"total": 0, "inserted": 0, "updated": 0, "matched": 0}

        # ── Load active cases ─────────────────────────────────────────────────
        stmt = (
            select(Case)
            .options(selectinload(Case.case_numbers))
            .where(Case.status == CaseStatus.active)
        )
        active_cases = list((await self.db.execute(stmt)).scalars())

        # ── Build normalised lookup: norm_case_number → (case_id, lawyer_id) ─
        case_map: Dict[str, Tuple[uuid.UUID, uuid.UUID]] = {}
        for case in active_cases:
            for cn in case.case_numbers:
                norm = self._normalize(cn.case_number)
                if norm:
                    case_map[norm] = (case.id, case.lawyer_id)

        known_numbers = list(case_map.keys())

        # ── Preload existing rows for this date (keyed by case_number) ────────
        existing_rows = list(
            (
                await self.db.execute(
                    select(CauseListing).where(CauseListing.listing_date == target_date)
                )
            ).scalars()
        )
        # Primary key: case_number (reliable dedup); fallback: (court, serial)
        existing_by_cn: Dict[str, CauseListing] = {
            row.case_number: row for row in existing_rows if row.case_number
        }
        existing_by_cs: Dict[Tuple, CauseListing] = {
            (row.court_number, row.serial_number): row
            for row in existing_rows
            if row.serial_number is not None
        }

        # ── Process each scraped row ──────────────────────────────────────────
        inserts: List[CauseListing] = []
        new_match_events: List[Tuple[uuid.UUID, CauseListing]] = []
        updates = 0
        matched_total = 0

        for data in listings_data:
            case_num = data.get("case_number")
            court = data.get("court_number")
            serial = data.get("serial_number")

            # Fuzzy match against active cases
            norm_cn = self._normalize(case_num)
            matched_case_id: Optional[uuid.UUID] = None
            matched_lawyer_id: Optional[uuid.UUID] = None

            if norm_cn and known_numbers:
                best = self._fuzzy_match(norm_cn, known_numbers)
                if best:
                    matched_case_id, matched_lawyer_id = case_map[best]
                    matched_total += 1

            # Find existing row: try case_number first, then (court, serial)
            existing: Optional[CauseListing] = None
            if case_num and case_num in existing_by_cn:
                existing = existing_by_cn[case_num]
            elif serial is not None and (court, serial) in existing_by_cs:
                existing = existing_by_cs[(court, serial)]

            if existing is not None:
                prev_match = existing.matched_case_id

                existing.court_number = court
                existing.serial_number = serial
                existing.case_number = case_num
                existing.case_title = data.get("case_title")
                existing.petitioner = data.get("petitioner")
                existing.respondent = data.get("respondent")
                existing.advocate_name = data.get("advocate_name")
                existing.case_type_raw = data.get("case_type_raw")
                existing.remarks = data.get("remarks")
                existing.raw_row_text = data.get("raw_row_text")
                existing.matched_case_id = matched_case_id
                existing.scraped_at = datetime.utcnow()
                updates += 1

                if matched_lawyer_id and prev_match != matched_case_id:
                    new_match_events.append((matched_lawyer_id, existing))

            else:
                listing = CauseListing(
                    listing_date=target_date,
                    court_number=court or "UNKNOWN",
                    serial_number=serial,
                    case_number=case_num,
                    case_title=data.get("case_title"),
                    petitioner=data.get("petitioner"),
                    respondent=data.get("respondent"),
                    advocate_name=data.get("advocate_name"),
                    case_type_raw=data.get("case_type_raw"),
                    remarks=data.get("remarks"),
                    raw_row_text=data.get("raw_row_text"),
                    matched_case_id=matched_case_id,
                )
                inserts.append(listing)
                if matched_lawyer_id:
                    new_match_events.append((matched_lawyer_id, listing))

        # ── Bulk insert ───────────────────────────────────────────────────────
        if inserts:
            self.db.add_all(inserts)
            await self.db.flush()

        # ── Notifications ─────────────────────────────────────────────────────
        await self._dispatch_match_notifications(new_match_events, target_date)

        # ── Commit ────────────────────────────────────────────────────────────
        await self.db.commit()

        stats = {
            "total": len(listings_data),
            "inserted": len(inserts),
            "updated": updates,
            "matched": matched_total,
        }
        logger.info(
            "listings_pipeline_complete", extra={"date": str(target_date), **stats}
        )
        return stats

    # ─────────────────────────────────────────────────────────────────────────
    # Notifications
    # ─────────────────────────────────────────────────────────────────────────

    async def _dispatch_match_notifications(
        self,
        events: List[Tuple[uuid.UUID, CauseListing]],
        listing_date: date,
    ) -> None:
        if not events:
            return

        date_str = listing_date.isoformat()
        potential_keys = [
            f"cause_list_match:{date_str}:{listing.matched_case_id}"
            for _, listing in events
            if listing.matched_case_id
        ]
        if not potential_keys:
            return

        existing_dedup = set(
            (
                await self.db.execute(
                    select(Notification.dedup_key).where(
                        Notification.dedup_key.in_(potential_keys)
                    )
                )
            ).scalars()
        )

        new_notifs: List[Notification] = []
        for lawyer_id, listing in events:
            if not listing.matched_case_id:
                continue
            dedup_key = f"cause_list_match:{date_str}:{listing.matched_case_id}"
            if dedup_key in existing_dedup:
                continue

            court_info = (
                f"Court {listing.court_number}, Serial {listing.serial_number}"
                if listing.court_number and listing.serial_number
                else "cause list"
            )
            new_notifs.append(
                Notification(
                    user_id=lawyer_id,
                    notification_type=NotificationType.cause_list_match,
                    title=f"Case listed on {listing_date.strftime('%d %b %Y')}",
                    message=(
                        f"{listing.case_number or 'Your case'} is listed in "
                        f"{court_info} on {listing_date.strftime('%d %b %Y')}."
                    ),
                    link=f"/cause-list?date={date_str}",
                    linked_entity_type="cause_listing",
                    linked_entity_id=listing.id,
                    dedup_key=dedup_key,
                    is_read=False,
                )
            )
            existing_dedup.add(dedup_key)

        if new_notifs:
            self.db.add_all(new_notifs)
            logger.info(
                "cause_list_notifications_queued",
                extra={"count": len(new_notifs), "date": date_str},
            )
