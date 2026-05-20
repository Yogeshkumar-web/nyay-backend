"""
NCRB FIR Parser — deterministic extraction of all 15 sections from OCR text.

No LLM is used here. Pure Python regex + string matching.

Key design decisions:
  - Section boundaries are located by known English anchor phrases (always
    present in bilingual NCRB forms, unaffected by OCR Hindi corruption).
  - Section 2 (Acts) and Section 7 (Accused) tables are parsed from their
    merged/flattened OCR representation into clean numbered plain text.
  - Output dict keys match TypingAgentState["detected_sections"] and the
    _FIR_ORDER in assemble.py.

Usage:
    from app.agents.typing.fir_parser import parse_ncrb_fir
    sections = parse_ncrb_fir(ocr_raw_text)   # → dict[str, str]
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Text normalisation
# ─────────────────────────────────────────────────────────────────────────────

# (pattern, replacement) — applied in order to the whole OCR text first
_NORMALISE: list[tuple[str, str]] = [
    # Expand common OCR abbreviations
    (r"मुरा\s*0\b", "मुरादाबाद"),
    (r"उ\s*0\s*प्र\s*0\b", "उत्तर प्रदेश"),
    (r"उ\s*\.\s*प्र\s*\.\B", "उत्तर प्रदेश"),
    (r"म\s*0\s*हे\s*0\s*का\s*0", "म0हे0का0"),  # keep, meaningful in tehreer
    (r"म\s*0\s*का\s*0", "म0का0"),
    # Blank / "Not stated" → empty
    (r"\bNot\s+stated\b", ""),
    (r"\bNot\s+Stated\b", ""),
    # Stray OCR artefacts
    (r"\(\s*i\s*\)", ""),  # "(i)" that OCR puts after occupation
    (r"[ \t]{2,}", " "),  # collapse multiple spaces (not newlines)
]


def _normalise(text: str) -> str:
    for pat, repl in _NORMALISE:
        text = re.sub(pat, repl, text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Section boundary detection
# ─────────────────────────────────────────────────────────────────────────────

# Ordered list of (section_key, anchor_regex).
# The first match position in the text becomes the section boundary.
_ANCHORS: list[tuple[str, str]] = [
    ("header", r"FIRST\s+INFORMATION\s+REPORT"),
    ("section_1", r"1\.\s+District/Unit"),
    # Section 2: use the Acts column header as anchor — it's unique and always present,
    # even when OCR drops or malforms the leading "2." section number.
    ("section_2", r"Acts\s*\(अधिनियम\)"),
    ("section_3", r"3\.\s*\(a\)\s*Occurrence"),
    ("section_4", r"4\.\s*Type of Information"),
    ("section_5", r"5\.\s*Place of Occurrence"),
    ("section_6", r"6\.\s*Complainant\s*/\s*Informant"),
    ("section_7", r"7\.\s*Details of known"),
    ("section_8", r"8\.\s*Reasons for delay"),
    ("section_9", r"9\.\s*Particulars of properties"),
    ("section_10", r"10\.\s*Total value of property"),
    ("section_11", r"11\.\s*Inquest Report"),
    ("section_12", r"12\.\s*First Information contents"),
    ("section_13", r"13\.\s*Action taken"),
    ("section_14", r"14\.\s*Signature"),
    ("section_15", r"15\.\s*Date and time of dispatch"),
    ("attachment", r"Attachment to item\s+7"),
    ("physical", r"Physical features.*?suspect/accused"),
]


def _find_boundaries(text: str) -> dict[str, int]:
    """Return {section_key: char_offset} for each anchor found in text."""
    boundaries: dict[str, int] = {}
    for key, pattern in _ANCHORS:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            boundaries[key] = m.start()
    return boundaries


def _slice(text: str, boundaries: dict[str, int], key: str) -> str:
    """Return the text slice for a section (from its start to next section start)."""
    if key not in boundaries:
        return ""
    start = boundaries[key]
    # Find next boundary that comes after this one
    later = sorted(pos for k, pos in boundaries.items() if pos > start)
    end = later[0] if later else len(text)
    return text[start:end].strip()


# ─────────────────────────────────────────────────────────────────────────────
# Per-section extractors
# ─────────────────────────────────────────────────────────────────────────────


def _extract_header(text: str) -> str:
    """FIR title block (bilingual header lines)."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Stop at section 1 prefix
        if re.match(r"1\.\s+District", line):
            break
        lines.append(line)
    return "\n".join(lines)


