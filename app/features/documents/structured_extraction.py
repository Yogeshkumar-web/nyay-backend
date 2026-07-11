from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings


class StructuredExtractionError(RuntimeError):
    pass


class ExtractedPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(..., ge=1)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)


class ExtractedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    statement_type: str = "unknown"
    source_pages: list[int] = Field(default_factory=list)
    paragraph_number: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ExtractedDate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str
    normalized_date: str | None = None
    context: str = ""
    source_pages: list[int] = Field(default_factory=list)
    warning: str | None = None


class ExtractedName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str
    role: str = "unknown"
    normalized_name: str | None = None
    source_pages: list[int] = Field(default_factory=list)
    warning: str | None = None


class FormattingPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_type: Literal["heading", "paragraph", "numbered_paragraph", "table", "note"]
    text: str
    source_pages: list[int] = Field(default_factory=list)
    style_hint: str | None = None


class StructuredExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str = "unknown"
    pages: list[ExtractedPage] = Field(default_factory=list)
    statements: list[ExtractedStatement] = Field(default_factory=list)
    dates: list[ExtractedDate] = Field(default_factory=list)
    names: list[ExtractedName] = Field(default_factory=list)
    unclear_words: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    formatting_plan: list[FormattingPlanItem] = Field(default_factory=list)


class SarvamStructuredExtractor:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ):
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.base_url = (base_url or settings.SARVAM_BASE_URL).rstrip("/")
        self.model = model or settings.SARVAM_REASONING_MODEL
        self.reasoning_effort = reasoning_effort or settings.SARVAM_REASONING_EFFORT
        self.max_tokens = max_tokens or settings.SARVAM_REASONING_MAX_TOKENS
        self.timeout_seconds = (
            timeout_seconds or settings.SARVAM_REASONING_TIMEOUT_SECONDS
        )

    async def extract(
        self,
        stitched_text: str,
        *,
        document_type_hint: str | None = None,
        page_artifact: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise StructuredExtractionError("SARVAM_API_KEY is not configured.")
        if not stitched_text.strip():
            raise StructuredExtractionError("Cannot extract structure from empty text.")

        payload = self._build_payload(
            stitched_text,
            document_type_hint=document_type_hint,
            page_artifact=page_artifact,
        )
        response = await self._post_chat_completion(payload)
        content = _extract_message_content(response)
        try:
            parsed = json.loads(content)
            validated = StructuredExtractionResult.model_validate(parsed)
        except Exception as exc:
            raise StructuredExtractionError(
                "Sarvam structured extraction returned invalid JSON."
            ) from exc

        return {
            "schema_version": 1,
            "provider": "sarvam_chat_completions",
            "model": response.get("model") or self.model,
            "reasoning_effort": self.reasoning_effort,
            "result": validated.model_dump(),
            "raw_response": _redact_reasoning(response),
            "usage": response.get("usage"),
        }

    def _build_payload(
        self,
        stitched_text: str,
        *,
        document_type_hint: str | None,
        page_artifact: dict[str, Any] | None,
    ) -> dict[str, Any]:
        schema = StructuredExtractionResult.model_json_schema()
        user_content = {
            "document_type_hint": document_type_hint or "unknown",
            "stitched_text": stitched_text,
            "page_trace": (page_artifact or {}).get("stitching", {}).get("pages", []),
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_content, ensure_ascii=False),
                },
            ],
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "legal_document_structured_extraction",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    async def _post_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds)
        ) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "api-subscription-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            try:
                details = response.json()
            except ValueError:
                details = {"error": {"message": response.text}}
            message = details.get("error", {}).get("message") or str(details)
            raise StructuredExtractionError(
                f"Sarvam structured extraction failed: {message}"
            )
        return response.json()


def _extract_message_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise StructuredExtractionError(
            "Sarvam structured extraction response is missing message content."
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise StructuredExtractionError(
            "Sarvam structured extraction response content was empty."
        )
    return content


def _redact_reasoning(response: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(response))
    for choice in sanitized.get("choices", []):
        message = choice.get("message") if isinstance(choice, dict) else None
        if isinstance(message, dict):
            message.pop("reasoning_content", None)
    return sanitized


_SYSTEM_PROMPT = """
You extract structured data from Indian legal OCR text.

Rules:
- Return only JSON matching the supplied schema.
- Use only facts present in stitched_text and page_trace.
- Preserve source page references from [[PAGE N START]] markers.
- Do not infer missing names, dates, sections, or events.
- Put uncertainty in warnings or unclear_words.
- Keep paragraph boundaries conservative.
- formatting_plan should describe how a reviewed DOCX can be generated later.
""".strip()
