from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

from app.core.config import settings
from app.features.documents.providers.contracts import (
    DocumentProviderError,
    ExtractedPageText,
    PageInput,
    ProviderErrorCategory,
)


_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


@dataclass(frozen=True)
class DocumentAiPage:
    page_number: int
    text: str
    confidence: float | None
    language: str | None
    warnings: tuple[str, ...]
    artifact: dict[str, Any]


@dataclass(frozen=True)
class DocumentAiResult:
    text: str
    pages: tuple[DocumentAiPage, ...]
    operation_name: str
    input_gcs_uri: str
    output_gcs_prefix: str


class GoogleDocumentAiProcessor:
    """Whole-document batch OCR plus single-page retry support.

    The normal PDF path uploads the original PDF once and uses Document AI batch
    processing. It never renders or splits the PDF into page images.
    """

    provider_key = "google_document_ai"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        location: str | None = None,
        processor_id: str | None = None,
        credentials_path: str | None = None,
        bucket: str | None = None,
        poll_interval_seconds: float | None = None,
        timeout_seconds: float | None = None,
        low_quality_threshold: float | None = None,
    ) -> None:
        self.project_id = project_id or settings.GOOGLE_PROJECT_ID
        self.location = location or settings.GOOGLE_LOCATION
        self.processor_id = processor_id or settings.GOOGLE_DOCAI_PROCESSOR_ID
        self.credentials_path = _resolve_credentials_path(
            credentials_path or settings.GOOGLE_APPLICATION_CREDENTIALS
        )
        self.bucket = bucket or settings.GOOGLE_DOCAI_GCS_BUCKET
        self.poll_interval_seconds = (
            poll_interval_seconds or settings.GOOGLE_DOCAI_POLL_INTERVAL_SECONDS
        )
        self.timeout_seconds = timeout_seconds or settings.GOOGLE_DOCAI_TIMEOUT_SECONDS
        self.low_quality_threshold = (
            low_quality_threshold
            if low_quality_threshold is not None
            else settings.GOOGLE_DOCAI_LOW_QUALITY_THRESHOLD
        )
        self._credentials = None

    async def process_pdf(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        idempotency_key: str,
    ) -> DocumentAiResult:
        self._validate_batch_configuration()
        safe_key = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        suffix = Path(filename).suffix.lower() or ".pdf"
        input_name = f"document-ai/{safe_key}/input/source{suffix}"
        output_prefix = f"document-ai/{safe_key}/output/"
        input_uri = f"gs://{self.bucket}/{input_name}"
        output_uri = f"gs://{self.bucket}/{output_prefix}"
        token = await self._access_token()
        operation_name = ""
        try:
            await self._upload_gcs_object(
                object_name=input_name,
                content=file_bytes,
                content_type="application/pdf",
                token=token,
            )
            operation_name = await self._start_batch(
                input_uri=input_uri,
                output_uri=output_uri,
                token=token,
            )
            await self._wait_for_operation(operation_name, token=token)
            payloads = await self._download_output_documents(output_prefix, token=token)
            pages = self._parse_output_documents(payloads)
            if not pages:
                raise self._error(
                    "Google Document AI returned no OCR pages.",
                    ProviderErrorCategory.invalid_response,
                    retryable=True,
                )
            return DocumentAiResult(
                text="\n\n".join(page.text for page in pages if page.text.strip()),
                pages=tuple(pages),
                operation_name=operation_name,
                input_gcs_uri=input_uri,
                output_gcs_prefix=output_uri,
            )
        finally:
            # Only objects under this run-specific prefix are removed.
            try:
                cleanup_token = token if "token" in locals() else await self._access_token()
                await self._delete_prefix(f"document-ai/{safe_key}/", token=cleanup_token)
            except Exception:
                # Cleanup failure must not hide a successfully processed document.
                pass

    async def extract_pages(
        self,
        pages: Sequence[PageInput],
        *,
        idempotency_key: str,
    ) -> Sequence[ExtractedPageText]:
        """Online OCR for explicit page retries and single uploaded images."""
        self._validate_base_configuration()
        results: list[ExtractedPageText] = []
        for page in pages:
            document = await self._process_inline(page.content, page.mime_type)
            parsed = self._parse_document(document, page_number_offset=page.page_number - 1)
            if len(parsed) != 1:
                raise self._error(
                    "Google Document AI page retry returned an unexpected page count.",
                    ProviderErrorCategory.invalid_response,
                    retryable=True,
                )
            item = parsed[0]
            results.append(
                ExtractedPageText(
                    page_id=page.page_id,
                    page_number=page.page_number,
                    text=item.text,
                    method="vision_ocr",
                    confidence=item.confidence,
                    language=item.language,
                    warnings=item.warnings,
                    provider_key=self.provider_key,
                    provider_version="document_ocr_v1",
                    provider_metadata=item.artifact,
                )
            )
        return results

    def _validate_base_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("GOOGLE_PROJECT_ID", self.project_id),
                ("GOOGLE_LOCATION", self.location),
                ("GOOGLE_DOCAI_PROCESSOR_ID", self.processor_id),
                ("GOOGLE_APPLICATION_CREDENTIALS", self.credentials_path),
            )
            if not value
        ]
        if missing:
            raise self._error(
                f"Google Document AI is missing configuration: {', '.join(missing)}.",
                ProviderErrorCategory.configuration,
                retryable=False,
            )
        if not Path(self.credentials_path).is_file():
            raise self._error(
                "Google Document AI service-account credentials file was not found.",
                ProviderErrorCategory.configuration,
                retryable=False,
            )

    def _validate_batch_configuration(self) -> None:
        self._validate_base_configuration()
        if not self.bucket:
            raise self._error(
                "GOOGLE_DOCAI_GCS_BUCKET is required for long-PDF batch OCR.",
                ProviderErrorCategory.configuration,
                retryable=False,
            )

    async def _access_token(self) -> str:
        credentials = self._credentials
        if credentials is None:
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=_SCOPES,
            )
            self._credentials = credentials
        if not credentials.valid:
            await asyncio.to_thread(credentials.refresh, GoogleAuthRequest())
        if not credentials.token:
            raise self._error(
                "Google authentication did not return an access token.",
                ProviderErrorCategory.authentication,
                retryable=True,
            )
        return credentials.token

    async def _upload_gcs_object(
        self,
        *,
        object_name: str,
        content: bytes,
        content_type: str,
        token: str,
    ) -> None:
        url = f"https://storage.googleapis.com/upload/storage/v1/b/{quote(self.bucket, safe='')}/o"
        await self._request(
            "POST",
            url,
            token=token,
            params={"uploadType": "media", "name": object_name},
            content=content,
            headers={"Content-Type": content_type},
        )

    async def _start_batch(self, *, input_uri: str, output_uri: str, token: str) -> str:
        response = await self._request(
            "POST",
            f"{self._document_ai_base()}/{self._processor_name()}:batchProcess",
            token=token,
            json={
                "inputDocuments": {
                    "gcsDocuments": {
                        "documents": [{"gcsUri": input_uri, "mimeType": "application/pdf"}]
                    }
                },
                "documentOutputConfig": {"gcsOutputConfig": {"gcsUri": output_uri}},
                "processOptions": {"ocrConfig": self._ocr_config()},
                "skipHumanReview": True,
            },
        )
        name = response.get("name")
        if not isinstance(name, str) or not name:
            raise self._error(
                "Google Document AI did not return an operation name.",
                ProviderErrorCategory.invalid_response,
                retryable=True,
            )
        return name

    async def _wait_for_operation(self, operation_name: str, *, token: str) -> None:
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        while True:
            operation = await self._request(
                "GET",
                f"{self._document_ai_base()}/v1/{operation_name}",
                token=token,
            )
            if operation.get("done"):
                if operation.get("error"):
                    message = operation["error"].get("message") or "batch OCR failed"
                    raise self._error(
                        f"Google Document AI batch OCR failed: {message}",
                        ProviderErrorCategory.provider_unavailable,
                        retryable=True,
                    )
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise self._error(
                    "Google Document AI batch OCR timed out.",
                    ProviderErrorCategory.timeout,
                    retryable=True,
                )
            await asyncio.sleep(self.poll_interval_seconds)

    async def _download_output_documents(
        self, prefix: str, *, token: str
    ) -> list[dict[str, Any]]:
        listing = await self._request(
            "GET",
            f"https://storage.googleapis.com/storage/v1/b/{quote(self.bucket, safe='')}/o",
            token=token,
            params={"prefix": prefix},
        )
        names = sorted(
            item["name"]
            for item in listing.get("items", [])
            if isinstance(item.get("name"), str) and item["name"].endswith(".json")
        )
        payloads: list[dict[str, Any]] = []
        for name in names:
            payload = await self._request(
                "GET",
                f"https://storage.googleapis.com/storage/v1/b/{quote(self.bucket, safe='')}/o/{quote(name, safe='')}",
                token=token,
                params={"alt": "media"},
            )
            payloads.append(payload)
        return payloads

    async def _delete_prefix(self, prefix: str, *, token: str) -> None:
        listing = await self._request(
            "GET",
            f"https://storage.googleapis.com/storage/v1/b/{quote(self.bucket, safe='')}/o",
            token=token,
            params={"prefix": prefix},
        )
        for item in listing.get("items", []):
            name = item.get("name")
            if not isinstance(name, str) or not name.startswith(prefix):
                continue
            await self._request(
                "DELETE",
                f"https://storage.googleapis.com/storage/v1/b/{quote(self.bucket, safe='')}/o/{quote(name, safe='')}",
                token=token,
            )

    async def _process_inline(self, content: bytes, mime_type: str) -> dict[str, Any]:
        import base64

        token = await self._access_token()
        response = await self._request(
            "POST",
            f"{self._document_ai_base()}/{self._processor_name()}:process",
            token=token,
            json={
                "rawDocument": {
                    "content": base64.b64encode(content).decode("ascii"),
                    "mimeType": mime_type,
                },
                "processOptions": {"ocrConfig": self._ocr_config()},
                "skipHumanReview": True,
            },
        )
        document = response.get("document")
        if not isinstance(document, dict):
            raise self._error(
                "Google Document AI response is missing the document.",
                ProviderErrorCategory.invalid_response,
                retryable=True,
            )
        return document

    def _parse_output_documents(
        self, payloads: Sequence[dict[str, Any]]
    ) -> list[DocumentAiPage]:
        pages: list[DocumentAiPage] = []
        offset = 0
        for payload in payloads:
            document = payload.get("document") if "document" in payload else payload
            if not isinstance(document, dict):
                continue
            raw_numbers = [
                int(page.get("pageNumber") or index)
                for index, page in enumerate(document.get("pages") or [], start=1)
            ]
            # Some Document AI shards keep global page numbers; others restart at 1.
            shard_offset = offset if raw_numbers and min(raw_numbers) <= offset else 0
            parsed = self._parse_document(document, page_number_offset=shard_offset)
            pages.extend(parsed)
            offset = max((page.page_number for page in pages), default=offset)
        return sorted(pages, key=lambda page: page.page_number)

    def _parse_document(
        self, document: dict[str, Any], *, page_number_offset: int
    ) -> list[DocumentAiPage]:
        full_text = str(document.get("text") or "")
        parsed: list[DocumentAiPage] = []
        for index, page in enumerate(document.get("pages") or [], start=1):
            source_page_number = int(page.get("pageNumber") or index)
            page_number = page_number_offset + source_page_number
            paragraph_rows: list[tuple[float, float, str, float | None]] = []
            for paragraph in page.get("paragraphs") or []:
                layout = paragraph.get("layout") or {}
                text = _text_from_anchor(full_text, layout.get("textAnchor") or {}).strip()
                if not text:
                    continue
                vertices = (layout.get("boundingPoly") or {}).get("normalizedVertices") or []
                xs = [float(vertex.get("x", 0.0)) for vertex in vertices]
                ys = [float(vertex.get("y", 0.0)) for vertex in vertices]
                x = min(xs, default=0.0)
                y = min(ys, default=0.0)
                confidence = _optional_float(layout.get("confidence"))
                paragraph_rows.append((round(y, 2), x, text, confidence))
            paragraph_rows.sort(key=lambda row: (row[0], row[1]))
            page_text = "\n".join(row[2] for row in paragraph_rows).strip()
            confidences = [row[3] for row in paragraph_rows if row[3] is not None]
            confidence = sum(confidences) / len(confidences) if confidences else None
            language = _best_language(page.get("detectedLanguages") or [])
            quality = page.get("imageQualityScores") or {}
            quality_score = _optional_float(quality.get("qualityScore"))
            defects = [
                {
                    "type": defect.get("type"),
                    "confidence": _optional_float(defect.get("confidence")),
                }
                for defect in quality.get("detectedDefects") or []
            ]
            warnings: list[str] = []
            if quality_score is not None and quality_score < self.low_quality_threshold:
                warnings.append(
                    f"Low scan quality ({quality_score:.2f}); consider re-uploading a clearer scan."
                )
            parsed.append(
                DocumentAiPage(
                    page_number=page_number,
                    text=page_text,
                    confidence=confidence,
                    language=language,
                    warnings=tuple(warnings),
                    artifact={
                        "source": self.provider_key,
                        "reading_order": "paragraph_y_rounded_2_then_x",
                        "paragraph_count": len(paragraph_rows),
                        "quality_score": quality_score,
                        "detected_defects": defects,
                        "detected_languages": page.get("detectedLanguages") or [],
                    },
                )
            )
        return parsed

    def _ocr_config(self) -> dict[str, Any]:
        return {
            "enableNativePdfParsing": False,
            "enableImageQualityScores": True,
            "enableSymbol": True,
            "hints": {"languageHints": ["hi"]},
        }

    def _document_ai_base(self) -> str:
        return f"https://{self.location}-documentai.googleapis.com"

    def _processor_name(self) -> str:
        return (
            f"v1/projects/{self.project_id}/locations/{self.location}/"
            f"processors/{self.processor_id}"
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        token: str,
        **kwargs: Any,
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise self._error(
                "Google Document AI request timed out.",
                ProviderErrorCategory.timeout,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._error(
                "Google Document AI request failed.",
                ProviderErrorCategory.provider_unavailable,
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            try:
                details = response.json()
                message = details.get("error", {}).get("message") or str(details)
            except ValueError:
                message = response.text
            category = (
                ProviderErrorCategory.authentication
                if response.status_code in {401, 403}
                else ProviderErrorCategory.rate_limit
                if response.status_code == 429
                else ProviderErrorCategory.invalid_input
                if response.status_code == 400
                else ProviderErrorCategory.provider_unavailable
            )
            raise self._error(
                f"Google Document AI request failed ({response.status_code}): {message}",
                category,
                retryable=response.status_code in {408, 429, 500, 502, 503, 504},
            )
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    @staticmethod
    def _error(
        message: str,
        category: ProviderErrorCategory,
        *,
        retryable: bool,
    ) -> DocumentProviderError:
        return DocumentProviderError(message, category=category, retryable=retryable)


def _text_from_anchor(full_text: str, anchor: dict[str, Any]) -> str:
    parts: list[str] = []
    for segment in anchor.get("textSegments") or []:
        start = int(segment.get("startIndex") or 0)
        end = int(segment.get("endIndex") or start)
        if 0 <= start <= end <= len(full_text):
            parts.append(full_text[start:end])
    return "".join(parts)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _best_language(languages: Sequence[dict[str, Any]]) -> str | None:
    if not languages:
        return None
    best = max(languages, key=lambda item: float(item.get("confidence") or 0.0))
    code = best.get("languageCode")
    return str(code) if code else None


def _resolve_credentials_path(configured_path: str) -> str:
    direct = Path(configured_path)
    if direct.is_file():
        return str(direct)
    normalized_name = Path(configured_path.replace("\\", "/")).name
    local_secret = Path("secrets") / normalized_name
    if normalized_name and local_secret.is_file():
        return str(local_secret)
    return configured_path
