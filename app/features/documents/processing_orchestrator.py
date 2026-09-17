from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.features.documents.digital_extractor import (
    DOCX_MIME,
    IMAGE_MIME_TYPES,
    PDF_MIME,
    build_single_image_page,
    extract_docx,
    render_pdf_pages,
)
from app.features.documents.canonical_document import build_canonical_document
from app.features.documents.document_classifier import classify_document
from app.features.documents.google_document_ai import GoogleDocumentAiProcessor
from app.features.documents.legal_form_extraction import (
    SarvamLegalFormExtractor,
    classify_iif_form,
    render_legal_form_markdown,
)
from app.features.documents.models import (
    DocReviewStatus,
    Document,
    DocumentType,
    DocumentExtractionMethod,
    DocumentPage,
    DocumentPageClassification,
    DocumentPageStatus,
    DocumentProcessingRun,
    OcrStatus,
    ProcessingRoute,
    ProcessingRunStatus,
    ProcessingStatus,
)
from app.features.documents.page_stitching import stitch_ocr_pages
from app.features.documents.providers.contracts import (
    DocumentProviderError,
    DocumentTypingProvider,
    ExtractedPageText,
    PageInput,
    VisionTextExtractor,
)
from app.features.documents.repository import DocumentRepository


@dataclass(frozen=True)
class PipelineExecutionResult:
    document_id: uuid.UUID
    run_id: uuid.UUID
    route: ProcessingRoute
    page_count: int
    typed_revision_id: uuid.UUID


