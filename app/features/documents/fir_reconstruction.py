from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from html import escape as html_escape
from typing import Any

from app.features.documents.layout_artifact import LayoutTable, tables_by_type


_FIR_NUMBER_RE = re.compile(r"(?m)(?:^|\n)\s*(\d{1,2})\s*[.)]\s*")
_DATE_RE = r"\d{1,2}/\d{1,2}/\d{4}"
_TIME_RE = r"\d{1,2}\s*:\s*\d{2}"


@dataclass
class FirActRow:
    serial: str = ""
    act: str = ""
    section: str = ""


@dataclass
class FirAccusedRow:
    serial: str = ""
    name: str = ""
    father: str = ""
    address: str = ""
    alias: str = ""


@dataclass
class FirSchema:
    basic: dict[str, str] = field(default_factory=dict)
    acts: list[FirActRow] = field(default_factory=list)
    occurrence: dict[str, str] = field(default_factory=dict)
    information_type: str = ""
    place: dict[str, str] = field(default_factory=dict)
    complainant: dict[str, str] = field(default_factory=dict)
    accused: list[FirAccusedRow] = field(default_factory=list)
    delay_reasons: str = ""
    properties: list[dict[str, str]] = field(default_factory=list)
    total_property_value: str = ""
    inquest: list[dict[str, str]] = field(default_factory=list)
    fir_contents: str = ""
    action: dict[str, str] = field(default_factory=dict)
    dispatch_datetime: str = ""
    missing_required_fields: list[str] = field(default_factory=list)
    source_map: dict[str, str] = field(default_factory=dict)
    confidence_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FirSchema":
        return cls(
            basic=dict(data.get("basic") or {}),
            acts=[FirActRow(**row) for row in data.get("acts", [])],
            occurrence=dict(data.get("occurrence") or {}),
            information_type=str(data.get("information_type") or ""),
            place=dict(data.get("place") or {}),
            complainant=dict(data.get("complainant") or {}),
            accused=[FirAccusedRow(**row) for row in data.get("accused", [])],
            delay_reasons=str(data.get("delay_reasons") or ""),
            properties=list(data.get("properties") or []),
            total_property_value=str(data.get("total_property_value") or ""),
            inquest=list(data.get("inquest") or []),
            fir_contents=str(data.get("fir_contents") or ""),
            action=dict(data.get("action") or {}),
            dispatch_datetime=str(data.get("dispatch_datetime") or ""),
            missing_required_fields=list(data.get("missing_required_fields") or []),
            source_map=dict(data.get("source_map") or {}),
            confidence_score=float(data.get("confidence_score") or 0.0),
        )

    def to_detected_sections(self) -> dict[str, Any]:
        return {
            "__fir_schema": self.to_dict(),
            "header": _format_basic(self.basic),
            "sections_law": "\n".join(
                f"{row.serial}. {row.act} - धारा {row.section}".strip()
                for row in self.acts
            ),
            "occurrence": _format_dict(self.occurrence),
            "info_type": self.information_type,
            "place": _format_dict(self.place),
            "complainant": _format_dict(self.complainant),
            "accused": "\n".join(
                " - ".join(
                    part
                    for part in [
                        f"{row.serial}. {row.name}".strip(),
                        f"पिता का नाम: {row.father}" if row.father else "",
                        row.address,
                    ]
                    if part
                )
                for row in self.accused
            ),
            "delay_reasons": self.delay_reasons,
            "properties": "\n".join(
                " - ".join(str(v) for v in row.values() if v) for row in self.properties
            ),
            "fir_contents": self.fir_contents,
            "action_taken": _format_dict(self.action),
            "signatures": _format_signature(self.action),
            "dispatch": self.dispatch_datetime,
        }


def is_fir_text(text: str) -> bool:
    return bool(
        re.search(r"FIRST\s+INFORMATION\s+REPORT|FIR\s+No\.|प्रथम\s+सूचना", text, re.I)
    )


def reconstruct_fir(
    text: str,
    *,
    ocr_artifact: dict[str, Any] | None = None,
) -> FirSchema | None:
    if not is_fir_text(text):
        return None

    item_1 = _section_by_number(text, 1) or text
    acts_text = _slice_between(
        text, r"\b2\.\s*S\.?\s*No\.?", r"\b3\.\s*\(?\s*a\s*\)?\s*Occurrence"
    ) or _section_by_number(text, 2)
    occurrence_text = _slice_between(
        text, r"\b3\.\s*\(?\s*a\s*\)?\s*Occurrence", r"\b4\.\s*Type\s+of\s+Information"
    ) or _section_by_number(text, 3)
    info_text = _slice_between(
        text, r"\b4\.\s*Type\s+of\s+Information", r"\b5\.\s*Place\s+of\s+Occurrence"
    ) or _section_by_number(text, 4)
    place_text = _slice_between(
        text, r"\b5\.\s*Place\s+of\s+Occurrence", r"Complainant\s*/\s*Informant"
    ) or _section_by_number(text, 5)
    complainant_text = _slice_between(
        text, r"Complainant\s*/\s*Informant", r"\b7\.\s*Details\s+of\s+known"
    ) or _section_by_number(text, 6)
    accused_text = _slice_between(
        text, r"\b7\.\s*Details\s+of\s+known", r"\b8\.\s*Reasons\s+for\s+delay"
    ) or _section_by_number(text, 7)
    action_text = _slice_between(
        text, r"\b13\.\s*Action\s+taken", r"\b15\.\s*Date\s+and\s+time"
    ) or _section_by_number(text, 13)
    delay_text = _section_by_number(text, 8)
    properties_text = _section_by_number(text, 9)
    total_value_text = _section_by_number(text, 10)
    inquest_text = _section_by_number(text, 11)
    dispatch_text = _section_by_number(text, 15)
    place = _extract_place(place_text)
    narrative = _extract_narrative(text)
    layout_tables = tables_by_type(ocr_artifact)
    form_values = _extract_form_values(ocr_artifact)
    source_map: dict[str, str] = {}

    layout_acts = _extract_acts_from_layout(layout_tables.get("acts_sections", []))
    text_acts = _extract_acts(acts_text)
    source_map["acts"] = "layout_table" if layout_acts else "text_fallback"

    layout_properties = _extract_properties_from_layout(
        layout_tables.get("property", [])
    )
    text_properties = _extract_properties(properties_text)
    source_map["properties"] = "layout_table" if layout_properties else "text_fallback"

    layout_inquest = _extract_inquest_from_layout(layout_tables.get("inquest", []))
    text_inquest = _extract_inquest(inquest_text)
    source_map["inquest"] = "layout_table" if layout_inquest else "text_fallback"

    text_basic = _find_basic_details(text, item_1)
    form_basic = _basic_from_form_values(form_values)
    text_occurrence = _extract_occurrence(occurrence_text)
    form_occurrence = _occurrence_from_form_values(form_values)

    schema = FirSchema(
        basic=_merge_prefer_primary(form_basic, text_basic),
        acts=layout_acts or text_acts,
        occurrence=_merge_prefer_primary(form_occurrence, text_occurrence),
        information_type=_extract_information_type(info_text),
        place=place,
        complainant=_extract_complainant(complainant_text),
        fir_contents=narrative,
        delay_reasons=_extract_free_text(delay_text, 8),
        properties=layout_properties or text_properties,
        total_property_value=_extract_total_property_value(total_value_text),
        inquest=layout_inquest or text_inquest,
        action=_extract_action(action_text),
        dispatch_datetime=_extract_dispatch_datetime(dispatch_text),
    )
    layout_accused = _extract_accused_from_layout(layout_tables.get("accused", []))
    schema.accused = layout_accused or _extract_accused(
        accused_text,
        narrative,
        place.get("address", ""),
    )
    source_map["accused"] = "layout_table" if layout_accused else "text_fallback"
    source_map["narrative"] = "text_fallback"
    source_map["basic"] = "form_field" if any(form_basic.values()) else "text_fallback"
    source_map["occurrence"] = (
        "form_field" if any(form_occurrence.values()) else "text_fallback"
    )
    _apply_schema_diagnostics(schema, source_map)
    return schema


