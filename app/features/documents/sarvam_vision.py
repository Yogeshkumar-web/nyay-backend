from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import httpx

from app.core.config import settings
from app.features.documents.digital_extractor import PageSplit
from app.features.documents.document_processing_service import OcrResult


class SarvamVisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class _PreparedSarvamInput:
    filename: str
    upload_bytes: bytes
    source_filenames: tuple[str, ...]
    original_page_numbers: tuple[int, ...] | None = None


class SarvamVisionOcrProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        language: str | None = None,
        output_format: str | None = None,
        poll_interval_seconds: float | None = None,
        timeout_seconds: float | None = None,
    ):
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.base_url = (base_url or settings.SARVAM_BASE_URL).rstrip("/")
        self.language = language or settings.SARVAM_DOC_LANGUAGE
        self.output_format = output_format or settings.SARVAM_DOC_OUTPUT_FORMAT
        self.poll_interval_seconds = (
            poll_interval_seconds or settings.SARVAM_DOC_POLL_INTERVAL_SECONDS
        )
        self.timeout_seconds = timeout_seconds or settings.SARVAM_DOC_TIMEOUT_SECONDS

    async def process(
        self,
        file_bytes: bytes,
        mime_type: str,
        *,
        original_page_numbers: tuple[int, ...] | None = None,
    ) -> OcrResult:
        if not self.api_key:
            raise SarvamVisionError("SARVAM_API_KEY is not configured.")

        filename, upload_bytes = _prepare_sarvam_input(file_bytes, mime_type)
        return await self._process_prepared_input(
            _PreparedSarvamInput(
                filename=filename,
                upload_bytes=upload_bytes,
                source_filenames=(filename,),
                original_page_numbers=original_page_numbers,
            )
        )

    async def process_pages(
        self,
        pages: Sequence[PageSplit],
        *,
        batch_size: int = 10,
    ) -> OcrResult:
        if not self.api_key:
            raise SarvamVisionError("SARVAM_API_KEY is not configured.")
        if batch_size < 1 or batch_size > 10:
            raise SarvamVisionError("Sarvam page batch size must be between 1 and 10.")
        if not pages:
            raise SarvamVisionError("No pages were provided for Sarvam OCR.")

        sorted_pages = sorted(pages, key=lambda page: page.page_number)
        all_pages: list[dict[str, Any]] = []
        batches: list[dict[str, Any]] = []
        for batch_index, offset in enumerate(range(0, len(sorted_pages), batch_size), start=1):
            batch_pages = sorted_pages[offset : offset + batch_size]
            prepared = _prepare_sarvam_page_batch(batch_pages, batch_index)
            result = await self._process_prepared_input(prepared)
            job = result.artifact.get("job", {})
            page_artifacts = result.artifact.get("pages", [])
            for page in page_artifacts:
                page["batch_index"] = batch_index
                page["batch_job_id"] = job.get("job_id")
            all_pages.extend(page_artifacts)
            batches.append(
                {
                    "batch_index": batch_index,
                    "job": job,
                    "input_filename": prepared.filename,
                    "source_filenames": list(prepared.source_filenames),
                    "download_manifest": result.artifact.get("download_manifest"),
                    "output_files": result.artifact.get("output_files", []),
                    "json_payload": result.artifact.get("json_payload"),
                }
            )

        all_pages.sort(key=lambda page: int(page.get("page_number") or 0))
        text = "\n\n".join(
            str(page.get("text") or "").strip()
            for page in all_pages
            if str(page.get("text") or "").strip()
        ).strip()
        if not text:
            raise SarvamVisionError("Sarvam did not return text for any page.")
        return OcrResult(
            text=text,
            language=self.language,
            page_count=len(all_pages),
            artifact={
                "schema_version": 1,
                "provider": "sarvam_vision",
                "processor_role": "document_ocr",
                "batch_size": batch_size,
                "batch_count": len(batches),
                "batches": batches,
                "pages": all_pages,
            },
        )

    async def _process_prepared_input(
        self,
        prepared: _PreparedSarvamInput,
    ) -> OcrResult:
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as client:
            created = await self._create_job(client)
            job_id = created["job_id"]
            upload = await self._get_upload_url(client, job_id, prepared.filename)
            await self._upload_file(client, upload["file_url"], prepared.upload_bytes)
            started = await self._start_job(client, job_id)
            status = await self._wait_for_completion(client, job_id, started)
            download = await self._get_download_urls(client, job_id)
            output_zip = await self._download_output_zip(client, download)

        extracted = _extract_zip_output(output_zip)
        normalized_text = _normalize_sarvam_text(extracted.text)
        pages = _build_pages(
            normalized_text,
            extracted.json_payload,
            original_page_numbers=prepared.original_page_numbers,
            source_filenames=prepared.source_filenames,
            fallback_page_texts=extracted.markdown_texts,
        )
        artifact = {
            "schema_version": 1,
            "provider": "sarvam_vision",
            "processor_role": "document_ocr",
            "job": status,
            "create_job_response": created,
            "input_filename": prepared.filename,
            "source_filenames": list(prepared.source_filenames),
            "download_manifest": download,
            "pages": pages,
            "json_payload": extracted.json_payload,
            "output_files": extracted.output_files,
        }
        return OcrResult(
            text=normalized_text,
            language=self.language,
            page_count=len(pages),
            artifact=artifact,
        )

    async def _create_job(self, client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(
            f"{self.base_url}/doc-digitization/job/v1",
            headers=self._headers(),
            json={
                "job_parameters": {
                    "language": self.language,
                    "output_format": self.output_format,
                }
            },
        )
        return _json_or_raise(response)

    async def _get_upload_url(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        filename: str,
    ) -> dict[str, Any]:
        response = await client.post(
            f"{self.base_url}/doc-digitization/job/v1/upload-files",
            headers=self._headers(),
            json={"job_id": job_id, "files": [filename]},
        )
        payload = _json_or_raise(response)
        try:
            return payload["upload_urls"][filename]
        except KeyError as exc:
            raise SarvamVisionError("Sarvam upload URL response is missing file URL.") from exc

    async def _upload_file(
        self,
        client: httpx.AsyncClient,
        file_url: str,
        file_bytes: bytes,
    ) -> None:
        response = await client.put(
            file_url,
            content=file_bytes,
            headers={"x-ms-blob-type": "BlockBlob"},
        )
        if response.status_code >= 400:
            raise SarvamVisionError(
                f"Sarvam presigned upload failed with status {response.status_code}."
            )

    async def _start_job(
        self,
        client: httpx.AsyncClient,
        job_id: str,
    ) -> dict[str, Any]:
        response = await client.post(
            f"{self.base_url}/doc-digitization/job/v1/{job_id}/start",
            headers=self._headers(),
            json={},
        )
        return _json_or_raise(response)

    async def _wait_for_completion(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        initial_status: dict[str, Any],
    ) -> dict[str, Any]:
        status = initial_status
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        while status.get("job_state") not in {"Completed", "PartiallyCompleted", "Failed"}:
            if asyncio.get_running_loop().time() >= deadline:
                raise SarvamVisionError("Sarvam document digitization timed out.")
            await asyncio.sleep(self.poll_interval_seconds)
            response = await client.get(
                f"{self.base_url}/doc-digitization/job/v1/{job_id}/status",
                headers=self._headers(),
            )
            status = _json_or_raise(response)
        if status.get("job_state") == "Failed":
            raise SarvamVisionError(
                str(status.get("error_message") or "Sarvam document digitization failed.")
            )
        return status

    async def _get_download_urls(
        self,
        client: httpx.AsyncClient,
        job_id: str,
    ) -> dict[str, Any]:
        response = await client.post(
            f"{self.base_url}/doc-digitization/job/v1/{job_id}/download-files",
            headers=self._headers(),
            json={},
        )
        return _json_or_raise(response)

    async def _download_output_zip(
        self,
        client: httpx.AsyncClient,
        download_manifest: dict[str, Any],
    ) -> bytes:
        urls = download_manifest.get("download_urls") or {}
        for filename, details in urls.items():
            if filename.lower().endswith(".zip") or len(urls) == 1:
                response = await client.get(details["file_url"])
                if response.status_code >= 400:
                    raise SarvamVisionError(
                        f"Sarvam output download failed with status {response.status_code}."
                    )
                return response.content
        raise SarvamVisionError("Sarvam download response did not include an output ZIP.")

    def _headers(self) -> dict[str, str]:
        return {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json",
        }


class _ZipExtraction:
    def __init__(
        self,
        *,
        text: str,
        json_payload: dict[str, Any] | list[Any] | None,
        output_files: list[str],
        markdown_texts: list[str],
    ):
        self.text = text
        self.json_payload = json_payload
        self.output_files = output_files
        self.markdown_texts = markdown_texts


def _json_or_raise(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"message": response.text}}
        message = payload.get("error", {}).get("message") or str(payload)
        raise SarvamVisionError(f"Sarvam API error {response.status_code}: {message}")
    return response.json()


