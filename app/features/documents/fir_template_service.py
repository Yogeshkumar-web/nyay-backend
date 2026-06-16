from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.features.documents.fir_reconstruction import (
    FirSchema,
    reconstruct_fir,
    render_fir_schema_html,
)
from app.features.documents.models import Document


@dataclass(frozen=True)
class FirTemplateResult:
    schema: FirSchema
    html: str
    notes: str


class FirTemplateService:
    """Deterministic FIR typing: OCR text/artifact -> schema -> fixed HTML."""

    def build_typed_version(
        self,
        document: Document,
        *,
        audit: dict[str, object] | None = None,
    ) -> FirTemplateResult:
        schema = self.extract_schema(document)
        html = self.render_html(schema)
        notes = self.build_notes(schema, audit=audit)
        return FirTemplateResult(schema=schema, html=html, notes=notes)

    def extract_schema(self, document: Document) -> FirSchema:
        text = document.ocr_raw_text or ""
        schema = reconstruct_fir(text, ocr_artifact=document.ocr_artifact)
        if schema is not None:
            return schema

        fallback = FirSchema(
            fir_contents=text,
            missing_required_fields=[
                "basic.fir_no",
                "basic.police_station",
                "basic.year",
                "basic.fir_datetime",
                "acts",
                "accused",
            ],
            source_map={
                "basic": "text_fallback",
                "acts": "text_fallback",
                "accused": "text_fallback",
                "narrative": "text_fallback",
            },
            confidence_score=0.2 if text.strip() else 0.0,
        )
        return fallback

    def render_html(self, schema: FirSchema) -> str:
        return render_fir_schema_html(schema)

    def build_notes(
        self,
        schema: FirSchema,
        *,
        audit: dict[str, object] | None = None,
    ) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        missing = ",".join(schema.missing_required_fields) or "none"
        sources = ",".join(
            f"{key}:{value}" for key, value in sorted(schema.source_map.items())
        )
        audit_notes = " ".join(
            f"| {key}={value}"
            for key, value in sorted((audit or {}).items())
            if value is not None
        )
        base = (
            f"ts={timestamp} | pipeline=fir_template"
            f" | confidence={schema.confidence_score:.2f}"
            f" | missing={missing}"
            f" | sources={sources or 'none'}"
        )
        if audit_notes:
            base = f"{base} {audit_notes}"
        return f"{base} | ai_cleanup=false"