def render_fir_html(text: str) -> str | None:
    schema = reconstruct_fir(text)
    if schema is None:
        return None
    return render_fir_schema_html(schema)


def render_fir_sections_html(sections: dict[str, Any]) -> str:
    schema_data = sections.get("__fir_schema")
    if isinstance(schema_data, dict):
        schema = FirSchema.from_dict(schema_data)
        if sections.get("fir_contents"):
            schema.fir_contents = str(sections["fir_contents"])
        return render_fir_schema_html(schema)

    # Compatibility path for older callers that only have section strings.
    schema = FirSchema(
        basic={"raw": str(sections.get("header") or "")},
        fir_contents=str(sections.get("fir_contents") or ""),
    )
    return render_fir_schema_html(schema)


def render_fir_schema_html(schema: FirSchema) -> str:
    basic = schema.basic
    occurrence = schema.occurrence
    place = schema.place
    complainant = schema.complainant
    action = schema.action
    present_address = complainant.get("present_address") or place.get("address", "")
    permanent_address = complainant.get("permanent_address") or present_address

    parts: list[str] = [
        '<div class="typed-document typed-fir">',
        "<style>.typed-fir{font-family:'Noto Sans Devanagari','Mangal','Arial Unicode MS',sans-serif}.typed-fir table{border-collapse:collapse;width:100%;margin:8px 0 14px 0;table-layout:fixed}.typed-fir th,.typed-fir td{border:1px solid #cfcfcf;padding:7px 9px;vertical-align:top;word-wrap:break-word}.typed-fir th{text-align:left;background:#f7f7f7}.typed-fir p{margin:6px 0}</style>",
        '<h2 style="text-align:center">FIRST INFORMATION REPORT</h2>',
        '<p style="text-align:center"><strong>(Under Section 173 B.N.S.S)</strong></p>',
        '<p style="text-align:center"><strong>प्रथम सूचना रिपोर्ट</strong></p>',
        '<p style="text-align:center"><strong>(धारा 173 बी एन एस एस के तहत)</strong></p>',
        (
            "<p><strong>1. District/Unit (जिला/इकाई):</strong> "
            f"{_e(_not_stated(basic.get('district')))}, "
            "<strong>P.S. (थाना):</strong> "
            f"{_e(_not_stated(basic.get('police_station')))} "
            "<strong>Year (वर्ष):</strong> "
            f"{_e(_not_stated(basic.get('year')))}</p>"
        ),
        (
            "<p><strong>FIR No. (प्र.सू.रि. सं.):</strong> "
            f"{_e(_not_stated(basic.get('fir_no')))} "
            "<strong>Date and Time of FIR (प्र.सू.रि. की दिनांक और समय):</strong> "
            f"{_e(_not_stated(basic.get('fir_datetime')))}</p>"
        ),
        "<p><strong>2.</strong></p>",
    ]

    parts.append(
        _html_table(
            ["S.No. (क्र.सं.)", "Acts (अधिनियम)", "Sections (धारा(एँ))"],
            [[row.serial, row.act, row.section] for row in schema.acts]
            or [["", "", ""]],
        )
    )
    parts.extend(
        [
            "<p><strong>3. (a) Occurrence of offence (अपराध की घटना):</strong></p>",
            _html_table(
                ["", "", "", ""],
                [
                    [
                        "1",
                        f"Day (दिन): {_not_stated(occurrence.get('day'))}",
                        f"Date from (दिनांक से): {_not_stated(occurrence.get('date_from'))}",
                        f"Date To (दिनांक तक): {_not_stated(occurrence.get('date_to'))}",
                    ],
                    [
                        "",
                        f"Time Period (समय अवधि): {_not_stated(occurrence.get('time_period'))}",
                        f"Time From (समय से): {_not_stated(occurrence.get('time_from'))}",
                        f"Time To (समय तक): {_not_stated(occurrence.get('time_to'))}",
                    ],
                ],
            ),
            (
                "<p><strong>(b) Information received at P.S. "
                "(थाना जहां सूचना प्राप्त हुई):</strong> "
                f"Date (दिनांक): {_e(_not_stated(occurrence.get('info_date')))} "
                f"Time (समय): {_e(_not_stated(occurrence.get('info_time')))}</p>"
            ),
            (
                "<p><strong>(c) General Diary Reference (रोजनामचा संदर्भ):</strong> "
                f"Entry No. (प्रविष्टि सं.): {_e(_not_stated(occurrence.get('gd_entry')))}</p>"
            ),
            (
                "<p><strong>4. Type of Information (सूचना का प्रकार):</strong> "
                f"{_e(_not_stated(schema.information_type))}</p>"
            ),
            "<p><strong>5. Place of Occurrence (घटनास्थल):</strong></p>",
            _html_p(
                "1. (a) Direction and distance from P.S. (थाना से दूरी और दिशा)",
                place.get("direction_distance", ""),
            ),
            _html_p("Beat No. (बीट सं.)", place.get("beat_no", "")),
            _html_p("(b) Address (पता)", place.get("address", "")),
            _html_p(
                "(c) Outside Police Station - Name of P.S.", place.get("outside_ps", "")
            ),
            "<p><strong>6. Complainant / Informant (शिकायतकर्ता / सूचनाकर्ता):</strong></p>",
            _html_p("(a) Name (नाम)", complainant.get("name", "")),
            _html_p("(b) Father's Name (पिताका नाम)", complainant.get("father", "")),
            (
                "<p><strong>(c) Date/Year of Birth (जन्म तिथि / वर्ष):</strong> "
                f"{_e(_not_stated(complainant.get('dob_year')))} "
                "<strong>(d) Nationality (राष्ट्रीयता):</strong> "
                f"{_e(_not_stated(complainant.get('nationality')))}</p>"
            ),
            "<p><strong>(e) UID No. (यूआईडी सं.):</strong></p>",
            "<p><strong>(f) Passport No. (पासपोर्ट सं.):</strong></p>",
            "<p><strong>(g) ID Details:</strong></p>",
            _html_table(
                [
                    "S. No. (क्र.सं.)",
                    "ID Type (पहचान पत्र का प्रकार)",
                    "ID Number (पहचान संख्या)",
                ],
                [["", "", ""]],
            ),
            _html_p("(h) Occupation (व्यवसाय)", complainant.get("occupation", "")),
            "<p><strong>(i) Address (पता):</strong></p>",
            _html_table(
                ["S.No. (क्र.सं.)", "Address Type (पता का प्रकार)", "Address (पता)"],
                [
                    ["1", "वर्तमान पता", present_address],
                    ["2", "स्थायी पता", permanent_address],
                ],
            ),
            (
                "<p><strong>(j) Phone number (दूरभाष सं.):</strong> "
                "<strong>Mobile (मोबाइल सं.):</strong> "
                f"{_e(_not_stated(complainant.get('mobile')))}</p>"
            ),
            "<p><strong>7. Details of known / suspected / unknown accused with full particulars</strong></p>",
            "<p>(ज्ञात / संदिग्ध / अज्ञात अभियुक्त का पूरे विवरण सहित वर्णन):</p>",
            "<p><strong>Accused More Than (अज्ञात आरोपी एक से अधिक हों तो संख्या):</strong> 0</p>",
            _html_table(
                [
                    "S. No. (क्र.सं.)",
                    "Name (नाम)",
                    "Alias (उपनाम)",
                    "Relative's Name (रिश्तेदार का नाम)",
                    "Present Address (वर्तमान पता)",
                ],
                [
                    [
                        row.serial,
                        row.name,
                        row.alias,
                        f"पिता का नाम: {_not_stated(row.father)}",
                        _not_stated(row.address),
                    ]
                    for row in schema.accused
                ]
                or [["", "", "", "", ""]],
            ),
            "<p><strong>8. Reasons for delay in reporting by the complainant / informant</strong></p>",
            "<p>(शिकायतकर्ता / सूचनाकर्ता द्वारा रिपोर्ट देरी से दर्ज कराने के कारण):</p>",
            f"<p>{_e(schema.delay_reasons)}</p>" if schema.delay_reasons else "",
            "<p><strong>9. Particulars of properties of interest (संबन्धित सम्पत्ति का विवरण):</strong></p>",
            _html_table(
                [
                    "S. No. (क्र.सं.)",
                    "Property Category (सम्पत्ति श्रेणी)",
                    "Property Type (सम्पत्ति के प्रकार)",
                    "Description (विवरण)",
                    "Value (In Rs/-) (मूल्य (रु में))",
                ],
                [
                    [
                        row.get("serial", ""),
                        row.get("category", ""),
                        row.get("type", ""),
                        row.get("description", ""),
                        row.get("value", ""),
                    ]
                    for row in schema.properties
                ]
                or [["", "", "", "", ""]],
            ),
            (
                "<p><strong>10. Total value of property (In Rs/-) "
                "(सम्पत्ति का कुल मूल्य (रु में)):</strong> "
                f"{_e(_not_stated(schema.total_property_value))}</p>"
            ),
            "<p><strong>11. Inquest Report / U.D. case No., if any (मृत्यु समीक्षा रिपोर्ट / यू.डी.प्रकरण सं., यदि कोई हो):</strong></p>",
            _html_table(
                ["S. No. (क्र.सं.)", "UIDB Number (यू.डी.प्रकरण सं.)"],
                [
                    [row.get("serial", ""), row.get("uidb_number", "")]
                    for row in schema.inquest
                ]
                or [["", ""]],
            ),
            "<p><strong>12. First Information contents (प्रथम सूचना तथ्य):</strong></p>",
            _narrative_html(schema.fir_contents),
            "<p><strong>13. Action taken: Since the above information reveals commission of offence(s) u/s as mentioned at Item No. 2.</strong></p>",
            "<p>(की गयी कार्यवाही : चूंकि उपरोक्त जानकारी से पता चलता है कि अपराध करने का तरीका मद सं. 2 में उल्लेख धारा के तहत है.):</p>",
            "<p><strong>(1) Registered the case and took up the investigation:</strong> / or (या)</p>",
            _html_p(
                "(2) Directed (Name of I.O.) (जांच अधिकारी का नाम)",
                action.get("io_name", ""),
            ),
            _html_p("Rank (पद)", action.get("io_rank", "")),
            _html_p("No. (सं.)", action.get("io_no", "")),
            "<p><strong>(3) Refused investigation due to (जांच के लिए):</strong> or (के कारण इंकार किया या)</p>",
            _html_p("(4) Transferred to P.S. (थाना)", action.get("transferred_to", "")),
            "<p>F.I.R. read over to the complainant / informant, admitted to be correctly recorded and a copy given to the complainant / informant, free of cost.</p>",
            "<p>R.O.A.C. (आर.ओ.ए.सी.)</p>",
            _html_table(
                [
                    "14. Signature / Thumb impression of the complainant / informant (शिकायतकर्ता / सूचनाकर्ता के हस्ताक्षर / अंगूठे का निशान)",
                    "Signature of Officer in charge, Police Station (थाना प्रभारी के हस्ताक्षर)",
                ],
                [
                    [
                        "",
                        (
                            f"Name (नाम): {_not_stated(action.get('officer_name'))}\n"
                            f"Rank (पद): {_not_stated(action.get('officer_rank'))}\n"
                            f"No. (सं.): {_not_stated(action.get('officer_no'))}"
                        ),
                    ]
                ],
            ),
            (
                "<p><strong>15. Date and time of dispatch to the court "
                "(अदालत में प्रेषण की दिनांक और समय):</strong> "
                f"{_e(_not_stated(schema.dispatch_datetime))}</p>"
            ),
            "<p><strong>Attachment to item 7 of First Information Report (प्रथम सूचना रिपोर्ट के मद 7 संलग्नक):</strong></p>",
            "<p><strong>Physical features, deformities and other details of the suspect/accused: (If known / seen)</strong></p>",
            _physical_table_1(schema.accused),
            _physical_table_2(schema.accused),
            _physical_table_3(schema.accused),
            "<p>These fields will be entered only if complainant/informant gives any one or more particulars about the suspect/accused.</p>",
            "</div>",
        ]
    )
    return "\n".join(parts)