def _extract_zip_output(output_zip: bytes) -> _ZipExtraction:
    try:
        archive = zipfile.ZipFile(io.BytesIO(output_zip))
    except zipfile.BadZipFile as exc:
        raise SarvamVisionError("Sarvam output was not a valid ZIP file.") from exc

    output_files = archive.namelist()
    markdown_parts: list[str] = []
    markdown_texts: list[str] = []
    json_payload: dict[str, Any] | list[Any] | None = None
    with archive:
        for name in sorted(output_files):
            suffix = Path(name).suffix.lower()
            if suffix == ".md":
                markdown_text = archive.read(name).decode("utf-8", errors="replace")
                markdown_texts.append(markdown_text.strip())
                markdown_parts.append(markdown_text)
            elif suffix == ".json" and json_payload is None:
                json_payload = json.loads(archive.read(name).decode("utf-8", errors="replace"))

    text = "\n\n".join(part.strip() for part in markdown_parts if part.strip())
    if not text and json_payload is not None:
        text = _text_from_json_payload(json_payload)
    if not text:
        raise SarvamVisionError("Sarvam output ZIP did not contain extracted text.")
    return _ZipExtraction(
        text=text,
        json_payload=json_payload,
        output_files=output_files,
        markdown_texts=[text for text in markdown_texts if text],
    )


