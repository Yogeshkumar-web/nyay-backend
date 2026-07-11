from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence


_NUMBERED_LINE_RE = re.compile(r"^(\s*)(\d{1,3})([.)])\s+(.+?)\s*$")
_LOWERCASE_START_RE = re.compile(r"^[a-z]")
_SENTENCE_END_RE = re.compile(r"[.!?:;।॥]$")


@dataclass(frozen=True)
class StitchedDocument:
    text: str
    pages: list[dict[str, Any]]
    warnings: list[str]

    def artifact(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "strategy": "page_marker_stitching_v1",
            "page_count": len(self.pages),
            "warnings": self.warnings,
            "pages": self.pages,
        }


def stitch_ocr_pages(page_artifacts: Sequence[dict[str, Any]]) -> StitchedDocument:
    ordered_pages = sorted(
        page_artifacts,
        key=lambda page: int(page.get("page_number") or 0),
    )
    stitched_blocks: list[str] = []
    stitched_pages: list[dict[str, Any]] = []
    warnings: list[str] = []

    for page in ordered_pages:
        page_number = int(page.get("page_number") or len(stitched_pages) + 1)
        raw_text = str(page.get("text") or "")
        cleanup = _cleanup_page_text(raw_text)
        page_warnings = list(page.get("warnings") or page.get("ocr_warnings") or [])
        uncertain_words = list(page.get("uncertain_words") or [])
        issue_flags = list(page.get("issue_flags") or page.get("ocr_issue_flags") or [])

        if cleanup["removed_synthetic_line_numbers"]:
            page_warnings.append("synthetic_line_numbers_removed")
        if not cleanup["text"].strip():
            page_warnings.append("empty_page_text")

        stitched_pages.append(
            {
                "page_number": page_number,
                "source_filename": page.get("source_filename"),
                "text": cleanup["text"],
                "raw_text": raw_text,
                "uncertain_words": uncertain_words,
                "issue_flags": issue_flags,
                "warnings": page_warnings,
            }
        )
        warnings.extend(f"page_{page_number}:{warning}" for warning in page_warnings)
        stitched_blocks.append(
            "\n".join(
                [
                    f"[[PAGE {page_number} START]]",
                    cleanup["text"],
                    f"[[PAGE {page_number} END]]",
                ]
            ).strip()
        )

    return StitchedDocument(
        text="\n\n".join(stitched_blocks).strip(),
        pages=stitched_pages,
        warnings=warnings,
    )


def _cleanup_page_text(text: str) -> dict[str, Any]:
    normalized = _normalize_whitespace(text)
    without_numbers, removed_numbers = _remove_synthetic_line_numbers(normalized)
    joined = _join_safe_broken_lines(without_numbers)
    return {
        "text": joined.strip(),
        "removed_synthetic_line_numbers": removed_numbers,
    }


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\x00", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    normalized_lines: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line
        if is_blank and previous_blank:
            continue
        normalized_lines.append(line)
        previous_blank = is_blank
    return "\n".join(normalized_lines).strip()


def _remove_synthetic_line_numbers(text: str) -> tuple[str, bool]:
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
        return text, False

    numbered_by_index = {index: body for index, _, body in numbered}
    normalized_lines = [
        numbered_by_index.get(index, line).strip() if line.strip() else ""
        for index, line in enumerate(lines)
    ]
    return "\n".join(normalized_lines).strip(), True


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


def _join_safe_broken_lines(text: str) -> str:
    paragraphs = text.split("\n\n")
    joined_paragraphs: list[str] = []
    for paragraph in paragraphs:
        lines = paragraph.splitlines()
        if not lines:
            continue
        joined_lines: list[str] = []
        for line in lines:
            if (
                joined_lines
                and joined_lines[-1]
                and not _SENTENCE_END_RE.search(joined_lines[-1])
                and _LOWERCASE_START_RE.match(line)
            ):
                joined_lines[-1] = f"{joined_lines[-1]} {line}"
            else:
                joined_lines.append(line)
        joined_paragraphs.append("\n".join(joined_lines))
    return "\n\n".join(joined_paragraphs)