def _apply_schema_diagnostics(schema: FirSchema, source_map: dict[str, str]) -> None:
    missing = _missing_required_fields(schema)
    schema.missing_required_fields = missing
    schema.source_map = source_map
    schema.confidence_score = _schema_confidence(schema, missing)


def _missing_required_fields(schema: FirSchema) -> list[str]:
    required: list[tuple[str, str]] = [
        ("basic.fir_no", schema.basic.get("fir_no", "")),
        ("basic.police_station", schema.basic.get("police_station", "")),
        ("basic.year", schema.basic.get("year", "")),
        ("basic.fir_datetime", schema.basic.get("fir_datetime", "")),
        ("occurrence.info_date", schema.occurrence.get("info_date", "")),
        ("occurrence.info_time", schema.occurrence.get("info_time", "")),
        ("complainant.name", schema.complainant.get("name", "")),
        ("fir_contents", schema.fir_contents),
    ]
    if not schema.acts:
        required.append(("acts", ""))
    if not schema.accused:
        required.append(("accused", ""))
    return [field for field, value in required if not _compact(value)]


def _schema_confidence(schema: FirSchema, missing: list[str]) -> float:
    required_count = 10
    present_score = max(0, required_count - len(missing)) / required_count
    layout_bonus = 0.0
    if schema.source_map.get("acts") == "layout_table":
        layout_bonus += 0.05
    if schema.source_map.get("accused") == "layout_table":
        layout_bonus += 0.05
    return round(min(1.0, present_score + layout_bonus), 2)