_NUMBERED_LINE_RE = re.compile(r"^(\s*)(\d{1,3})([.)])\s+(.+?)\s*$")


def _normalize_sarvam_text(text: str) -> str:
    """Remove line numbers added by OCR, without touching real paragraph numbers."""
    lines = text.splitlines()
    numbered: list[tuple[int, int, str]] = []
    non_empty_count = 0

    for index, line in enumerate(lines):
        if not line.strip():
            continue
        non_empty_count += 1
        match = _NUMBERED_LINE_RE.match(line)
        if match:
            numbered.append((index, int(match.group(2)), match.group(4)))

    if not _looks_like_synthetic_line_numbering(numbered, non_empty_count):
        return text.strip()

    numbered_by_index = {index: body for index, _, body in numbered}
    normalized_lines = [
        numbered_by_index.get(index, line).strip() if line.strip() else ""
        for index, line in enumerate(lines)
    ]
    return "\n".join(normalized_lines).strip()


def _looks_like_synthetic_line_numbering(
    numbered: list[tuple[int, int, str]],
    non_empty_count: int,
) -> bool:
    if non_empty_count == 0 or len(numbered) < 8:
        return False

    density = len(numbered) / non_empty_count
    if density < 0.8:
        return False

    numbers = [number for _, number, _ in numbered]
    if numbers[0] != 1:
        return False

    increasing_pairs = sum(
        1
        for current, next_number in zip(numbers, numbers[1:], strict=False)
        if next_number > current
    )
    if increasing_pairs / max(len(numbers) - 1, 1) < 0.8:
        return False

    number_span = numbers[-1] - numbers[0] + 1
    return number_span >= 8 and number_span / len(numbers) <= 1.5


def _text_from_json_payload(payload: dict[str, Any] | list[Any]) -> str:
    strings: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key.lower() in {"text", "content", "markdown", "html"}:
                    collect(nested)
                elif isinstance(nested, (dict, list)):
                    collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)
        elif isinstance(value, str) and value.strip():
            strings.append(value.strip())

    collect(payload)
    return "\n\n".join(strings)


def _build_pages(
    text: str,
    json_payload: dict[str, Any] | list[Any] | None,
    *,
    original_page_numbers: tuple[int, ...] | None,
    source_filenames: tuple[str, ...],
    fallback_page_texts: list[str],
) -> list[dict[str, Any]]:
    page_texts = _extract_page_texts(json_payload)
    if not page_texts:
        page_texts = fallback_page_texts if fallback_page_texts else [text]
    else:
        page_texts = [_normalize_sarvam_text(page_text) for page_text in page_texts]

    pages: list[dict[str, Any]] = []
    for index, page_text in enumerate(page_texts):
        page_number = (
            original_page_numbers[index]
            if original_page_numbers and index < len(original_page_numbers)
            else index + 1
        )
        pages.append(
            {
                "page_number": page_number,
                "source": "sarvam_vision",
                "source_filename": source_filenames[index]
                if index < len(source_filenames)
                else None,
                "text": page_text,
            }
        )
    return pages


def _extract_page_texts(payload: dict[str, Any] | list[Any] | None) -> list[str]:
    if payload is None:
        return []
    candidates = payload.get("pages") if isinstance(payload, dict) else payload
    if not isinstance(candidates, list):
        return []
    pages: list[str] = []
    for page in candidates:
        if not isinstance(page, dict):
            continue
        text = page.get("text") or page.get("content") or page.get("markdown")
        if isinstance(text, str) and text.strip():
            pages.append(text.strip())
    return pages


def _prepare_sarvam_input(file_bytes: bytes, mime_type: str) -> tuple[str, bytes]:
    if mime_type == "application/pdf":
        return "document.pdf", file_bytes
    if mime_type == "application/zip":
        return "document.zip", file_bytes
    if mime_type == "image/png":
        return "document.zip", _zip_single_image(file_bytes, "document.png")
    if mime_type in {"image/jpeg", "image/jpg"}:
        return "document.zip", _zip_single_image(file_bytes, "document.jpg")
    raise SarvamVisionError(f"Unsupported Sarvam Vision input type: {mime_type}")


def _prepare_sarvam_page_batch(
    pages: Sequence[PageSplit],
    batch_index: int,
) -> _PreparedSarvamInput:
    if len(pages) > 10:
        raise SarvamVisionError("Sarvam page batches cannot exceed 10 pages.")
    filename = f"pages_batch_{batch_index:04d}.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for page in sorted(pages, key=lambda item: item.page_number):
            archive.writestr(page.filename, page.content)
    ordered_pages = tuple(sorted(pages, key=lambda item: item.page_number))
    return _PreparedSarvamInput(
        filename=filename,
        upload_bytes=buffer.getvalue(),
        source_filenames=tuple(page.filename for page in ordered_pages),
        original_page_numbers=tuple(page.page_number for page in ordered_pages),
    )


def _zip_single_image(file_bytes: bytes, filename: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, file_bytes)
    return buffer.getvalue()