class DocumentProcessingOrchestrator:
    def __init__(
        self,
        db: AsyncSession,
        *,
        vision_provider: VisionTextExtractor,
        typing_provider: DocumentTypingProvider,
        document_ocr_provider: GoogleDocumentAiProcessor | None = None,
        legal_form_extractor: SarvamLegalFormExtractor | None = None,
    ) -> None:
        self.db = db
        self.repo = DocumentRepository(db)
        self.vision_provider = vision_provider
        self.typing_provider = typing_provider
        self.document_ocr_provider = document_ocr_provider
        self.legal_form_extractor = legal_form_extractor or SarvamLegalFormExtractor()

    async def process(
        self,
        *,
        doc: Document,
        run: DocumentProcessingRun,
        file_bytes: bytes,
    ) -> PipelineExecutionResult:
        classification = classify_document(file_bytes, doc.mime_type)
        if doc.mime_type == PDF_MIME and self.document_ocr_provider is not None:
            return await self._process_pdf_as_whole_document(
                doc=doc,
                run=run,
                file_bytes=file_bytes,
                page_count=classification.pdf.page_count if classification.pdf else 0,
                classification_details=classification.details,
            )
        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.splitting_pages,
            metadata={
                **(run.metadata_ or {}),
                "processing_route": classification.route.value,
            },
        )
        await self.db.commit()

        page_rows, scanned_inputs = await self._inventory_pages(
            doc=doc,
            run=run,
            file_bytes=file_bytes,
            route=classification.route,
            pdf=classification.pdf,
        )
        run.total_pages = len(page_rows)
        run.vision_provider_key = self.vision_provider.provider_key
        run.typing_provider_key = self.typing_provider.provider_key
        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.extracting_pages,
            metrics={
                "total_pages": len(page_rows),
                "digital_pages": sum(
                    page.classification == DocumentPageClassification.digital
                    for page in page_rows
                ),
                "scanned_pages": len(scanned_inputs),
            },
        )
        await self.db.commit()

        extracted_pages = await self._extract_pages(
            run=run,
            page_rows=page_rows,
            scanned_inputs=scanned_inputs,
        )
        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.typing_pages,
        )
        typing_inputs = [
            page
            for page in extracted_pages
            if page.method != DocumentExtractionMethod.none.value
        ]
        for page in page_rows:
            if page.status != DocumentPageStatus.blank:
                page.status = DocumentPageStatus.typing
        await self.db.commit()

        typed_pages = (
            await self.typing_provider.type_pages(
                typing_inputs,
                document_type_hint=doc.document_type.value,
                idempotency_key=f"{run.id}:typing",
            )
            if typing_inputs
            else []
        )
        typed_by_id = {page.page_id: page for page in typed_pages}
        expected_typed_ids = {
            page.id
            for page in page_rows
            if page.status != DocumentPageStatus.blank
        }
        if set(typed_by_id) != expected_typed_ids:
            raise RuntimeError("Typing provider output did not match the page inventory.")
        for page in page_rows:
            if page.status == DocumentPageStatus.blank:
                continue
            typed = typed_by_id[page.id]
            await self.repo.save_page_typing(
                page,
                typed_markdown=typed.typed_markdown,
                warnings=list(typed.warnings),
                provider_key=typed.provider_key,
                provider_version=typed.provider_version,
                provider_artifact=dict(typed.provider_metadata),
                typed_hash=_sha256_text(typed.typed_markdown),
            )
        run.completed_pages = len(page_rows)
        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.stitching_pages,
        )
        await self.db.commit()

        return await self._finalize_run(
            doc=doc,
            run=run,
            page_rows=page_rows,
            route=classification.route,
            extracted_pages=extracted_pages,
            classification_details=classification.details,
        )

    async def _process_pdf_as_whole_document(
        self,
        *,
        doc: Document,
        run: DocumentProcessingRun,
        file_bytes: bytes,
        page_count: int,
        classification_details: dict,
    ) -> PipelineExecutionResult:
        if page_count < 1:
            raise RuntimeError("PDF page count could not be determined.")
        provider = self.document_ocr_provider
        if provider is None:
            raise RuntimeError("Whole-document OCR provider is not configured.")

        run.total_pages = page_count
        run.vision_provider_key = provider.provider_key
        run.typing_provider_key = self.typing_provider.provider_key
        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.extracting_pages,
            metadata={
                **(run.metadata_ or {}),
                "processing_route": ProcessingRoute.scanned_ocr.value,
                "ocr_mode": "google_document_ai_batch_whole_pdf",
                "native_pdf_parsing": False,
                "language_hint": "hi",
            },
            metrics={"total_pages": page_count, "batch_documents": 1},
        )
        page_rows = await self._inventory_whole_pdf_pages(
            doc=doc,
            run=run,
            file_bytes=file_bytes,
            page_count=page_count,
        )
        await self.db.commit()

        ocr = await provider.process_pdf(
            file_bytes,
            filename=doc.original_filename,
            idempotency_key=f"{run.id}:google-document-ai",
        )
        by_number = {page.page_number: page for page in ocr.pages}
        if set(by_number) != {page.page_number for page in page_rows}:
            raise RuntimeError("Google Document AI output did not match the PDF page inventory.")

        extracted_pages: list[ExtractedPageText] = []
        for row in page_rows:
            page = by_number[row.page_number]
            extracted = ExtractedPageText(
                page_id=row.id,
                page_number=row.page_number,
                text=page.text,
                method=DocumentExtractionMethod.vision_ocr.value,
                confidence=page.confidence,
                language=page.language,
                warnings=page.warnings,
                provider_key=provider.provider_key,
                provider_version="document_ocr_batch_v1",
                provider_job_id=ocr.operation_name,
                provider_metadata=page.artifact,
            )
            extracted_pages.append(extracted)
            await self.repo.save_page_extraction(
                row,
                extracted_text=extracted.text,
                extraction_method=DocumentExtractionMethod.vision_ocr,
                provider_key=extracted.provider_key,
                provider_version=extracted.provider_version,
                provider_job_id=extracted.provider_job_id,
                language=extracted.language,
                confidence=extracted.confidence,
                warnings=list(extracted.warnings),
                provider_artifact=dict(extracted.provider_metadata),
                content_hashes={"extracted": _sha256_text(extracted.text)},
            )

        form_type = classify_iif_form(ocr.text)
        if form_type is None:
            return await self._type_unstructured_google_ocr(
                doc=doc,
                run=run,
                page_rows=page_rows,
                extracted_pages=extracted_pages,
                classification_details={
                    **classification_details,
                    "legal_form_type": "unknown",
                    "classification_method": "deterministic_header_match",
                },
            )

        doc.document_type = DocumentType(form_type)
        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.structured_extraction,
            metadata={
                **(run.metadata_ or {}),
                "legal_form_type": form_type,
                "classification_method": "deterministic_header_match",
            },
        )
        await self.db.commit()

        extraction_artifact: dict = {}
        extraction_warning: str | None = None
        try:
            structured, extraction_artifact = await self.legal_form_extractor.extract(
                ocr.text,
                form_type=form_type,
            )
            typed_markdown = render_legal_form_markdown(structured)
        except DocumentProviderError as exc:
            if exc.retryable:
                raise
            extraction_warning = str(exc)
            extraction_artifact = {
                "schema_version": 1,
                "status": "manual_review_required",
                "provider": self.legal_form_extractor.provider_key,
                "form_type": form_type,
                "warnings": [extraction_warning],
            }
            typed_markdown = _raw_ocr_review_markdown(extracted_pages, form_type=form_type)

        for index, row in enumerate(page_rows):
            warnings = list(row.warnings or [])
            if index == 0 and extraction_warning:
                warnings.append(extraction_warning)
            await self.repo.save_page_typing(
                row,
                typed_markdown=row.extracted_text or "",
                warnings=warnings,
                provider_key=(
                    self.legal_form_extractor.provider_key
                    if extraction_warning is None
                    else "google_ocr_manual_review_fallback"
                ),
                provider_version="legal_form_schema_v1",
                provider_artifact={
                    "form_type": form_type,
                    "structured_document_level_output": True,
                },
                typed_hash=_sha256_text(row.extracted_text or ""),
            )

        return await self._finalize_whole_document_form(
            doc=doc,
            run=run,
            page_rows=page_rows,
            extracted_pages=extracted_pages,
            typed_markdown=typed_markdown,
            extraction_artifact=extraction_artifact,
            classification_details={
                **classification_details,
                "legal_form_type": form_type,
                "classification_method": "deterministic_header_match",
            },
        )

    async def _inventory_whole_pdf_pages(
        self,
        *,
        doc: Document,
        run: DocumentProcessingRun,
        file_bytes: bytes,
        page_count: int,
    ) -> list[DocumentPage]:
        source_hash = hashlib.sha256(file_bytes).hexdigest()
        rows = await self.repo.replace_run_pages(
            doc,
            run,
            [
                {
                    "page_number": page_number,
                    "source_filename": f"page_{page_number:04d}.pdf",
                    "mime_type": PDF_MIME,
                    "checksum_sha256": hashlib.sha256(
                        f"{source_hash}:{page_number}".encode("ascii")
                    ).hexdigest(),
                    "size_bytes": len(file_bytes),
                    "status": DocumentPageStatus.extracting,
                    "classification": DocumentPageClassification.scanned,
                    "extraction_method": DocumentExtractionMethod.vision_ocr,
                    "classification_artifact": {
                        "route": "whole_document_batch_ocr",
                        "native_pdf_parsing": False,
                    },
                    "content_hashes": {"source_document": source_hash},
                }
                for page_number in range(1, page_count + 1)
            ],
        )
        return rows

    async def _type_unstructured_google_ocr(
        self,
        *,
        doc: Document,
        run: DocumentProcessingRun,
        page_rows: Sequence[DocumentPage],
        extracted_pages: Sequence[ExtractedPageText],
        classification_details: dict,
    ) -> PipelineExecutionResult:
        await self.repo.update_processing_run(run, status=ProcessingRunStatus.typing_pages)
        for page in page_rows:
            page.status = DocumentPageStatus.typing
        await self.db.commit()
        typed_pages = await self.typing_provider.type_pages(
            extracted_pages,
            document_type_hint=doc.document_type.value,
            idempotency_key=f"{run.id}:typing-google-ocr",
        )
        typed_by_id = {page.page_id: page for page in typed_pages}
        if set(typed_by_id) != {page.id for page in page_rows}:
            raise RuntimeError("Typing provider output did not match Google OCR pages.")
        for row in page_rows:
            typed = typed_by_id[row.id]
            await self.repo.save_page_typing(
                row,
                typed_markdown=typed.typed_markdown,
                warnings=list(typed.warnings),
                provider_key=typed.provider_key,
                provider_version=typed.provider_version,
                provider_artifact=dict(typed.provider_metadata),
                typed_hash=_sha256_text(typed.typed_markdown),
            )
        await self.repo.update_processing_run(run, status=ProcessingRunStatus.stitching_pages)
        await self.db.commit()
        return await self._finalize_run(
            doc=doc,
            run=run,
            page_rows=page_rows,
            route=ProcessingRoute.scanned_ocr,
            extracted_pages=extracted_pages,
            classification_details=classification_details,
        )

    async def _finalize_whole_document_form(
        self,
        *,
        doc: Document,
        run: DocumentProcessingRun,
        page_rows: Sequence[DocumentPage],
        extracted_pages: Sequence[ExtractedPageText],
        typed_markdown: str,
        extraction_artifact: dict,
        classification_details: dict,
    ) -> PipelineExecutionResult:
        document_ocr_provider = self.document_ocr_provider
        if document_ocr_provider is None:
            raise RuntimeError("Whole-document OCR provider is not configured.")
        previous = await self.repo.get_latest_typed_revision(doc.id)
        canonical = build_canonical_document(
            typed_markdown,
            document_type=doc.document_type.value,
            page_evidence=[
                {
                    "page_number": page.page_number,
                    "confidence": page.confidence,
                    "warnings": page.warnings or [],
                }
                for page in page_rows
            ],
        )
        revision = await self.repo.create_typed_revision(
            doc,
            content_markdown=typed_markdown,
            content_hash=_sha256_text(typed_markdown),
            canonical_structure=canonical.model_dump(mode="json"),
            canonical_schema_version=canonical.schema_version,
            created_by=doc.uploaded_by,
            processing_run_id=run.id,
            parent_revision_id=previous.id if previous else None,
        )
        raw_text = "\n\n".join(page.text for page in extracted_pages if page.text.strip())
        source_artifact = {
            "schema_version": 3,
            "provider": document_ocr_provider.provider_key,
            "route": ProcessingRoute.scanned_ocr.value,
            "ocr_mode": "batch_whole_pdf",
            "reading_order": "paragraph_y_rounded_2_then_x",
            "structured_extraction": extraction_artifact,
            "pages": [
                {
                    "page_number": page.page_number,
                    "text": page.text,
                    "confidence": page.confidence,
                    "language": page.language,
                    "warnings": list(page.warnings),
                    "issue_flags": [],
                }
                for page in extracted_pages
            ],
            "stitching": {
                "page_count": len(extracted_pages),
                "warnings": extraction_artifact.get("warnings", []),
                "pages": [
                    {
                        "page_number": page.page_number,
                        "text": page.text,
                        "confidence": page.confidence,
                        "warnings": list(page.warnings),
                        "issue_flags": [],
                    }
                    for page in extracted_pages
                ],
            },
        }
        await self.repo.complete_processing(
            doc,
            route=ProcessingRoute.scanned_ocr,
            source_text=typed_markdown,
            source_artifact=source_artifact,
            classification_details=classification_details,
            is_scanned=True,
        )
        doc.processing_status = ProcessingStatus.ready_for_review
        doc.review_status = DocReviewStatus.review_required
        doc.page_count = len(page_rows)
        doc.ocr_status = OcrStatus.completed
        doc.ocr_raw_text = raw_text
        doc.ocr_provider = document_ocr_provider.provider_key
        doc.ocr_artifact = source_artifact
        from app.features.extraction.repository import ExtractionRepository

        await ExtractionRepository(self.db).create_or_update_typed_version(
            document_id=doc.id,
            typed_content=typed_markdown,
            agent_notes=f"pipeline=google_docai_sarvam_v1|run={run.id}",
        )
        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.ready_for_review,
            metrics={
                **(run.metrics or {}),
                "total_pages": len(page_rows),
                "completed_pages": len(page_rows),
                "failed_pages": 0,
                "ocr_chars": len(raw_text),
                "typed_chars": len(typed_markdown),
            },
            metadata={
                **(run.metadata_ or {}),
                "typed_revision_id": str(revision.id),
                "vision_provider": document_ocr_provider.provider_key,
                "typing_provider": self.legal_form_extractor.provider_key,
                "structured_extraction_status": extraction_artifact.get("status"),
            },
        )
        await self.db.commit()
        return PipelineExecutionResult(
            document_id=doc.id,
            run_id=run.id,
            route=ProcessingRoute.scanned_ocr,
            page_count=len(page_rows),
            typed_revision_id=revision.id,
        )

    async def retry_page(
        self,
        *,
        doc: Document,
        run: DocumentProcessingRun,
        page: DocumentPage,
        file_bytes: bytes,
    ) -> PipelineExecutionResult | None:
        classification = classify_document(file_bytes, doc.mime_type)
        if (
            page.status == DocumentPageStatus.typed
            and run.status == ProcessingRunStatus.ready_for_review
        ):
            revision = await self.repo.get_latest_typed_revision(doc.id)
            if revision is None:
                raise RuntimeError("Completed retry has no typed revision.")
            return PipelineExecutionResult(
                document_id=doc.id,
                run_id=run.id,
                route=classification.route,
                page_count=run.total_pages,
                typed_revision_id=revision.id,
            )
        extracted = self._extracted_page_from_row(page)
        if extracted is None:
            extracted = await self._extract_single_page(
                doc=doc,
                run=run,
                page=page,
                file_bytes=file_bytes,
                pdf=classification.pdf,
            )
            await self.repo.save_page_extraction(
                page,
                extracted_text=extracted.text,
                extraction_method=DocumentExtractionMethod(extracted.method),
                provider_key=extracted.provider_key,
                provider_version=extracted.provider_version,
                provider_job_id=extracted.provider_job_id,
                language=extracted.language,
                confidence=extracted.confidence,
                warnings=list(extracted.warnings),
                provider_artifact=dict(extracted.provider_metadata),
                content_hashes={"extracted": _sha256_text(extracted.text)},
            )

        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.typing_pages,
            error=None,
        )
        page.status = DocumentPageStatus.typing
        await self.db.commit()
        typed_pages = await self.typing_provider.type_pages(
            [extracted],
            document_type_hint=doc.document_type.value,
            idempotency_key=f"{run.id}:typing:{page.id}:{page.attempt_count}",
        )
        if len(typed_pages) != 1:
            raise RuntimeError("Typing provider did not return the retried page.")
        typed = typed_pages[0]
        if typed.page_id != page.id or typed.page_number != page.page_number:
            raise RuntimeError("Typing provider returned a mismatched retried page.")
        await self.repo.save_page_typing(
            page,
            typed_markdown=typed.typed_markdown,
            warnings=list(typed.warnings),
            provider_key=typed.provider_key,
            provider_version=typed.provider_version,
            provider_artifact=dict(typed.provider_metadata),
            typed_hash=_sha256_text(typed.typed_markdown),
        )

        pages = await self.repo.list_pages_for_run(run.id)
        failed_pages = [item for item in pages if item.status == DocumentPageStatus.failed]
        run.failed_pages = len(failed_pages)
        run.completed_pages = sum(
            item.status in {DocumentPageStatus.typed, DocumentPageStatus.blank}
            for item in pages
        )
        if failed_pages:
            run.status = ProcessingRunStatus.failed
            run.error = "One or more pages still require retry."
            doc.processing_status = ProcessingStatus.failed
            doc.processing_error = run.error
            await self.db.commit()
            return None

        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.stitching_pages,
            error=None,
        )
        extracted_pages = [
            item
            for item in (self._extracted_page_from_row(row) for row in pages)
            if item is not None
        ]
        await self.db.commit()
        return await self._finalize_run(
            doc=doc,
            run=run,
            page_rows=pages,
            route=classification.route,
            extracted_pages=extracted_pages,
            classification_details=classification.details,
        )

    async def _extract_single_page(
        self,
        *,
        doc: Document,
        run: DocumentProcessingRun,
        page: DocumentPage,
        file_bytes: bytes,
        pdf,
    ) -> ExtractedPageText:
        if doc.mime_type == PDF_MIME and pdf is not None:
            if page.page_number in pdf.digital_pages:
                return ExtractedPageText(
                    page_id=page.id,
                    page_number=page.page_number,
                    text=pdf.page_text.get(page.page_number, ""),
                    method=DocumentExtractionMethod.embedded_text.value,
                    provider_key="local_pdf",
                    provider_version="pymupdf_v1",
                )
            rendered = render_pdf_pages(file_bytes, (page.page_number,))[0]
        elif doc.mime_type in IMAGE_MIME_TYPES:
            rendered = build_single_image_page(file_bytes, doc.mime_type)
        elif doc.mime_type == DOCX_MIME:
            text, _ = extract_docx(file_bytes)
            return ExtractedPageText(
                page_id=page.id,
                page_number=page.page_number,
                text=text,
                method=DocumentExtractionMethod.embedded_text.value,
                provider_key="local_docx",
                provider_version="python_docx_v1",
            )
        else:
            raise RuntimeError("Unsupported document page retry input.")

        results = await self.vision_provider.extract_pages(
            [
                PageInput(
                    page_id=page.id,
                    page_number=page.page_number,
                    mime_type=rendered.mime_type,
                    content=rendered.content,
                    checksum_sha256=rendered.checksum,
                    source_filename=rendered.filename,
                )
            ],
            idempotency_key=f"{run.id}:vision:{page.id}:{page.attempt_count}",
        )
        if len(results) != 1:
            raise RuntimeError("Vision provider did not return the retried page.")
        result = results[0]
        if result.page_id != page.id or result.page_number != page.page_number:
            raise RuntimeError("Vision provider returned a mismatched retried page.")
        return result

    @staticmethod
    def _extracted_page_from_row(page: DocumentPage) -> ExtractedPageText | None:
        if page.extraction_method is None or page.extracted_text is None:
            return None
        return ExtractedPageText(
            page_id=page.id,
            page_number=page.page_number,
            text=page.extracted_text,
            method=page.extraction_method.value,
            confidence=page.confidence,
            language=page.language,
            warnings=tuple(page.warnings or []),
            provider_key=page.provider_key or "local",
            provider_version=page.provider_version,
            provider_job_id=page.provider_job_id,
            provider_metadata=page.provider_artifact or {},
        )

    async def _finalize_run(
        self,
        *,
        doc: Document,
        run: DocumentProcessingRun,
        page_rows: Sequence[DocumentPage],
        route: ProcessingRoute,
        extracted_pages: Sequence[ExtractedPageText],
        classification_details: dict,
    ) -> PipelineExecutionResult:
        if any(
            page.status not in {DocumentPageStatus.typed, DocumentPageStatus.blank}
            for page in page_rows
        ):
            raise RuntimeError("Every non-blank page must be typed before stitching.")
        stitched = stitch_ocr_pages(
            [
                {
                    "page_number": page.page_number,
                    "source_filename": page.source_filename,
                    "text": page.typed_markdown,
                    "warnings": page.warnings,
                }
                for page in page_rows
            ]
        )
        if not stitched.text.strip():
            raise RuntimeError("Document processing produced empty typed content.")

        previous = await self.repo.get_latest_typed_revision(doc.id)
        canonical = build_canonical_document(
            stitched.text,
            document_type=doc.document_type.value,
            page_evidence=[
                {
                    "page_number": page.page_number,
                    "confidence": page.confidence,
                    "warnings": page.warnings or [],
                }
                for page in page_rows
            ],
        )
        if doc.document_type.value == "other" and canonical.document_type == "fir":
            doc.document_type = DocumentType.fir
        revision = await self.repo.create_typed_revision(
            doc,
            content_markdown=stitched.text,
            content_hash=_sha256_text(stitched.text),
            canonical_structure=canonical.model_dump(mode="json"),
            canonical_schema_version=canonical.schema_version,
            created_by=doc.uploaded_by,
            processing_run_id=run.id,
            parent_revision_id=previous.id if previous else None,
        )
        await self._write_legacy_compatibility(
            doc=doc,
            run=run,
            route=route,
            extracted_pages=extracted_pages,
            stitched=stitched,
            classification_details=classification_details,
        )
        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.ready_for_review,
            metrics={
                **(run.metrics or {}),
                "total_pages": len(page_rows),
                "completed_pages": len(page_rows),
                "failed_pages": 0,
                "typed_chars": len(stitched.text),
            },
            metadata={
                **(run.metadata_ or {}),
                "typed_revision_id": str(revision.id),
                "vision_provider": self.vision_provider.provider_key,
                "typing_provider": self.typing_provider.provider_key,
            },
        )
        await self.db.commit()
        return PipelineExecutionResult(
            document_id=doc.id,
            run_id=run.id,
            route=route,
            page_count=len(page_rows),
            typed_revision_id=revision.id,
        )

    async def _inventory_pages(
        self,
        *,
        doc: Document,
        run: DocumentProcessingRun,
        file_bytes: bytes,
        route: ProcessingRoute,
        pdf,
    ) -> tuple[list[DocumentPage], dict[int, PageInput]]:
        await self.repo.update_processing_run(
            run,
            status=ProcessingRunStatus.classifying_pages,
        )
        page_data: list[dict] = []
        rendered_by_number = {}
        if doc.mime_type == PDF_MIME:
            if pdf is None:
                raise RuntimeError("PDF classification details are missing.")
            rendered_by_number = {
                page.page_number: page
                for page in render_pdf_pages(file_bytes, pdf.scanned_pages)
            }
            for page_number in range(1, pdf.page_count + 1):
                embedded_text = pdf.page_text.get(page_number, "")
                rendered = rendered_by_number.get(page_number)
                is_digital = page_number in pdf.digital_pages
                is_blank = page_number in pdf.blank_pages
                source_bytes = (
                    embedded_text.encode("utf-8")
                    if is_digital
                    else rendered.content
                    if rendered is not None
                    else b""
                )
                if is_blank:
                    page_classification = DocumentPageClassification.blank
                    extraction_method = DocumentExtractionMethod.none
                    page_status = DocumentPageStatus.blank
                    route_label = "blank"
                elif is_digital:
                    page_classification = DocumentPageClassification.digital
                    extraction_method = DocumentExtractionMethod.embedded_text
                    page_status = DocumentPageStatus.classified
                    route_label = "digital"
                else:
                    page_classification = (
                        DocumentPageClassification.digital_low_quality
                        if embedded_text.strip()
                        else DocumentPageClassification.scanned
                    )
                    extraction_method = DocumentExtractionMethod.vision_ocr
                    page_status = DocumentPageStatus.classified
                    route_label = page_classification.value
                page_data.append(
                    {
                        "page_number": page_number,
                        "source_filename": (
                            f"page_{page_number:04d}.pdf"
                            if is_digital or is_blank
                            else rendered.filename
                        ),
                        "mime_type": (
                            PDF_MIME
                            if is_digital or is_blank
                            else rendered.mime_type
                        ),
                        "checksum_sha256": hashlib.sha256(source_bytes).hexdigest(),
                        "size_bytes": len(source_bytes),
                        "status": page_status,
                        "classification": page_classification,
                        "extraction_method": extraction_method,
                        "embedded_text": embedded_text if is_digital else None,
                        "classification_artifact": {
                            "route": route_label,
                            "embedded_chars": len(embedded_text),
                        },
                        "content_hashes": {
                            "source": hashlib.sha256(source_bytes).hexdigest()
                        },
                    }
                )
        elif doc.mime_type in IMAGE_MIME_TYPES:
            rendered = build_single_image_page(file_bytes, doc.mime_type)
            rendered_by_number = {1: rendered}
            page_data.append(
                {
                    "page_number": 1,
                    "source_filename": rendered.filename,
                    "mime_type": rendered.mime_type,
                    "checksum_sha256": rendered.checksum,
                    "size_bytes": rendered.size_bytes,
                    "status": DocumentPageStatus.classified,
                    "classification": DocumentPageClassification.scanned,
                    "extraction_method": DocumentExtractionMethod.vision_ocr,
                    "classification_artifact": {"route": "scanned"},
                    "content_hashes": {"source": rendered.checksum},
                }
            )
        elif doc.mime_type == DOCX_MIME:
            text, artifact = extract_docx(file_bytes)
            checksum = hashlib.sha256(file_bytes).hexdigest()
            page_data.append(
                {
                    "page_number": 1,
                    "source_filename": "document.docx",
                    "mime_type": DOCX_MIME,
                    "checksum_sha256": checksum,
                    "size_bytes": len(file_bytes),
                    "status": DocumentPageStatus.classified,
                    "classification": DocumentPageClassification.digital,
                    "extraction_method": DocumentExtractionMethod.embedded_text,
                    "embedded_text": text,
                    "classification_artifact": {
                        "route": "digital",
                        "embedded_chars": len(text),
                        "blocks": len(artifact.get("blocks", [])),
                    },
                    "content_hashes": {"source": checksum},
                }
            )
        else:
            raise RuntimeError(f"Unsupported document MIME type: {doc.mime_type}")

        rows = await self.repo.replace_run_pages(doc, run, page_data)
        scanned_inputs: dict[int, PageInput] = {}
        for row in rows:
            rendered = rendered_by_number.get(row.page_number)
            if rendered is not None:
                scanned_inputs[row.page_number] = PageInput(
                    page_id=row.id,
                    page_number=row.page_number,
                    mime_type=rendered.mime_type,
                    content=rendered.content,
                    checksum_sha256=rendered.checksum,
                    source_filename=rendered.filename,
                )
        await self.db.commit()
        return rows, scanned_inputs

    async def _extract_pages(
        self,
        *,
        run: DocumentProcessingRun,
        page_rows: Sequence[DocumentPage],
        scanned_inputs: dict[int, PageInput],
    ) -> list[ExtractedPageText]:
        extracted: list[ExtractedPageText] = []
        for page in page_rows:
            if page.classification == DocumentPageClassification.digital:
                extracted.append(
                    ExtractedPageText(
                        page_id=page.id,
                        page_number=page.page_number,
                        text=page.embedded_text or "",
                        method="embedded_text",
                        provider_key="local_pdf",
                        provider_version="pymupdf_v1",
                    )
                )
            elif page.classification == DocumentPageClassification.blank:
                extracted.append(
                    ExtractedPageText(
                        page_id=page.id,
                        page_number=page.page_number,
                        text="",
                        method=DocumentExtractionMethod.none.value,
                        provider_key="local_blank_page",
                        provider_version="v1",
                    )
                )
        local_by_id = {result.page_id: result for result in extracted}
        for page in page_rows:
            result = local_by_id.get(page.id)
            if result is None:
                continue
            method = DocumentExtractionMethod(result.method)
            if method == DocumentExtractionMethod.none:
                page.extracted_text = ""
                page.typed_markdown = ""
                page.provider_key = result.provider_key
                page.provider_version = result.provider_version
                page.error = None
            else:
                await self.repo.save_page_extraction(
                    page,
                    extracted_text=result.text,
                    extraction_method=method,
                    provider_key=result.provider_key,
                    provider_version=result.provider_version,
                    provider_job_id=result.provider_job_id,
                    language=result.language,
                    confidence=result.confidence,
                    warnings=list(result.warnings),
                    provider_artifact=dict(result.provider_metadata),
                    content_hashes={"extracted": _sha256_text(result.text)},
                )
        await self.db.commit()

        if scanned_inputs:
            for page in page_rows:
                if page.page_number in scanned_inputs:
                    page.status = DocumentPageStatus.extracting
            await self.db.commit()
            vision_results = await self.vision_provider.extract_pages(
                list(scanned_inputs.values()),
                idempotency_key=f"{run.id}:vision",
            )
            extracted.extend(vision_results)
            pages_by_id = {page.id: page for page in page_rows}
            for result in vision_results:
                page = pages_by_id.get(result.page_id)
                if page is None:
                    raise RuntimeError("Vision provider returned an unknown page.")
                await self.repo.save_page_extraction(
                    page,
                    extracted_text=result.text,
                    extraction_method=DocumentExtractionMethod(result.method),
                    provider_key=result.provider_key,
                    provider_version=result.provider_version,
                    provider_job_id=result.provider_job_id,
                    language=result.language,
                    confidence=result.confidence,
                    warnings=list(result.warnings),
                    provider_artifact=dict(result.provider_metadata),
                    content_hashes={"extracted": _sha256_text(result.text)},
                )
        extracted_by_id = {page.page_id: page for page in extracted}
        if set(extracted_by_id) != {page.id for page in page_rows}:
            raise RuntimeError("Text extraction did not return every inventoried page.")
        await self.db.commit()
        return sorted(extracted, key=lambda page: page.page_number)

    async def _write_legacy_compatibility(
        self,
        *,
        doc: Document,
        run: DocumentProcessingRun,
        route: ProcessingRoute,
        extracted_pages: Sequence[ExtractedPageText],
        stitched,
        classification_details: dict,
    ) -> None:
        raw_text = "\n\n".join(
            page.text.strip() for page in extracted_pages if page.text.strip()
        )
        source_artifact = {
            "schema_version": 2,
            "provider": self.typing_provider.provider_key,
            "route": route.value,
            "stitching": stitched.artifact(),
            "pages": stitched.pages,
            "page_evidence": [
                {
                    "page_number": page.page_number,
                    "confidence": page.confidence,
                    "warnings": page.warnings or [],
                    "layout": (page.provider_artifact or {}).get("layout", {}),
                }
                for page in await self.repo.list_pages_for_run(run.id)
            ],
        }
        await self.repo.complete_processing(
            doc,
            route=route,
            source_text=stitched.text,
            source_artifact=source_artifact,
            classification_details=classification_details,
            is_scanned=route != ProcessingRoute.digital_extract,
        )
        doc.processing_status = ProcessingStatus.ready_for_review
        doc.review_status = DocReviewStatus.review_required
        doc.page_count = len(extracted_pages)
        if route == ProcessingRoute.digital_extract:
            doc.ocr_status = OcrStatus.not_required
            doc.ocr_raw_text = None
            doc.ocr_provider = None
        else:
            doc.ocr_status = OcrStatus.completed
            doc.ocr_raw_text = raw_text
            doc.ocr_provider = self.vision_provider.provider_key
            doc.ocr_artifact = source_artifact
        from app.features.extraction.repository import ExtractionRepository

        await ExtractionRepository(self.db).create_or_update_typed_version(
            document_id=doc.id,
            typed_content=stitched.text,
            agent_notes=f"pipeline={run.pipeline_version}|run={run.id}",
        )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _raw_ocr_review_markdown(
    pages: Sequence[ExtractedPageText], *, form_type: str
) -> str:
    title = "FIRST INFORMATION REPORT" if form_type == "fir" else "FINAL FORM / REPORT"
    sections = [
        f"# {title}",
        "",
        "> Structured extraction needs manual review. The source-grounded Google OCR text is shown below.",
    ]
    for page in pages:
        sections.extend(["", f"## Page {page.page_number}", "", page.text.strip()])
    return "\n".join(sections).strip() + "\n"
