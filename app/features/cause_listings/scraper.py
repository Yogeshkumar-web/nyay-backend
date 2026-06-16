"""
Allahabad High Court — Cause List Scraper (PDF-based)
══════════════════════════════════════════════════════

Real AHC form flow (discovered by probing the live site):

  Step 1  GET  indexA.html                           → seeds session cookies
  Step 2  POST input1A.jsp   {listType=Z}            → returns date dropdown + form
  Step 3  POST input2A.jsp   {listDate, criteria=court, listType=Z}
                                                      → returns court dropdown + hidden fields
  Step 4  POST viewlistA.jsp {courtNo=-99, ...hidden} → returns a <a href="...pdf"> link

Then we download the PDF (~32 MB / 2147 pages) and parse it with pypdf.

Key facts:
  • Date format on AHC: DD-MM-YYYY  (dashes, not slashes)
  • No CAPTCHA for "Court Wise" (criteria=court)
  • courtNo=-99 = entire list (all courts)
  • PDF parsing takes ~34 s on a typical server — always run from a Celery task

Public API
──────────
  scrape_cause_list_for_date(target_date) → list[dict]
    • Never raises — returns [] on any failure
    • Designed to be called from an async context (uses run_in_executor for PDF parse)
"""

import asyncio
import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

logger = logging.getLogger(__name__)

# ── AHC URLs ──────────────────────────────────────────────────────────────────
AHC_BASE = "https://www.allahabadhighcourt.in"
AHC_INDEX_URL = f"{AHC_BASE}/causelist/indexA.html"
AHC_INPUT1_URL = f"{AHC_BASE}/causelist/input1A.jsp"
AHC_INPUT2_URL = f"{AHC_BASE}/causelist/input2A.jsp"
AHC_VIEWLIST_URL = f"{AHC_BASE}/causelist/viewlistA.jsp"

# ── Timeouts ──────────────────────────────────────────────────────────────────
NAV_TIMEOUT = 30.0  # seconds for each form step
DOWNLOAD_TIMEOUT = 300.0  # 5 min — PDF download can be slow

# ── Browser-like headers ──────────────────────────────────────────────────────
_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}

# ── Regex patterns ────────────────────────────────────────────────────────────
# Case number: TYPE/NUMBER/YEAR  e.g. SPLAD/354/2026, WRIA/6637/2026
CASE_NUM_RE = re.compile(r"\b([A-Z]{2,10}/\d+/\d{4})\b")

# Court section header: "Court No-1", "Court No-CJ", "Court No. 5" etc.
COURT_RE = re.compile(r"Court\s+No[-.\s]+([A-Z0-9]+)", re.IGNORECASE)

# Serial number: a 1–4 digit number at the start of a case entry line
SERIAL_RE = re.compile(r"^\s{1,5}(\d{1,4})\s", re.MULTILINE)

# VS separator (party split)
_VS_RE = re.compile(r"\s{2,}VS\s{2,}", re.IGNORECASE)

# Case-type prefix from case number, e.g. "WRIT-A 1234/2024" → "WRIT-A"
_CASE_TYPE_RE = re.compile(r"^([A-Z][A-Z0-9.\-]{1,15})/", re.IGNORECASE)

# Shared thread pool for CPU-heavy PDF parsing
_PDF_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf_parser")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _clean(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def _extract_case_type(case_number: Optional[str]) -> Optional[str]:
    if not case_number:
        return None
    m = _CASE_TYPE_RE.match(case_number.strip())
    return m.group(1).upper() if m else None


def _best_available_date(target: date, available: List[str]) -> Optional[str]:
    """
    Pick the closest date in `available` (DD-MM-YYYY strings) that is ≤ target.
    Returns None if the list is empty.
    """
    if not available:
        return None
    best: Optional[str] = None
    for ds in available:
        try:
            d = datetime.strptime(ds, "%d-%m-%Y").date()
            if d <= target:
                if best is None:
                    best = ds
                else:
                    best_d = datetime.strptime(best, "%d-%m-%Y").date()
                    if d > best_d:
                        best = ds
        except ValueError:
            pass
    return best or available[0]  # fall back to most recent


# ─────────────────────────────────────────────────────────────────────────────
# Step 1-4: navigate the AHC form chain to obtain the PDF URL
# ─────────────────────────────────────────────────────────────────────────────