def _section_by_number(text: str, number: int) -> str:
    matches = list(_FIR_NUMBER_RE.finditer(text))
    for idx, match in enumerate(matches):
        if int(match.group(1)) != number:
            continue
        start = match.end()
        end = len(text)
        for next_match in matches[idx + 1 :]:
            next_number = int(next_match.group(1))
            if number < next_number <= 15:
                end = next_match.start()
                break
        return text[start:end].strip()
    return ""


def _slice_between(text: str, start_pattern: str, end_pattern: str) -> str:
    start = re.search(start_pattern, text, re.IGNORECASE | re.DOTALL)
    if not start:
        return ""
    end = re.search(end_pattern, text[start.end() :], re.IGNORECASE | re.DOTALL)
    end_pos = start.end() + end.start() if end else len(text)
    return text[start.end() : end_pos].strip()


def _first_match(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return default
    return _compact(match.group(1))


def _compact(text: str | None) -> str:
    return _normalise_common_ocr_terms(re.sub(r"\s+", " ", text or "").strip(" :;,-"))


def _normalise_common_ocr_terms(text: str | None) -> str:
    value = text or ""
    replacements = {
        "\u092e\u0941\u0930\u093e0": "\u092e\u0941\u0930\u093e\u0926\u093e\u092c\u093e\u0926",
        "\u092e\u0941\u0930\u093e 0": "\u092e\u0941\u0930\u093e\u0926\u093e\u092c\u093e\u0926",
        "\u092e\u0941\u0930\u093e\u0966": "\u092e\u0941\u0930\u093e\u0926\u093e\u092c\u093e\u0926",
        "\u09090\u092a\u094d\u09300": "\u0909\u0924\u094d\u0924\u0930 \u092a\u094d\u0930\u0926\u0947\u0936",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    return value


def _not_stated(value: str | None) -> str:
    value = _compact(value)
    return value if value else "Not stated"


def _e(text: str | None) -> str:
    return html_escape(str(text or ""))


def _html_p(label: str, value: str | None) -> str:
    return f"<p><strong>{_e(label)}:</strong> {_e(_not_stated(value))}</p>"


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    head_html = "".join(f"<th>{_e(header)}</th>" for header in headers)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{_e(_compact(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><tr>{head_html}</tr>{body_html}</table>"


def _extract_form_values(artifact: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(artifact, dict):
        return {}

    values: dict[str, str] = {}
    for page in artifact.get("pages") or []:
        if not isinstance(page, dict):
            continue
        for raw_field in page.get("form_fields") or []:
            if not isinstance(raw_field, dict):
                continue
            label = _form_text(raw_field.get("name"))
            value = _form_text(raw_field.get("value"))
            if not label:
                continue
            key = _form_key_for_label(label)
            if not key or key in values:
                continue
            if not value:
                value = _value_after_label_colon(label)
            if value:
                values[key] = _compact(value)
    return values


def _form_text(payload: Any) -> str:
    if isinstance(payload, dict):
        return _compact(str(payload.get("text") or ""))
    return _compact(str(payload or ""))


def _value_after_label_colon(label: str) -> str:
    if ":" not in label:
        return ""
    return _compact(label.rsplit(":", 1)[-1])


def _form_key_for_label(label: str) -> str:
    key = _normalise_form_label(label)
    checks = [
        ("district", ("district unit", "district", "unit")),
        ("police_station", ("p s", "ps police station", "police station")),
        ("year", ("year",)),
        ("fir_no", ("fir no",)),
        ("fir_datetime", ("date and time of fir",)),
        ("day", ("day",)),
        ("date_from", ("date from",)),
        ("date_to", ("date to",)),
        ("time_period", ("time period",)),
        ("time_from", ("time from",)),
        ("time_to", ("time to",)),
        ("info_date", ("date",)),
        ("info_time", ("time",)),
        ("gd_entry", ("entry no", "entry number")),
    ]
    for canonical, aliases in checks:
        if any(key == alias or key.startswith(f"{alias} ") for alias in aliases):
            return canonical
    return ""


def _normalise_form_label(label: str) -> str:
    value = label.lower()
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"[^0-9a-z]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _basic_from_form_values(values: dict[str, str]) -> dict[str, str]:
    return {
        "district": values.get("district", ""),
        "police_station": values.get("police_station", ""),
        "year": _first_match(r"(\d{4})", values.get("year", "")),
        "fir_no": values.get("fir_no", ""),
        "fir_datetime": _normalise_datetime_value(values.get("fir_datetime", "")),
    }


def _occurrence_from_form_values(values: dict[str, str]) -> dict[str, str]:
    return {
        "day": values.get("day", ""),
        "date_from": _normalise_date_value(values.get("date_from", "")),
        "date_to": _normalise_date_value(values.get("date_to", "")),
        "time_period": values.get("time_period", ""),
        "time_from": _normalise_time_value(values.get("time_from", "")),
        "time_to": _normalise_time_value(values.get("time_to", "")),
        "info_date": _normalise_date_value(values.get("info_date", "")),
        "info_time": _normalise_time_value(values.get("info_time", "")),
        "gd_entry": values.get("gd_entry", ""),
    }


def _normalise_datetime_value(value: str) -> str:
    date = _normalise_date_value(value)
    time = _first_match(rf"({_TIME_RE})", value)
    suffix = "घंटे" if "घंटे" in value else ("बजे" if "बजे" in value else "")
    return _compact(" ".join(part for part in [date, time, suffix] if part))


def _normalise_date_value(value: str) -> str:
    return _first_match(f"({_DATE_RE})", value)


def _normalise_time_value(value: str) -> str:
    time = _first_match(rf"({_TIME_RE})", value)
    if not time:
        return ""
    suffix = "बजे" if "बजे" in value else ("घंटे" if "घंटे" in value else "")
    return _compact(f"{time} {suffix}")


def _merge_prefer_primary(
    primary: dict[str, str], fallback: dict[str, str]
) -> dict[str, str]:
    merged = dict(fallback)
    for key, value in primary.items():
        if _compact(value):
            merged[key] = value
    return merged


def _find_basic_details(full_text: str, item_1: str) -> dict[str, str]:
    search_text = full_text
    district = _first_match(r"District\s*/?\s*Unit.*?:\s*(.*?)\s+FIR\s+No", search_text)
    if not district:
        district = _first_match(
            r"District\s*/?\s*Unit.*?:\s*(.*?)\s+P\.?\s*S\.?\s*\(", search_text
        )
    if "FIR No" in district or "S.No" in district:
        district = _first_match(
            r"District\s*/?\s*Unit.*?:\s*(.*?)\s+FIR\s+No", search_text
        )
    if "P.S." in district or "Year" in district:
        district = _first_match(
            r"Year\s*\([^)]*\)\s*:\s*\d{4}\s+(.+?)\s+FIR\s+No", search_text
        )
    if not district:
        district = _first_match(r"Year.*?:\s*\d{4}\s+(.+?)\s+FIR\s+No", item_1)
    return {
        "district": district,
        "police_station": _first_match(
            r"P\.?\s*S\.?\s*\([^)]*(?:थाना)?[^)]*\)\s*:\s*(.*?)\s+Year",
            search_text,
        ),
        "year": _first_match(r"Year\s*\([^)]*\)\s*:\s*(\d{4})", search_text),
        "fir_no": _first_match(r"FIR\s+No\.[^:]*:\s*([A-Za-z0-9/-]+)", search_text),
        "fir_datetime": _first_match(
            rf"Date\s+and\s+Time\s+of\s+FIR.*?:\s*({_DATE_RE}\s+{_TIME_RE}\s*(?:घंटे|बजे)?)",
            search_text,
        ),
    }


def _extract_acts(section_text: str) -> list[FirActRow]:
    rows: list[FirActRow] = []
    if not section_text:
        return rows
    header = re.search(r"Sections\s*\(", section_text, re.IGNORECASE)
    if header:
        section_text = section_text[header.end() :]
    matches = re.finditer(
        r"(?:^|\n)\s*(\d{1,2})\s+(.*?)(?=(?:\n\s*\d{1,2}\s+भारतीय)|\n\s*3\s*[.)]|\Z)",
        section_text,
        re.DOTALL,
    )
    for match in matches:
        raw_row = _compact(match.group(2))
        section = ""
        parenthetical = re.search(r"(?<!\d)(\d{2,3})\s*\(\s*(\d+)\s*\)(?!\d)", raw_row)
        if parenthetical:
            section = f"{parenthetical.group(1)}({parenthetical.group(2)})"
        if not section:
            for candidate in re.findall(
                r"(?<!\d)(\d{2,3}(?:\s*\(\s*\d+\s*\))?)(?!\d)", raw_row
            ):
                normalized = re.sub(r"\s+", "", candidate)
                if normalized != "2023":
                    section = normalized
                    break
        if section:
            rows.append(
                FirActRow(match.group(1), "भारतीय न्याय संहिता (बी एन एस), 2023", section)
            )
    if rows:
        return rows
    seen: set[str] = set()
    for candidate in re.findall(
        r"(?<!\d)(\d{2,3}(?:\s*\(\s*\d+\s*\))?)(?!\d)", section_text
    ):
        normalized = re.sub(r"\s+", "", candidate)
        if normalized == "2023" or normalized in seen:
            continue
        seen.add(normalized)
        rows.append(
            FirActRow(
                str(len(rows) + 1), "भारतीय न्याय संहिता (बी एन एस), 2023", normalized
            )
        )
    return rows


def _extract_acts_from_layout(tables: list[LayoutTable]) -> list[FirActRow]:
    rows: list[FirActRow] = []
    for table in tables:
        columns = _layout_columns(table)
        for raw_row in table.rows:
            serial = _layout_cell(raw_row, columns, "serial") or str(len(rows) + 1)
            act = _layout_cell(raw_row, columns, "acts")
            section = _layout_cell(raw_row, columns, "sections")
            if _is_blank_table_row([serial, act, section]):
                continue
            if not section:
                section = _first_section_number(" ".join(cell.text for cell in raw_row))
            if not act and section:
                act = _default_bns_act()
            if section or act:
                rows.append(FirActRow(serial=serial, act=act, section=section))
    return rows


def _extract_accused_from_layout(tables: list[LayoutTable]) -> list[FirAccusedRow]:
    rows: list[FirAccusedRow] = []
    for table in tables:
        columns = _layout_columns(table)
        for raw_row in table.rows:
            serial = _layout_cell(raw_row, columns, "serial") or str(len(rows) + 1)
            name = _layout_cell(raw_row, columns, "name")
            alias = _layout_cell(raw_row, columns, "alias")
            father = _layout_cell(raw_row, columns, "relative")
            address = _layout_cell(raw_row, columns, "present_address")
            if _is_blank_table_row([serial, name, alias, father, address]):
                continue
            if _looks_like_table_header(" ".join([name, alias, father, address])):
                continue
            father = re.sub(r"^पिता\s+का\s+नाम\s*:?", "", father).strip()
            if name or father or address:
                rows.append(
                    FirAccusedRow(
                        serial=serial,
                        name=name,
                        father=father,
                        address=address,
                        alias=alias,
                    )
                )
    return rows


def _extract_properties_from_layout(tables: list[LayoutTable]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for table in tables:
        columns = _layout_columns(table)
        for raw_row in table.rows:
            serial = _layout_cell(raw_row, columns, "serial")
            row = {
                "serial": serial or str(len(rows) + 1),
                "category": _layout_cell(raw_row, columns, "property_category"),
                "type": _layout_cell(raw_row, columns, "property_type"),
                "description": _layout_cell(raw_row, columns, "description"),
                "value": _layout_cell(raw_row, columns, "value"),
            }
            if serial and not _is_blank_table_row(list(row.values())):
                rows.append(row)
    return rows


def _extract_inquest_from_layout(tables: list[LayoutTable]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for table in tables:
        columns = _layout_columns(table)
        for raw_row in table.rows:
            row = {
                "serial": _layout_cell(raw_row, columns, "serial")
                or str(len(rows) + 1),
                "uidb_number": _layout_cell(raw_row, columns, "uidb_number"),
            }
            if not _is_blank_table_row(list(row.values())):
                rows.append(row)
    return rows


def _layout_columns(table: LayoutTable) -> dict[str, int]:
    columns: dict[str, int] = {}
    for idx, header in enumerate(table.headers):
        key = _layout_header_key(header)
        if key and key not in columns:
            columns[key] = idx
    return columns


def _layout_cell(row: list[Any], columns: dict[str, int], key: str) -> str:
    idx = columns.get(key)
    if idx is None or idx >= len(row):
        return ""
    return _compact(getattr(row[idx], "text", ""))


def _layout_header_key(header: str) -> str:
    value = re.sub(r"\([^)]*\)", " ", header.lower())
    value = re.sub(r"[^0-9a-z\u0900-\u097f]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    checks = [
        ("serial", ["s no", "sno", "serial", "क्रम", "क्र सं"]),
        ("acts", ["acts", "act", "अधिनियम"]),
        ("sections", ["sections", "section", "धारा"]),
        ("alias", ["alias", "उपनाम"]),
        ("relative", ["relative", "father", "रिश्तेदार", "पिता"]),
        ("name", ["name", "नाम"]),
        ("present_address", ["present address", "address", "वर्तमान पता", "पता"]),
        ("property_category", ["property category", "सम्पत्ति श्रेणी", "संपत्ति श्रेणी"]),
        ("property_type", ["property type", "सम्पत्ति के प्रकार", "संपत्ति के प्रकार"]),
        ("description", ["description", "विवरण"]),
        ("value", ["value", "मूल्य"]),
        ("uidb_number", ["uidb", "u d b", "यू डी", "यू.डी"]),
    ]
    for key, needles in checks:
        if any(needle in value for needle in needles):
            return key
    return value


def _is_blank_table_row(values: list[str]) -> bool:
    meaningful = [
        _compact(value)
        for value in values
        if _compact(value) and not _compact(value).isdigit()
    ]
    return not meaningful


def _looks_like_table_header(value: str) -> bool:
    lowered = value.lower()
    return any(
        token in lowered for token in ["s. no", "name", "alias", "present address"]
    )


def _first_section_number(value: str) -> str:
    for candidate in re.findall(r"(?<!\d)(\d{2,3}(?:\s*\(\s*\d+\s*\))?)(?!\d)", value):
        normalized = re.sub(r"\s+", "", candidate)
        if normalized != "2023":
            return normalized
    return ""


def _default_bns_act() -> str:
    return "भारतीय न्याय संहिता (बी एन एस), 2023"


def _extract_occurrence(section_text: str) -> dict[str, str]:
    info_time_hits = re.findall(
        r"Time\s*\([^)]*समय[^)]*\)\s*:\s*(" + _TIME_RE + r"\s*(?:बजे)?)",
        section_text,
        flags=re.IGNORECASE,
    )
    date_hits = re.findall(_DATE_RE, section_text)
    time_hits = re.findall(_TIME_RE + r"\s*(?:बजे)?", section_text)
    time_period = _first_match(
        r"Time\s+Period.*?:\s*([^:\n]+?)(?:\s+Time\s+From|\n|$)", section_text
    )
    if "Time To" in time_period or "समय" in time_period:
        period_match = re.search(
            r"अवधि\)\s*:\s*([^:\n]+?)(?:\s+Time|\n|$)", section_text
        )
        time_period = _compact(period_match.group(1)) if period_match else ""
    return {
        "day": _first_match(
            r"Day\s*\([^)]*\)\s*:\s*([^\n:]+?)(?:\s+Date|\n|$)", section_text
        ),
        "date_from": _first_match(
            r"Date\s+from\s*\([^)]*\)\s*:\s*(" + _DATE_RE + ")", section_text
        )
        or (date_hits[0] if date_hits else ""),
        "date_to": _first_match(
            r"Date\s+To\s*\([^)]*\)\s*:\s*(" + _DATE_RE + ")", section_text
        )
        or (date_hits[1] if len(date_hits) > 1 else ""),
        "time_period": time_period,
        "time_from": _first_match(
            r"Time\s+From.*?:\s*(" + _TIME_RE + r"\s*(?:बजे)?)", section_text
        )
        or (time_hits[0] if time_hits else ""),
        "time_to": _first_match(
            r"Time\s+To.*?:\s*(" + _TIME_RE + r"\s*(?:बजे)?)", section_text
        )
        or (time_hits[1] if len(time_hits) > 1 else ""),
        "info_date": _first_match(
            r"Information\s+received.*?Date.*?:\s*(" + _DATE_RE + ")", section_text
        ),
        "info_time": _compact(info_time_hits[-1])
        if info_time_hits
        else (time_hits[-1] if len(time_hits) > 2 else ""),
        "gd_entry": _first_match(r"Entry\s+No\..*?:\s*([0-9]+)", section_text),
    }


def _extract_information_type(section_text: str) -> str:
    value = _first_match(r"Type\s+of\s+Information[^\n:]*:\s*([^\n]+)", section_text)
    if not value:
        value = _first_match(r"^\s*\([^)]*सूचना[^)]*\)\s*:\s*([^\n]+)", section_text)
    value = re.sub(r"\s*Date\s+and\s+Time.*$", "", value, flags=re.IGNORECASE).strip()
    value = value.split(" Date and Time", 1)[0].strip()
    return value


def _extract_place(section_text: str) -> dict[str, str]:
    beat_no = _first_match(r"Beat\s+No\..*?:\s*([^\n(]+)", section_text)
    direction_distance = _first_match(
        r"Direction\s+and\s+distance\s+from\s+P\.S\..*?:\s*(.*?)(?:Beat\s+No|\(\s*b\s*\)|Address|$)",
        section_text,
    )
    direction_distance = re.sub(r"\s+सं\.\)?$", "", direction_distance).strip()
    if (
        "Address" in beat_no
        or "ग्राम" in beat_no
        or beat_no == direction_distance
        or direction_distance.startswith(beat_no)
    ):
        beat_no = ""
    return {
        "direction_distance": direction_distance,
        "beat_no": beat_no,
        "address": _first_match(
            r"\(\s*b\s*\)\s*Address\s*\([^)]*\)\s*:\s*(.*?)(?:\s+\(\s*c\s*\)|\n\s*\(\s*c\s*\)|\s+6\s*[.)]|$)",
            section_text,
        ),
        "outside_ps": _first_match(
            r"Name\s+of\s+P\.S\..*?:\s*(.*?)(?:District|$)", section_text
        ),
    }


def _extract_complainant(section_text: str) -> dict[str, str]:
    addresses = _find_address_lines(section_text)
    occupation = _first_match(
        r"Occupation.*?:\s*(.*?)(?:\s+\(\s*i\s*\)|Address|$)", section_text
    )
    if occupation in {"(i)", "i"}:
        occupation = ""
    father = _first_match(
        r"Father'?s\s+Name.*?:\s*(.*?)(?:\s+\(\s*c\s*\)|Date)", section_text
    )
    father = re.sub(r"\s+\(\s*d\s*\)\s*Nationality.*$", "", father, flags=re.I).strip()
    nationality = _first_match(
        r"Nationality.*?:\s*(.*?)(?:Date\s+of\s+Issue|\s+\(\s*[ei]\s*\)|UID|Occupation|Address|$)",
        section_text,
    )
    if "भारत" in nationality:
        nationality = "भारत"
    return {
        "name": _first_match(
            r"Name\s*\([^)]*\)\s*:\s*(.*?)(?:\s+\(\s*b\s*\)|Father)", section_text
        ),
        "father": father,
        "dob_year": _first_match(
            r"Date\s*/\s*Year\s+of\s+Birth.*?:\s*([0-9]{4}|[0-9/-]+)", section_text
        ),
        "nationality": nationality,
        "occupation": occupation,
        "mobile": _first_match(r"Mobile.*?:\s*([0-9Xx+\-\s]+)", section_text),
        "present_address": addresses[0]
        if addresses
        else _first_match(
            r"1\s+वर्तमान\s+पता\s+(.*?)(?:\s+2\s+स्थायी|\n\s*2\s+स्थायी|$)",
            section_text,
        ),
        "permanent_address": addresses[1]
        if len(addresses) > 1
        else _first_match(
            r"2\s+स्थायी\s+पता\s+(.*?)(?:\s+\(\s*j\s*\)|Phone|$)",
            section_text,
        ),
    }


def _find_address_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    addresses: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "ग्राम" not in line:
            i += 1
            continue
        parts = [line]
        j = i + 1
        while j < len(lines) and "भारत" not in " ".join(parts):
            nxt = lines[j]
            if re.search(
                r"^(का प्रकार|वर्तमान पता|स्थायी पता|Phone|Mobile|\(\s*j\s*\))", nxt
            ):
                break
            parts.append(nxt)
            j += 1
        addr = _compact(" ".join(parts))
        if "भारत" in addr and addr not in addresses:
            addresses.append(addr)
        i = max(j, i + 1)
    return addresses[:2]


def _narrative_html(text: str) -> str:
    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    if not paragraphs:
        return "<p>Not stated</p>"
    return "".join(f"<p>{_e(p)}</p>" for p in paragraphs)


def _extract_narrative(text: str) -> str:
    section_text = _section_by_number(text, 12)
    if not section_text:
        return ""
    narrative = re.sub(
        r"^First\s+Information\s+contents?.*?:",
        "",
        section_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Preserve paragraph breaks — do NOT pass through _compact() which collapses newlines.
    return _rebuild_narrative(narrative).strip()


def _rebuild_narrative(text: str) -> str:
    paragraphs: list[str] = []
    current_tokens: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current_tokens:
                paragraphs.append(" ".join(current_tokens))
                current_tokens = []
        else:
            current_tokens.append(stripped)
    if current_tokens:
        paragraphs.append(" ".join(current_tokens))
    return "\n\n".join(p for p in paragraphs if p)


def _extract_free_text(section_text: str, section_num: int) -> str:
    if not section_text:
        return ""
    text = re.sub(rf"^\s*{section_num}\s*[.)]\s*", "", section_text).strip()
    text = re.sub(
        r"N\.?\s*C\.?\s*R\.?\s*B\.?\s*(?:\([^)]*\))?", "", text, flags=re.I
    ).strip()
    text = re.sub(r"^[^:]{0,180}:\s*", "", text, flags=re.DOTALL).strip()
    return "" if _is_only_form_labels(text) else _compact(text)


def _extract_properties(section_text: str) -> list[dict[str, str]]:
    if not section_text:
        return []
    text = _extract_free_text(section_text, 9)
    if not text:
        return []
    rows: list[dict[str, str]] = []
    row_pattern = re.compile(
        r"(?:^|\n|\s)(\d{1,2})\s+([^\n]+?)\s{2,}([^\n]+?)\s{2,}([^\n]+?)(?:\s{2,}([0-9,.]+))?(?=\n|\Z)",
        re.DOTALL,
    )
    for match in row_pattern.finditer(text):
        rows.append(
            {
                "serial": match.group(1),
                "category": _compact(match.group(2)),
                "type": _compact(match.group(3)),
                "description": _compact(match.group(4)),
                "value": _compact(match.group(5)),
            }
        )
    return rows


def _extract_total_property_value(section_text: str) -> str:
    value = _extract_free_text(section_text, 10)
    value = re.sub(r"Total\s+value\s+of\s+property.*?:", "", value, flags=re.I).strip()
    if re.search(r"Inquest\s+Report|UIDB\s+Number|U\.D\.\s+case", value, re.I):
        return ""
    return "" if _is_only_form_labels(value) else _compact(value)


def _extract_inquest(section_text: str) -> list[dict[str, str]]:
    text = _extract_free_text(section_text, 11)
    if not text:
        return []
    rows: list[dict[str, str]] = []
    for serial, uidb in re.findall(r"(?:^|\s)(\d{1,2})\s+([A-Za-z0-9/-]+)", text):
        rows.append({"serial": serial, "uidb_number": uidb})
    return rows


def _extract_dispatch_datetime(section_text: str) -> str:
    if not section_text:
        return ""
    date = _first_match(
        r"(" + _DATE_RE + r"(?:\s+" + _TIME_RE + r"\s*(?:\S+)?)?)", section_text
    )
    return "" if _is_only_form_labels(date) else _compact(date)


def _is_only_form_labels(text: str | None) -> bool:
    value = _compact(text).lower()
    if not value:
        return True
    label_words = {
        "not stated",
        "s. no.",
        "uidb number",
        "property category",
        "property type",
        "description",
        "value",
    }
    return value in label_words


def _extract_accused(
    section_text: str, narrative: str, place_address: str
) -> list[FirAccusedRow]:
    rows: list[FirAccusedRow] = []
    row_matches = list(
        re.finditer(
            r"(?:^|\n)\s*(\d{1,2})\s+([\u0900-\u097F][\s\S]*?)(?=\n\s*\d{1,2}\s+[\u0900-\u097F]|\n\s*Accused\s+More|\Z)",
            section_text,
        )
    )
    for match in row_matches:
        body = _compact(match.group(2))
        if any(word in body for word in ["S. No", "Name", "Alias", "Address Type"]):
            continue
        rel = re.search(r"पि[तप]ा\s+का\s+नाम\s*:?", body)
        if rel:
            name = _compact(body[: rel.start()])
            rest = _compact(body[rel.end() :])
            addr = ""
            addr_match = re.search(r"(?:1\.\s*)?ग्राम[-\s]", rest)
            if addr_match:
                father = _compact(rest[: addr_match.start()])
                addr = _compact(rest[addr_match.start() :])
            else:
                father = rest
            if name:
                rows.append(
                    FirAccusedRow(
                        str(len(rows) + 1), name, father, addr or place_address
                    )
                )
    if rows:
        return rows

    seen: set[tuple[str, str]] = set()
    suspect_text = narrative
    suspect_match = re.search(
        r"मौजूद\s+([\s\S]{0,500}?)\s+ने\s+",
        narrative,
    )
    if suspect_match:
        suspect_text = suspect_match.group(1)

    pattern = re.compile(
        r"([\u0900-\u097F]{2,}(?:\s+सिंह)?)" r"\s+पुत्र\s+([\u0900-\u097F]{2,}(?:\s+सिंह)?)"
    )
    for match in pattern.finditer(suspect_text):
        name = _compact(match.group(1))
        father = _compact(match.group(2))
        key = (name, father)
        if not name or key in seen:
            continue
        seen.add(key)
        rows.append(FirAccusedRow(str(len(rows) + 1), name, father, place_address))
    return rows


def _extract_action(section_text: str) -> dict[str, str]:
    action_text = re.split(r"\b14\.\s*Signature", section_text, maxsplit=1, flags=re.I)[
        0
    ]
    officer_blob = _first_match(r"(Name\s*\([^)]*\)\s*:.*)$", section_text)
    io_rank = _first_match(
        r"Rank\s*\([^)]*\)\s*:\s*(.*?)(?:\s+No\.|\n\s*No\.|$)",
        action_text,
    )
    if io_rank.endswith("/"):
        next_rank = _first_match(
            r"Rank\s*\([^)]*\)\s*:.*?\n\s*([^\n]+?निरीक्षक)", action_text
        )
        if next_rank and next_rank not in io_rank:
            io_rank = _compact(f"{io_rank} {next_rank}")
    io_name = _first_match(
        r"(?:Directed.*?Name\s+of\s+I\.O\..*?:|नाम\)\s*:)\s*(.*?)(?:\s+Rank|\n\s*Rank)",
        action_text,
    )
    if not io_name:
        embedded_name = re.search(r"नाम\)\s*:\s*([A-Za-z ]+)", io_rank)
        if embedded_name:
            io_name = _compact(embedded_name.group(1))
            io_rank = _compact(re.sub(r"नाम\)\s*:\s*[A-Za-z ]+", "", io_rank))
    return {
        "registered": "Yes"
        if re.search(r"Registered\s+the\s+case", action_text, re.I)
        else "",
        "io_name": io_name,
        "io_rank": io_rank,
        "io_no": _clean_value(
            _first_match(r"No\.\s*\([^)]*\)\s*:\s*([A-Za-z0-9/-]+)", action_text)
        ),
        "transferred_to": _first_match(
            r"Transferred\s+to\s+P\.S\..*?:\s*(.*?)(?:District|on\s+point|$)",
            action_text,
        ),
        "officer_name": _clean_officer_field(
            _first_match(r"Name\s*\([^)]*\)\s*:\s*(.*?)\s+Rank\s*\(", officer_blob)
        )
        or _first_match(r"Name\s*\([^)]*\)\s*:\s*(.*?)\s+Rank\s*\(", section_text),
        "officer_rank": _first_match(
            r"Rank\s*\([^)]*\)\s*:\s*(.*?)\s+No\.\s*\(", officer_blob
        )
        or _first_match(r"Rank\s*\([^)]*\)\s*:\s*(.*?)\s+No\.\s*\(", section_text),
        "officer_no": _first_match(r"No\.\s*\([^)]*\)\s*:\s*([0-9]+)", officer_blob),
    }


def _clean_value(value: str | None) -> str:
    value = _compact(value)
    return "" if value.lower() in {"name", "rank", "no", "to"} else value


def _clean_officer_field(value: str | None) -> str:
    value = _compact(value)
    value = re.sub(r"\s+14\.\s*Signature.*$", "", value, flags=re.I).strip()
    return _clean_value(value)


def _format_basic(data: dict[str, str]) -> str:
    if data.get("raw"):
        return data["raw"]
    return "\n".join(f"{key}: {value}" for key, value in data.items() if value)


def _format_dict(data: dict[str, str]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in data.items() if value)


def _format_signature(action: dict[str, str]) -> str:
    return "\n".join(
        line
        for line in [
            f"Name (नाम): {action.get('officer_name', '')}",
            f"Rank (पद): {action.get('officer_rank', '')}",
            f"No. (सं.): {action.get('officer_no', '')}",
        ]
        if line.split(": ", 1)[-1]
    )


def _physical_table_1(accused: list[FirAccusedRow]) -> str:
    rows = [[row.serial, "पुरुष", "", "", "-", "", "चेचक: नहीं"] for row in accused] or [
        ["", "", "", "", "", "", ""]
    ]
    return _html_table(
        [
            "S. No. (क्र.सं.)",
            "Sex (लिंग)",
            "Date/Year Of Birth (जन्म तिथि / वर्ष)",
            "Build (बनावट)",
            "Height cms (कद (से.मी.))",
            "Complexion (रंग)",
            "Identification Mark(s) (पहचान चिन्ह)",
        ],
        rows,
    )


def _physical_table_2(accused: list[FirAccusedRow]) -> str:
    rows = [["", "", "", "", "", "", ""] for _ in accused] or [
        ["", "", "", "", "", "", ""]
    ]
    return _html_table(
        [
            "Deformities / Peculiarities (विकृतियाँ / विशिष्टताएँ)",
            "Teeth (दाँत)",
            "Hair (बाल)",
            "Eye (आँखें)",
            "Habit(s) (आदतें)",
            "Dress Habit(s) (पहनावा)",
            "Language / Dialect (भाषा/बोली)",
        ],
        rows,
    )


def _physical_table_3(accused: list[FirAccusedRow]) -> str:
    rows = [["", "", "", "", "", "", ""] for _ in accused] or [
        ["", "", "", "", "", "", ""]
    ]
    return _html_table(
        [
            "Place of (का स्थान)",
            "Burn Mark (जले हुए का निशान)",
            "Leucodema (लुकोदेर्मा (सफेद धब्बे))",
            "Mole (मस्सा)",
            "Scar (घाव)",
            "Tattoo (गूदे हुए का)",
            "Others (अन्य)",
        ],
        rows,
    )
