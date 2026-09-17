from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal, TypeAlias

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import settings
from app.features.documents.providers.contracts import (
    DocumentProviderError,
    ProviderErrorCategory,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActsSectionsEntry(StrictModel):
    act_name: str
    section: str


class PersonBasicDetails(StrictModel):
    name: str
    fathers_or_husbands_name: str | None = None
    dob_or_year: str | None = None
    nationality: str | None = None
    sex: str | None = None
    occupation: str | None = None
    religion: str | None = None
    sc_st_obc: str | None = None


class AddressEntry(StrictModel):
    address_type: str
    address: str


class IdDetail(StrictModel):
    id_type: str
    id_number: str


class IdentificationDetails(StrictModel):
    uid_no: str | None = None
    passport_no: str | None = None
    passport_issue_date: str | None = None
    passport_issue_place: str | None = None
    id_details: list[IdDetail] | None = None


class PhysicalFeatures(StrictModel):
    sex: str | None = None
    build: str | None = None
    height_cm: str | None = None
    complexion: str | None = None
    identification_marks: str | None = None
    deformities: str | None = None
    teeth: str | None = None
    hair: str | None = None
    eye: str | None = None
    habits: str | None = None
    dress_habits: str | None = None
    language_dialect: str | None = None
    burn_mark: str | None = None
    leucoderma: str | None = None
    mole: str | None = None
    scar: str | None = None
    tattoo: str | None = None
    others: str | None = None


class HeaderBlock(StrictModel):
    district_unit: str
    police_station: str
    year: int
    fir_number: str


class FirOccurrence(StrictModel):
    day: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    time_period: str | None = None
    time_from: str | None = None
    time_to: str | None = None
    info_received_date: str | None = None
    info_received_time: str | None = None
    general_diary_entry_no: str | None = None
    general_diary_date_time: str | None = None


class PlaceOfOccurrence(StrictModel):
    direction_distance_from_ps: str | None = None
    beat_no: str | None = None
    address: str | None = None
    outside_ps_limit_name: str | None = None
    district_state: str | None = None


class FirComplainant(StrictModel):
    details: PersonBasicDetails
    identification: IdentificationDetails
    addresses: list[AddressEntry] = Field(default_factory=list)
    phone_number: str | None = None
    mobile_number: str | None = None


class FirAccused(StrictModel):
    name: str
    alias: str | None = None
    relatives_name: str | None = None
    present_address: str | None = None


class FirActionTaken(StrictModel):
    registered_and_investigation_taken_up: bool | None = None
    directed_io_name: str | None = None
    directed_io_rank: str | None = None
    directed_io_no: str | None = None
    refused_investigation_reason: str | None = None
    transferred_to_ps: str | None = None
    transferred_to_district: str | None = None
    fir_read_over_confirmed: bool | None = None


class Officer(StrictModel):
    name: str | None = None
    rank: str | None = None
    no: str | None = None


class FirPhysicalFeatureEntry(StrictModel):
    sl_no: int
    dob_or_year: str | None = None
    features: PhysicalFeatures


class FirExtraction(StrictModel):
    header: HeaderBlock
    fir_date_time: str
    acts_sections: list[ActsSectionsEntry] = Field(default_factory=list)
    occurrence: FirOccurrence
    type_of_information: str | None = None
    place_of_occurrence: PlaceOfOccurrence
    complainant: FirComplainant
    accused_more_than_count: int | None = None
    accused: list[FirAccused] = Field(default_factory=list)
    delay_reason: str | None = None
    properties_of_interest: list[dict[str, Any]] | None = None
    total_property_value: str | None = None
    inquest_report_ud_case_no: str | None = None
    fir_contents_narrative: str
    action_taken: FirActionTaken
    officer_in_charge: Officer
    dispatch_to_court_datetime: str | None = None
    physical_features: list[FirPhysicalFeatureEntry] = Field(default_factory=list)


class InvestigatingOfficer(StrictModel):
    name: str | None = None
    rank: str | None = None
    no: str | None = None


class ChargesheetComplainant(StrictModel):
    details: PersonBasicDetails


class PropertyRecovered(StrictModel):
    s_no: int
    property_description: str | None = None
    estimated_value: str | None = None
    ps_property_register_no: str | None = None
    recovered_from_where: str | None = None
    disposal: str | None = None


class AccusedChargesheeted(StrictModel):
    details: PersonBasicDetails
    whether_verified: bool | None = None
    identification: IdentificationDetails
    addresses: list[AddressEntry] = Field(default_factory=list)
    regular_criminal_no: str | None = None
    date_of_arrest: str | None = None
    date_of_release_on_bail: str | None = None
    date_forwarded_to_court: str | None = None
    acts_sections: list[ActsSectionsEntry] = Field(default_factory=list)
    bailers_sureties: str | None = None
    previous_convictions: str | None = None
    status_of_accused: str | None = None
    brief_crime_details: str | None = None
    crime_evidence: str | None = None
    is_charged: Literal[True]


class AccusedNotChargesheeted(StrictModel):
    details: PersonBasicDetails
    whether_verified: bool | None = None
    addresses: list[AddressEntry] = Field(default_factory=list)
    suspicion_approved: bool | None = None
    status_of_accused: str | None = None
    special_remarks_reason: str | None = None
    is_charged: Literal[False]


class Witness(StrictModel):
    s_no: int
    name: str | None = None
    mobile_no: str | None = None
    fathers_or_husbands_name: str | None = None
    dob_or_year: str | None = None
    occupation: str | None = None
    address: str | None = None
    evidence_type: str | None = None


class NamedAddressAge(StrictModel):
    name: str
    address: str | None = None
    age: str | None = None


class NotChallanedAccused(StrictModel):
    suspected: list[NamedAddressAge] | None = None
    absconding: list[NamedAddressAge] | None = None


class ChallanedAccusedStatus(StrictModel):
    on_bail: list[NamedAddressAge] | None = None
    absconding_mafarur: list[NamedAddressAge] | None = None


class Punishment(StrictModel):
    date: str | None = None
    place: str | None = None
    punishment: str | None = None


class SubmittingOfficer(StrictModel):
    name: str | None = None
    rank: str | None = None
    pno_cug_no: str | None = None


class ChargesheetExtraction(StrictModel):
    header: HeaderBlock
    fir_date: str
    chargesheet_no: str
    chargesheet_date: str
    acts_sections: list[ActsSectionsEntry] = Field(default_factory=list)
    report_type: str
    fr_unoccurred: str | None = None
    relation_type: str
    investigating_officer: InvestigatingOfficer
    complainant: ChargesheetComplainant
    properties_recovered: list[PropertyRecovered] = Field(default_factory=list)
    accused_chargesheeted: list[AccusedChargesheeted] = Field(default_factory=list)
    accused_suspects_not_chargesheeted: list[AccusedNotChargesheeted] = Field(
        default_factory=list
    )
    witnesses: list[Witness] = Field(default_factory=list)
    fr_false_action: str | None = None
    lab_analysis_result: str | None = None
    brief_facts_narrative: str
    refer_notice_served: bool | None = None
    despatched_on: str | None = None
    enclosures_count: str | None = None
    enclosures_list: str | None = None
    not_challaned_accused: NotChallanedAccused
    challaned_accused_status: ChallanedAccusedStatus
    case_property: str | None = None
    trial_result: str | None = None
    punishment: Punishment | None = None
    officer_in_charge: SubmittingOfficer
    submitting_investigating_officer: SubmittingOfficer


LegalFormExtraction: TypeAlias = FirExtraction | ChargesheetExtraction
KnownFormType: TypeAlias = Literal["fir", "chargesheet"]


def classify_iif_form(text: str) -> KnownFormType | None:
    normalized = re.sub(r"[^A-Z0-9]+", " ", text.upper())
    if "FIRST INFORMATION REPORT" in normalized and re.search(
        r"UNDER SECTION 173 B N S S", normalized
    ):
        return "fir"
    if "FINAL FORM REPORT" in normalized and re.search(
        r"UNDER SECTION 193 B N S S", normalized
    ):
        return "chargesheet"
    return None


class SarvamLegalFormExtractor:
    provider_key = "sarvam_structured_extraction"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.base_url = (base_url or settings.SARVAM_BASE_URL).rstrip("/")
        self.model = model or settings.SARVAM_REASONING_MODEL
        self.max_tokens = max_tokens or settings.SARVAM_REASONING_MAX_TOKENS
        self.timeout_seconds = timeout_seconds or settings.SARVAM_REASONING_TIMEOUT_SECONDS

    async def extract(
        self, text: str, *, form_type: KnownFormType
    ) -> tuple[LegalFormExtraction, dict[str, Any]]:
        if not self.api_key:
            raise self._error("SARVAM_API_KEY is not configured.", retryable=False)
        model_class = FirExtraction if form_type == "fir" else ChargesheetExtraction
        schema = _strict_json_schema(model_class.model_json_schema())
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._post(self._payload(text, form_type, schema))
                content = _message_content(response)
                parsed = json.loads(content)
                validated = model_class.model_validate(parsed)
                return validated, {
                    "schema_version": 1,
                    "status": "validated",
                    "provider": self.provider_key,
                    "model": response.get("model") or self.model,
                    "attempts": attempt + 1,
                    "usage": response.get("usage"),
                    "result": validated.model_dump(mode="json"),
                }
            except (json.JSONDecodeError, ValidationError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.25)
                    continue
                break
        raise self._error(
            "Sarvam returned invalid structured legal-form JSON twice; manual review is required.",
            retryable=False,
        ) from last_error

    def _payload(
        self,
        text: str,
        form_type: KnownFormType,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        label = (
            "Indian police FIR (I.I.F.-I under Section 173 B.N.S.S.)"
            if form_type == "fir"
            else "Indian police chargesheet (I.I.F.-V under Section 193 B.N.S.S.)"
        )
        instruction = (
            f"Task: Extract structured data from this {label}.\n"
            "Constraints: Use only the supplied OCR text. Bilingual Hindi/English labels may be "
            "split across lines; treat each printed label as one semantic field. Preserve names, "
            "dates, sections and narratives exactly. Blank fields must be null or empty arrays as "
            "allowed by the schema. Never invent placeholder text. Keep charged accused and suspects "
            "not chargesheeted in separate arrays and set is_charged exactly as required. Return only "
            "JSON matching the schema.\nEvidence/Input:\n"
            f"{text}"
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a source-grounded Indian legal document extraction engine.",
                },
                {"role": "user", "content": instruction},
            ],
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{form_type}_structured_extraction",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if settings.SARVAM_REASONING_EFFORT:
            payload["reasoning_effort"] = settings.SARVAM_REASONING_EFFORT
        return payload

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers={
                        "api-subscription-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise self._error("Sarvam structured extraction timed out.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise self._error("Sarvam structured extraction request failed.", retryable=True) from exc
        if response.status_code >= 400:
            try:
                details = response.json()
                message = details.get("error", {}).get("message") or str(details)
            except ValueError:
                message = response.text
            raise self._error(
                f"Sarvam structured extraction failed ({response.status_code}): {message}",
                retryable=response.status_code in {408, 429, 500, 502, 503, 504},
            )
        return response.json()

    @staticmethod
    def _error(message: str, *, retryable: bool) -> DocumentProviderError:
        return DocumentProviderError(
            message,
            category=ProviderErrorCategory.invalid_response,
            retryable=retryable,
        )


def render_legal_form_markdown(result: LegalFormExtraction) -> str:
    if isinstance(result, FirExtraction):
        return _render_fir(result)
    return _render_chargesheet(result)


def _render_fir(fir: FirExtraction) -> str:
    lines = [
        "# FIRST INFORMATION REPORT (I.I.F.-I)",
        "",
        "## FIR Details",
        "",
        _kv_table(
            [
                ("District / Unit", fir.header.district_unit),
                ("Police Station", fir.header.police_station),
                ("Year", fir.header.year),
                ("FIR Number", fir.header.fir_number),
                ("FIR Date and Time", fir.fir_date_time),
                ("Type of Information", fir.type_of_information),
            ]
        ),
        "",
        "## Acts and Sections",
        "",
        _table(["Act", "Section"], [[x.act_name, x.section] for x in fir.acts_sections]),
        "",
        "## Occurrence of Offence",
        "",
        _kv_table(_model_rows(fir.occurrence)),
        "",
        "## Place of Occurrence",
        "",
        _kv_table(_model_rows(fir.place_of_occurrence)),
        "",
        "## Complainant / Informant",
        "",
        _kv_table(_person_rows(fir.complainant.details)),
        "",
        _addresses_table(fir.complainant.addresses),
        "",
        "## Accused",
        "",
        _table(
            ["Name", "Alias", "Relative's Name", "Present Address"],
            [[x.name, x.alias, x.relatives_name, x.present_address] for x in fir.accused],
        ),
        "",
        "## FIR Contents / Narrative",
        "",
        fir.fir_contents_narrative,
        "",
        "## Action Taken",
        "",
        _kv_table(_model_rows(fir.action_taken)),
        "",
        "## Officer in Charge",
        "",
        _kv_table(_model_rows(fir.officer_in_charge)),
    ]
    return _clean_markdown(lines)


def _render_chargesheet(cs: ChargesheetExtraction) -> str:
    lines = [
        "# FINAL FORM / REPORT (I.I.F.-V)",
        "",
        "## Chargesheet Details",
        "",
        _kv_table(
            [
                ("District / Unit", cs.header.district_unit),
                ("Police Station", cs.header.police_station),
                ("Year", cs.header.year),
                ("FIR Number", cs.header.fir_number),
                ("FIR Date", cs.fir_date),
                ("Chargesheet Number", cs.chargesheet_no),
                ("Chargesheet Date", cs.chargesheet_date),
                ("Report Type", cs.report_type),
                ("Relation Type", cs.relation_type),
            ]
        ),
        "",
        "## Acts and Sections",
        "",
        _table(["Act", "Section"], [[x.act_name, x.section] for x in cs.acts_sections]),
        "",
        "## Investigating Officer",
        "",
        _kv_table(_model_rows(cs.investigating_officer)),
        "",
        "## Complainant",
        "",
        _kv_table(_person_rows(cs.complainant.details)),
        "",
        "## Accused Chargesheeted",
        "",
        _table(
            ["Name", "Father / Husband", "Status", "Arrest Date", "Charged"],
            [
                [
                    x.details.name,
                    x.details.fathers_or_husbands_name,
                    x.status_of_accused,
                    x.date_of_arrest,
                    "Yes",
                ]
                for x in cs.accused_chargesheeted
            ],
        ),
        "",
        "## Suspects Not Chargesheeted",
        "",
        _table(
            ["Name", "Father / Husband", "Status", "Reason", "Charged"],
            [
                [
                    x.details.name,
                    x.details.fathers_or_husbands_name,
                    x.status_of_accused,
                    x.special_remarks_reason,
                    "No",
                ]
                for x in cs.accused_suspects_not_chargesheeted
            ],
        ),
        "",
        "## Witnesses",
        "",
        _table(
            ["S. No.", "Name", "Mobile", "Father / Husband", "Address", "Evidence Type"],
            [
                [x.s_no, x.name, x.mobile_no, x.fathers_or_husbands_name, x.address, x.evidence_type]
                for x in cs.witnesses
            ],
        ),
        "",
        "## Properties Recovered",
        "",
        _table(
            ["S. No.", "Description", "Estimated Value", "Register No.", "Recovered From", "Disposal"],
            [
                [
                    x.s_no,
                    x.property_description,
                    x.estimated_value,
                    x.ps_property_register_no,
                    x.recovered_from_where,
                    x.disposal,
                ]
                for x in cs.properties_recovered
            ],
        ),
        "",
        "## Brief Facts of the Case",
        "",
        cs.brief_facts_narrative,
        "",
        "## Submission",
        "",
        _kv_table(
            [
                ("Notice Served", cs.refer_notice_served),
                ("Despatched On", cs.despatched_on),
                ("Enclosures Count", cs.enclosures_count),
                ("Enclosures", cs.enclosures_list),
                ("Case Property", cs.case_property),
            ]
        ),
        "",
        "## Submitting Investigating Officer",
        "",
        _kv_table(_model_rows(cs.submitting_investigating_officer)),
    ]
    return _clean_markdown(lines)


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema = json.loads(json.dumps(schema))

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties)
                node["additionalProperties"] = False
            node.pop("default", None)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(schema)
    return schema


def _message_content(response: dict[str, Any]) -> str:
    content = response["choices"][0]["message"]["content"]
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    raise ValueError("Sarvam response message content was empty.")


def _person_rows(person: PersonBasicDetails) -> list[tuple[str, Any]]:
    return _model_rows(person)


def _model_rows(model: BaseModel) -> list[tuple[str, Any]]:
    return [
        (name.replace("_", " ").title(), value)
        for name, value in model.model_dump(mode="json").items()
        if value not in (None, "", [], {})
    ]


def _addresses_table(addresses: list[AddressEntry]) -> str:
    return _table(
        ["Address Type", "Address"],
        [[item.address_type, item.address] for item in addresses],
    )


def _kv_table(rows: list[tuple[str, Any]]) -> str:
    return _table(["Field", "Value"], [[label, value] for label, value in rows])


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_No entries recorded._"
    rendered = [
        "| " + " | ".join(_cell(value) for value in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    rendered.extend(
        "| " + " | ".join(_cell(value) for value in row) + " |" for row in rows
    )
    return "\n".join(rendered)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).replace("|", "\\|").replace("\n", "<br>").strip()


def _clean_markdown(lines: list[str]) -> str:
    return "\n".join(str(line).rstrip() for line in lines).strip() + "\n"