async def _navigate_to_pdf_url(target_date: date) -> Tuple[str, str]:
    """
    Navigate the 4-step AHC form and return (pdf_url, actual_date_str).

    actual_date_str is DD-MM-YYYY of the date for which we got a PDF —
    it may differ from target_date if the exact date has no listing.

    Raises RuntimeError on any unrecoverable failure.
    """
    target_str = target_date.strftime("%d-%m-%Y")

    async with httpx.AsyncClient(
        timeout=NAV_TIMEOUT,
        follow_redirects=True,
        headers=_HEADERS,
    ) as client:
        # ── Step 1: Seed session cookies ─────────────────────────────────────
        try:
            await client.get(AHC_INDEX_URL)
        except Exception as exc:
            logger.warning("ahc_index_seed_failed", extra={"error": str(exc)})

        # ── Step 2: POST input1A → get date dropdown ─────────────────────────
        r1 = await client.post(
            AHC_INPUT1_URL,
            data={"listType": "Z"},
            headers={
                **_HEADERS,
                "Referer": AHC_INDEX_URL,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        r1.raise_for_status()

        soup1 = BeautifulSoup(r1.text, "html.parser")
        available = [
            opt.get("value", "").strip()
            for opt in soup1.find_all("option")
            if opt.get("value", "").strip()
        ]

        if target_str in available:
            listing_date = target_str
        else:
            listing_date = _best_available_date(target_date, available)
            if not listing_date:
                raise RuntimeError("AHC returned no available dates")
            logger.info(
                "ahc_date_approximated",
                extra={"requested": target_str, "using": listing_date},
            )

        # ── Step 3: POST input2A → Court Wise (no CAPTCHA) ───────────────────
        r2 = await client.post(
            AHC_INPUT2_URL,
            data={"listDate": listing_date, "criteria": "court", "listType": "Z"},
            headers={
                **_HEADERS,
                "Referer": AHC_INPUT1_URL,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        r2.raise_for_status()

        soup2 = BeautifulSoup(r2.text, "html.parser")
        hidden = {
            inp.get("name"): inp.get("value", "")
            for inp in soup2.find_all("input", type="hidden")
            if inp.get("name")
        }

        # ── Step 4: POST viewlistA → entire list (courtNo=-99) ───────────────
        post_data = {"courtNo": "-99", "listDate": listing_date, **hidden}
        r3 = await client.post(
            AHC_VIEWLIST_URL,
            data=post_data,
            headers={
                **_HEADERS,
                "Referer": AHC_INPUT2_URL,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        r3.raise_for_status()

        soup3 = BeautifulSoup(r3.text, "html.parser")
        pdf_links = [
            a.get("href")
            for a in soup3.find_all("a")
            if (a.get("href") or "").lower().endswith(".pdf")
        ]

        if not pdf_links:
            raise RuntimeError(
                f"No PDF link in viewlistA.jsp response (date={listing_date}). "
                "Cause list may not be published yet."
            )

        logger.info(
            "ahc_pdf_url_found",
            extra={"date": listing_date, "url": pdf_links[0]},
        )
        return pdf_links[0], listing_date


# ─────────────────────────────────────────────────────────────────────────────
# PDF download
# ─────────────────────────────────────────────────────────────────────────────


async def _download_pdf(pdf_url: str, target_date: date) -> bytes:
    """Download the cause list PDF. Raises on failure."""
    async with httpx.AsyncClient(
        timeout=DOWNLOAD_TIMEOUT,
        follow_redirects=True,
        headers={**_HEADERS, "Referer": AHC_VIEWLIST_URL},
    ) as client:
        r = await client.get(pdf_url)
        r.raise_for_status()

    logger.info(
        "ahc_pdf_downloaded",
        extra={"date": str(target_date), "size_kb": len(r.content) // 1024},
    )
    return r.content


# ─────────────────────────────────────────────────────────────────────────────
# PDF parser  (synchronous — run in thread executor)
# ─────────────────────────────────────────────────────────────────────────────


def _parse_pdf_bytes(pdf_bytes: bytes, target_date: date) -> List[Dict[str, Any]]:
    """
    Parse the AHC cause list PDF into a list of row dicts.

    Extraction strategy:
      • Per-page: detect court section header → update current_court
      • Per-page: find serial numbers + case numbers positionally
      • For each case number: find nearest preceding serial number
      • De-duplicate by case number (sub-cases appear multiple times)
      • Party names: split text before case number on VS

    This is synchronous and takes ~34 s for the full 32 MB PDF.
    Always call via run_in_executor.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    entries: List[Dict[str, Any]] = []
    seen_case_nums: set = set()
    current_court = "UNKNOWN"

    for page in reader.pages:
        text = page.extract_text() or ""

        # Update court number when a new court section begins
        cm = COURT_RE.search(text)
        if cm:
            current_court = cm.group(1).strip()

        # Collect serial numbers on this page (position → value)
        serials = [(m.start(), int(m.group(1))) for m in SERIAL_RE.finditer(text)]

        # Process each case number found on this page
        for cn_m in CASE_NUM_RE.finditer(text):
            cn = cn_m.group(1)
            if cn in seen_case_nums:
                continue
            seen_case_nums.add(cn)

            cn_pos = cn_m.start()

            # Find the nearest serial before this case number
            serial: Optional[int] = None
            for s_pos, s_val in reversed(serials):
                if s_pos < cn_pos:
                    serial = s_val
                    break

            # Extract party names from the ~300 chars before the case number
            snippet = text[max(0, cn_pos - 300) : cn_pos]
            vs_m = _VS_RE.search(snippet)
            petitioner: Optional[str] = None
            respondent: Optional[str] = None
            case_title: Optional[str] = None

            if vs_m:
                # Last "word group" before VS = petitioner; first after VS = respondent
                pet_raw = snippet[: vs_m.start()].strip()
                resp_raw = snippet[vs_m.end() :].strip()
                petitioner = _clean(pet_raw.split("\n")[-1])
                respondent = _clean(resp_raw.split("\n")[0])
                if petitioner or respondent:
                    case_title = f"{petitioner or ''} VS {respondent or ''}".strip()

            entries.append(
                {
                    "listing_date": target_date,
                    "court_number": current_court,
                    "serial_number": serial,
                    "case_number": cn,
                    "case_title": case_title,
                    "petitioner": petitioner,
                    "respondent": respondent,
                    "advocate_name": None,  # not reliably extractable from PDF layout
                    "case_type_raw": _extract_case_type(cn),
                    "remarks": None,
                    "raw_row_text": None,
                }
            )

    logger.info(
        "pdf_parse_complete",
        extra={
            "date": str(target_date),
            "pages": len(reader.pages),
            "entries": len(entries),
        },
    )
    return entries


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


async def scrape_cause_list_for_date(target_date: date) -> List[Dict[str, Any]]:
    """
    Full pipeline: navigate form chain → download PDF → parse.

    • Never raises — returns [] on any failure so the caller decides on retry/alert.
    • PDF parsing is offloaded to a thread executor so the event loop stays free.
    • Expected wall-clock time: ~40 s (network-dependent).
      Always call from a Celery task, never from a synchronous HTTP handler.
    """
    logger.info("scraper_start", extra={"date": str(target_date)})

    # ── 1. Get PDF URL ────────────────────────────────────────────────────────
    try:
        pdf_url, actual_date_str = await _navigate_to_pdf_url(target_date)
    except Exception as exc:
        logger.error(
            "scraper_nav_failed",
            extra={"date": str(target_date), "error": str(exc)},
        )
        return []

    # Parse actual date back so we label DB rows correctly
    try:
        actual_date = datetime.strptime(actual_date_str, "%d-%m-%Y").date()
    except ValueError:
        actual_date = target_date

    # ── 2. Download PDF ───────────────────────────────────────────────────────
    try:
        pdf_bytes = await _download_pdf(pdf_url, actual_date)
    except Exception as exc:
        logger.error(
            "scraper_pdf_download_failed",
            extra={"date": str(target_date), "url": pdf_url, "error": str(exc)},
        )
        return []

    # ── 3. Parse PDF in thread (CPU-heavy) ────────────────────────────────────
    try:
        loop = asyncio.get_event_loop()
        entries = await loop.run_in_executor(
            _PDF_EXECUTOR, _parse_pdf_bytes, pdf_bytes, actual_date
        )
    except Exception as exc:
        logger.error(
            "scraper_pdf_parse_failed",
            extra={"date": str(target_date), "error": str(exc)},
        )
        return []

    logger.info(
        "scraper_done",
        extra={"date": str(target_date), "count": len(entries)},
    )
    return entries