def _extract_section_1(text: str) -> str:
    """District, PS, Year, FIR No, Date/Time."""
    # Remove leading '1.' marker
    text = re.sub(r"^1\.\s+", "", text, flags=re.MULTILINE).strip()
    # Clean up trailing blank "Not stated" fields
    text = re.sub(r":\s*\n", ": —\n", text)
    return text.strip()


# ── Section 2: Acts / Sections table ────────────────────────────────────────


def _parse_acts_table(raw: str) -> str:
    """
    Parse merged NCRB acts table into numbered plain text.

    OCR merges all rows into one string like:
        "S.No. (क्र.सं.)Acts (अधिनियम)Sections (धारा(एँ))
         1भारतीय न्याय संहिता (बी एन एस), 2023742भारतीय..."

    The core difficulty: row number 2 is directly adjacent to section 74 from
    the previous row → "742" where r"(?<!\d)2" would fail the lookbehind.
    Solution: use findall with a lookahead r"(?=\d[ऀ-ॿ]|\s*$)" that stops the
    section capture just before the next row's leading digit + Devanagari char.

    Output:
        1. भारतीय न्याय संहिता (बी एन एस), 2023 — धारा 74
        2. भारतीय न्याय संहिता (बी एन एस), 2023 — धारा 76
        ...
    """
    # Strip any column-header row (S.No./Acts/Sections header in any order)
    raw = re.sub(
        r"(?:S[\s.]*No[\s.]*\(.*?\)\s*)?Acts\s*\(अधिनियम\)\s*Sections\s*\(.*?\)",
        "",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    raw = raw.strip()

    if not raw:
        return ""

    # Pattern explanation:
    #   (\d{1,2})          — row number (1 or 2 digits)
    #   ([ऀ-ॿ].*?\d{4})   — act name: starts with Devanagari, ends at 4-digit year (lazy)
    #   \s*([\d()\s,/]+?)  — section identifier after the year (lazy)
    #   (?=\d[ऀ-ॿ]|\s*$)  — lookahead: next row starts with digit+Devanagari, or end of string
    #
    # The lookahead is the key: it stops the section capture just before the next
    # row number (even when the row number is directly concatenated with this row's
    # section digits, e.g. "742" = section "74" + next row "2").
    _ACTS_PATTERN = re.compile(
        r"(\d{1,2})" r"([ऀ-ॿ].*?\d{4})" r"\s*([\d()\s,/]*?)\s*" r"(?=\d[ऀ-ॿ]|\s*$)",
        re.DOTALL,
    )

    entries: list[str] = []
    for m in _ACTS_PATTERN.finditer(raw):
        sno = m.group(1).strip()
        act = " ".join(m.group(2).split())  # collapse internal whitespace
        section = m.group(3).strip()
        if act:
            line = f"{sno}. {act}"
            if section:
                line += f" — धारा {section}"
            entries.append(line)

    if not entries:
        # Pattern didn't match — return raw cleaned text as fallback
        return raw.strip()

    return "\n".join(entries)


def _extract_section_2(text: str) -> str:
    # Strip optional leading "2." — may be absent if anchor matched at "Acts (...)"
    raw = re.sub(r"^(?:2\.?\s*\n?\s*)?", "", text, count=1).strip()
    return _parse_acts_table(raw)


# ── Section 3: Occurrence ────────────────────────────────────────────────────


def _extract_section_3(text: str) -> str:
    """Extract occurrence date/time/GD reference."""
    text = re.sub(r"^3\.\s*", "", text, count=1)

    def _field(label_pat: str) -> str:
        m = re.search(label_pat + r"\s*[:\)]\s*([^\n(]+)", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    day = _field(r"Day\s*\(दिन\)")
    date_from = _field(r"Date from\s*\(दिनांक से\)")
    date_to = _field(r"Date To\s*\(दिनांक त")
    time_from = _field(r"Time From\s*\(समय से\)")
    time_to = _field(r"Time To\s*\(समय त")
    time_period = _field(r"Time Period\s*\(समय अवधि\)")
    info_date = _field(r"Date\s*\(दिनांक\):\s*(?=\d{2}/\d{2}/\d{4})")
    if not info_date:
        # fallback — find date in part (b)
        m = re.search(
            r"\(b\).*?Date.*?(\d{2}/\d{2}/\d{4})", text, re.DOTALL | re.IGNORECASE
        )
        info_date = m.group(1) if m else ""
    info_time = _field(r"Time\s*\(समय\):\s*(?=\d{2}:\d{2})")
    gd_entry = _field(r"Entry No\.\s*\(प्रविष्टि सं\.\)")
    gd_dt_m = re.search(r"Date and Time.*?(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
    gd_dt = gd_dt_m.group(1) if gd_dt_m else ""

    lines = []
    if day:
        lines.append(f"दिन (Day): {day}")
    if date_from:
        lines.append(f"दिनांक से (Date From): {date_from}")
    if date_to:
        lines.append(f"दिनांक तक (Date To): {date_to}")
    if time_period:
        lines.append(f"समय अवधि (Time Period): {time_period}")
    if time_from:
        lines.append(f"समय से (Time From): {time_from}")
    if time_to:
        lines.append(f"समय तक (Time To): {time_to}")
    lines.append("")
    if info_date:
        lines.append(f"थाने पर सूचना प्राप्त दिनांक: {info_date}")
    if info_time:
        lines.append(f"थाने पर सूचना प्राप्त समय: {info_time}")
    if gd_entry:
        lines.append(f"रोजनामचा प्रविष्टि सं.: {gd_entry}")
    if gd_dt:
        lines.append(f"रोजनामचा दिनांक: {gd_dt}")

    # Remove blank-only result
    result = "\n".join(lines).strip()
    if not result or result == "":
        return text.strip()
    return result


def _extract_section_4(text: str) -> str:
    text = re.sub(r"^4\.\s*", "", text, count=1)
    m = re.search(r"Type of Information.*?[:\)]\s*(.+)", text, re.IGNORECASE)
    return m.group(1).strip() if m else text.strip()


# ── Section 5: Place of occurrence ──────────────────────────────────────────


def _extract_section_5(text: str) -> str:
    text = re.sub(r"^5\.\s*", "", text, count=1)

    def _field(label_pat: str) -> str:
        m = re.search(label_pat + r"\s*[:\)]\s*([^\n]+)", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    direction = _field(r"Direction and distance from P\.S\.")
    # Beat No often picks up direction text due to layout — clean it
    beat_raw = _field(r"Beat No\.\s*\(बीट सं\.\)")
    beat = "" if direction and beat_raw and beat_raw == direction else beat_raw
    address = _field(r"Address\s*\(पता\)")
    outside = _field(r"Name of P\.S\.")

    lines = []
    if direction:
        lines.append(f"दिशा व दूरी (Direction & Distance): {direction}")
    if beat:
        lines.append(f"बीट नं. (Beat No.): {beat}")
    if address:
        lines.append(f"पता (Address): {address}")
    if outside:
        lines.append(f"बाहरी थाना (Outside P.S.): {outside}")

    return "\n".join(lines).strip() or text.strip()


# ── Section 6: Complainant ───────────────────────────────────────────────────


def _extract_section_6(text: str) -> str:
    text = re.sub(r"^6\.\s*", "", text, count=1)

    def _field(label_pat: str) -> str:
        # Use [^\n:]* to skip over anything between the label keyword and the
        # colon (e.g. Hindi gloss "(मोबाइल सं.)" or "(पिता का नाम)").
        # This is more robust than the old \s*[:\)]\s* suffix which failed
        # whenever the label contained extra text before the colon.
        m = re.search(label_pat + r"[^\n:]*:\s*([^\n(]+)", text, re.IGNORECASE)
        val = m.group(1).strip() if m else ""
        # Remove trailing label artefacts like "(d)" that sometimes get appended
        val = re.sub(r"\s*\([a-z]\)\s*$", "", val)
        return val

    name = _field(r"Name\s*\(नाम\)")
    # Father's Name: use a loose label so Hindi spelling variants all match
    father = _field(r"Father'?s?\s*Name")
    dob = _field(r"Date/Year of Birth")
    # DOB sometimes has "वर्ष ) : 1973" appended — clean
    dob = re.sub(r"\s*\(d\).*$", "", dob).strip()
    dob = re.sub(r"वर्ष\s*\)?\s*:\s*", "", dob).strip()
    nationality = _field(r"Nationality")
    nationality = re.sub(r"\s*वर्ष.*$", "", nationality).strip()  # remove leaked DOB
    uid = _field(r"UID No\.")
    passport = _field(r"Passport No\.")
    occupation = _field(r"Occupation")
    # Mobile label varies: "Mobile (मोबाइल)" or "Mobile (मोबाइल सं.)"
    phone = _field(r"Mobile")
    if not phone:
        phone = _field(r"Phone number")

    # Address table — extract present/permanent address rows.
    # Old pattern [^\n2]+ incorrectly stopped at every "2" digit in the address.
    # New pattern: capture everything up to the "2 स्थायी पता" row (or end).
    addr_present = ""
    addr_perm = ""
    addr_m = re.search(
        r"1\s*वर्तमान\s*पता\s*(.*?)(?=\s*2\s*स्थायी\s*पता|\s*$)",
        text,
        re.DOTALL,
    )
    if addr_m:
        addr_present = " ".join(addr_m.group(1).split()).strip()
    addr_m2 = re.search(
        r"2\s*स्थायी\s*पता\s*(.*?)(?=\s*\d\s*[^\n]|\s*$)",
        text,
        re.DOTALL,
    )
    if addr_m2:
        addr_perm = " ".join(addr_m2.group(1).split()).strip()

    lines = []
    if name:
        lines.append(f"नाम (Name): {name}")
    if father:
        lines.append(f"पिता का नाम (Father): {father}")
    if dob:
        lines.append(f"जन्म वर्ष (DOB/Year): {dob}")
    if nationality:
        lines.append(f"राष्ट्रीयता (Nationality): {nationality}")
    if uid:
        lines.append(f"यूआईडी सं. (UID): {uid}")
    if passport:
        lines.append(f"पासपोर्ट सं. (Passport): {passport}")
    if occupation:
        lines.append(f"व्यवसाय (Occupation): {occupation}")
    if addr_present:
        lines.append(f"वर्तमान पता (Present Address): {addr_present}")
    if addr_perm:
        lines.append(f"स्थायी पता (Permanent Address): {addr_perm}")
    if phone:
        lines.append(f"मोबाइल (Mobile): {phone}")

    return "\n".join(lines).strip() or text.strip()


# ── Section 7: Accused table ─────────────────────────────────────────────────

# Relationship keywords that precede relative's name
_RELATION_KW = r"(पिता|माता|पत्नी|पति|पुत्र|पुत्री|भाई|बहन|अभिभावक)"
_RELATION_PAT = re.compile(_RELATION_KW + r"\s+का\s+नाम\s*:")

# Address starters in Hindi
_ADDR_START = re.compile(
    r"(?:ग्राम|मकान|प्लॉट|गली|मोहल्ला|नगर|शहर|थाना|जिला|[\d]+\.\s*ग्राम)",
    re.IGNORECASE,
)


def _parse_accused_table(raw: str) -> str:
    """
    Parse merged NCRB accused table into numbered plain text.

    OCR merges rows like:
        "1योगेशपिता का नाम: हरि सिंहग्राम लालापुर...मुरा 02गौरव सिंह..."

    The difficulty: row 2's leading digit is adjacent to "0" in "मुरा 0"
    so a simple r"(?<!\d)(\d)" lookbehind fails. We use findall with a
    lookahead r"(?=\d[ऀ-ॿ]|\s*$)" that stops each row's content just before
    the next row number + Devanagari character.

    Output:
        1. योगेश — पिता: हरि सिंह — ग्राम लालापुर पीपलसाना, ठाकुरद्वारा, मुरादाबाद
        2. गौरव सिंह — पिता: हरि सिंह — ग्राम लालापुर पीपलसाना, ठाकुरद्वारा, मुरादाबाद
        ...
    """
    # Strip column header row (table headers for the accused table)
    raw = re.sub(
        r"S[\s.]*No[\s.]*\(.*?\).*?(?:Present Address|वर्तमान\s+पता).*?(?=\d[ऀ-ॿ])",
        "",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Strip "Accused More Than" note
    raw = re.sub(r"Accused More Than.*?\d+", "", raw, flags=re.IGNORECASE)
    raw = raw.strip()

    if not raw:
        return ""

    # Pattern: row_num (Devanagari start) + content (lazy) + lookahead for next row or end
    # The lookahead (?=\d[ऀ-ॿ]|\s*$) stops content just before the next row's
    # digit+Devanagari sequence, even when digits are concatenated ("मुरा 02गौरव").
    _ACCUSED_PATTERN = re.compile(
        r"(\d{1,2})"  # row number
        r"([ऀ-ॿ].+?)"  # row content: starts with Devanagari, lazy
        r"(?=\d[ऀ-ॿ]|\s*$)",  # lookahead: next row or end of string
        re.DOTALL,
    )

    entries: list[str] = []
    for m in _ACCUSED_PATTERN.finditer(raw):
        sno_str = m.group(1).strip()
        content = m.group(2).strip()

        if not sno_str.isdigit():
            continue

        # Find relation keyword (पिता का नाम:, माता का नाम:, etc.)
        rel_m = _RELATION_PAT.search(content)
        if rel_m:
            name = content[: rel_m.start()].strip()
            rel_keyword = rel_m.group(1)  # e.g. "पिता"
            after_rel = content[rel_m.end() :].strip()

            # Relative name ends where address starts
            addr_m = _ADDR_START.search(after_rel)
            if addr_m:
                relative_name = after_rel[: addr_m.start()].strip()
                address = after_rel[addr_m.start() :].strip()
            else:
                # No address found — take first 60 chars as name, rest as address
                relative_name = after_rel[:60].strip()
                address = after_rel[60:].strip()

            # Collapse whitespace in address; drop duplicate row entries
            address = re.sub(
                r"\s+\d+\.\s+ग्राम.*$", "", address, flags=re.DOTALL
            ).strip()
            address = " ".join(address.split())

            parts_line = [f"{sno_str}. {name}"]
            if relative_name:
                parts_line.append(f"{rel_keyword}: {relative_name}")
            if address:
                parts_line.append(address)
            entries.append(" — ".join(parts_line))
        else:
            entries.append(f"{sno_str}. {content.strip()}")

    if not entries:
        return raw.strip()

    return "\n".join(entries)


def _extract_section_7(text: str) -> str:
    raw = re.sub(r"^7\.\s*", "", text, count=1).strip()
    return _parse_accused_table(raw)


# ── Sections 8–11: mostly empty in practice ──────────────────────────────────


def _extract_simple(text: str, section_num: int) -> str:
    """Generic extractor — strips section number and returns cleaned text."""
    text = re.sub(rf"^{section_num}\.\s*", "", text, count=1).strip()
    # Remove pure header labels, keep values
    text = re.sub(r"^\(.+?\)\s*", "", text)
    return text.strip()


# ── Section 12: FIR contents (tehreer) ───────────────────────────────────────


def _extract_section_12(text: str) -> str:
    """
    Extract the full tehreer text. This is free-form Hindi narrative.
    We preserve it verbatim; the LLM cleaning step will clean it up.
    """
    text = re.sub(
        r"^12\.\s*First Information contents\s*\(.*?\)\s*:?\s*",
        "",
        text,
        count=1,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return text.strip()


# ── Section 13: Action taken ─────────────────────────────────────────────────


def _extract_section_13(text: str) -> str:
    text = re.sub(r"^13\.\s*", "", text, count=1)
    # Clean "Not stated" and stray parenthetical headers
    text = re.sub(r"\(की गयी कार्यवाही.*?\):", "", text, flags=re.DOTALL)
    # Extract IO name — stop before "Rank", "or", "No." or end-of-line
    # NOTE: do NOT use character class [Rr] — it stops on any 'r' in the name
    io_m = re.search(
        r"Directed.*?I\.O\.\).*?:\s*([^\n]+?)(?=\s+Rank\s*\(|\s+or\s|\s+No\.\s*\(|\s*$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    io_name = io_m.group(1).strip() if io_m else ""
    rank_m = re.search(
        r"Rank\s*\(पद\)\s*:\s*([^\n]+?)(?=\s+No\.|\s*$)", text, re.IGNORECASE
    )
    rank = rank_m.group(1).strip() if rank_m else ""

    lines = ["(1) प्रकरण दर्ज किया गया और जांच प्रारम्भ की गयी।"]
    if io_name:
        lines.append(f"(2) जांच अधिकारी (I.O.): {io_name}")
    if rank:
        lines.append(f"    पद (Rank): {rank}")
    # Preserve R.O.A.C. note if present
    if "R.O.A.C." in text:
        lines.append("R.O.A.C. (आर.ओ.ए.सी.)")
    return "\n".join(lines)


# ── Section 14–15: Signatures + dispatch ─────────────────────────────────────


def _extract_section_14_15(text_14: str, text_15: str) -> str:
    lines: list[str] = []

    # Officer details from section 14.
    # Use lookahead to stop just before the next field label, not at "(" which
    # incorrectly truncated names like "THANA THAKURDWARA" to "THANA".
    name_m = re.search(
        r"Name\s*\(नाम\)\s*:\s*([^\n]+?)(?=\s+Rank\s*\(|\s+No\.\s*\(|\s*$)",
        text_14,
        re.IGNORECASE,
    )
    rank_m = re.search(
        r"Rank\s*\(पद\)\s*:\s*([^\n]+?)(?=\s+No\.\s*\(|\s*$)",
        text_14,
        re.IGNORECASE,
    )
    no_m = re.search(r"No\.\s*\(सं\.\)\s*:\s*([\d\w]+)", text_14, re.IGNORECASE)

    officer_name = name_m.group(1).strip() if name_m else ""
    officer_rank = rank_m.group(1).strip() if rank_m else ""
    officer_no = no_m.group(1).strip() if no_m else ""

    if officer_name:
        lines.append(f"थाना प्रभारी (Officer-in-Charge): {officer_name}")
    if officer_rank:
        lines.append(f"पद (Rank): {officer_rank}")
    if officer_no:
        lines.append(f"सं. (No.): {officer_no}")

    # Dispatch date from section 15
    date_m = re.search(r"(\d{2}/\d{2}/\d{4})", text_15)
    if date_m:
        lines.append(f"न्यायालय को प्रेषण दिनांक (Dispatch to Court): {date_m.group(1)}")

    return "\n".join(lines)


# ── Physical features (Attachment to item 7) ─────────────────────────────────


def _extract_physical_features(text: str) -> str:
    """
    Parse accused physical features table.
    Usually sparse — most fields blank.
    Returns a readable summary or empty string if all blank.
    """
    # Row split: same strategy as accused table
    row_pat = re.compile(
        r"(?<!\d)(\d{1,2})(?=[ऀ-ॿ\s]*(पुरुष|स्त्री|male|female))", re.IGNORECASE
    )
    parts = row_pat.split(text)

    entries: list[str] = []
    i = 1 if (parts and not parts[0].strip().isdigit()) else 0

    while i < len(parts) - 1:
        sno_str = parts[i].strip()
        content = parts[i + 1].strip() if (i + 1) < len(parts) else ""
        i += 2

        if not sno_str.isdigit():
            continue

        sex_m = re.search(r"(पुरुष|स्त्री)", content)
        sex = sex_m.group(1) if sex_m else ""

        marks_m = re.search(r"चेचक\s*:\s*(\S+)", content)
        marks = f"चेचक: {marks_m.group(1)}" if marks_m else ""

        fields: list[str] = [f"अभियुक्त {sno_str}"]
        if sex:
            fields.append(f"लिंग: {sex}")
        if marks:
            fields.append(marks)
        entries.append(", ".join(fields))

    return "\n".join(entries)


# ─────────────────────────────────────────────────────────────────────────────
# Main public function
# ─────────────────────────────────────────────────────────────────────────────


def parse_ncrb_fir(ocr_text: str) -> dict[str, str]:
    """
    Parse all 15 sections of an NCRB FIR from OCR text.

    Returns a dict with keys matching TypingAgentState["detected_sections"]:
        header, sections_law, occurrence, info_type, place,
        complainant, accused, delay_reasons, properties,
        fir_contents, action_taken, signatures, physical_features

    Section 12 (fir_contents) is returned verbatim — the caller should
    pass it to the LLM cleaning step (reformat node) separately.
    """
    text = _normalise(ocr_text)
    bd = _find_boundaries(text)

    if not bd:
        logger.warning("parse_ncrb_fir: no section anchors found — returning raw text")
        return {"fir_contents": ocr_text}

    def _s(key: str) -> str:
        return _slice(text, bd, key)

    # ── Section slices ────────────────────────────────────────────────────────
    raw_header = _s("header")
    raw_s1 = _s("section_1")
    raw_s2 = _s("section_2")
    raw_s3 = _s("section_3")
    raw_s4 = _s("section_4")
    raw_s5 = _s("section_5")
    raw_s6 = _s("section_6")
    raw_s7 = _s("section_7")
    raw_s8 = _s("section_8")
    raw_s9 = _s("section_9")
    raw_s12 = _s("section_12")
    raw_s13 = _s("section_13")
    raw_s14 = _s("section_14")
    raw_s15 = _s("section_15")
    raw_phys = _s("physical") or _s("attachment")

    # ── Build header block (sections 1 + title) ───────────────────────────────
    title_block = _extract_header(raw_header)
    s1_block = _extract_section_1(raw_s1)
    header_text = (title_block + "\n\n" + s1_block).strip()

    # ── Assemble output ───────────────────────────────────────────────────────
    sections: dict[str, str] = {}

    def _add(key: str, value: str) -> None:
        v = value.strip()
        if v:
            sections[key] = v

    _add("header", header_text)
    _add("sections_law", _extract_section_2(raw_s2))
    _add("occurrence", _extract_section_3(raw_s3))
    _add("info_type", _extract_section_4(raw_s4))
    _add("place", _extract_section_5(raw_s5))
    _add("complainant", _extract_section_6(raw_s6))
    _add("accused", _extract_section_7(raw_s7))
    _add("delay_reasons", _extract_simple(raw_s8, 8))
    _add("properties", _extract_simple(raw_s9, 9))
    _add("fir_contents", _extract_section_12(raw_s12))  # → LLM cleans this
    _add("action_taken", _extract_section_13(raw_s13))
    _add("signatures", _extract_section_14_15(raw_s14, raw_s15))
    _add("physical_features", _extract_physical_features(raw_phys))

    found = [k for k, v in sections.items() if v]
    logger.info("parse_ncrb_fir: extracted %d sections: %s", len(found), found)
    return sections
