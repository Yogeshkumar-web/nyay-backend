import pytest
from pydantic import ValidationError

from app.features.documents.google_document_ai import (
    GoogleDocumentAiProcessor,
    _resolve_credentials_path,
    _text_from_anchor,
)
from app.features.documents.legal_form_extraction import (
    AccusedChargesheeted,
    IdentificationDetails,
    PersonBasicDetails,
    SarvamLegalFormExtractor,
    classify_iif_form,
)


def test_text_anchor_defaults_missing_start_index_to_zero() -> None:
    assert _text_from_anchor(
        "FIRST INFORMATION REPORT",
        {"textSegments": [{"endIndex": "5"}]},
    ) == "FIRST"


def test_windows_credentials_path_resolves_to_backend_secrets(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    credential = secrets / "google-docai-sa.json"
    credential.write_text("{}")

    assert _resolve_credentials_path(
        r"C:\projects\my-saas\backend\secrets\google-docai-sa.json"
    ) == "secrets/google-docai-sa.json"


def test_document_ai_paragraphs_are_sorted_by_rounded_y_then_x() -> None:
    processor = GoogleDocumentAiProcessor(
        project_id="project",
        location="us",
        processor_id="processor",
        credentials_path="unused.json",
        bucket="bucket",
    )
    document = {
        "text": "Right\nLeft\nNext row\n",
        "pages": [
            {
                "pageNumber": 1,
                "detectedLanguages": [{"languageCode": "hi", "confidence": 0.9}],
                "imageQualityScores": {"qualityScore": 0.9},
                "paragraphs": [
                    {
                        "layout": {
                            "textAnchor": {"textSegments": [{"endIndex": "6"}]},
                            "confidence": 0.8,
                            "boundingPoly": {
                                "normalizedVertices": [{"x": 0.7, "y": 0.101}]
                            },
                        }
                    },
                    {
                        "layout": {
                            "textAnchor": {
                                "textSegments": [{"startIndex": "6", "endIndex": "11"}]
                            },
                            "confidence": 1.0,
                            "boundingPoly": {
                                "normalizedVertices": [{"x": 0.1, "y": 0.104}]
                            },
                        }
                    },
                    {
                        "layout": {
                            "textAnchor": {
                                "textSegments": [{"startIndex": "11", "endIndex": "20"}]
                            },
                            "confidence": 0.9,
                            "boundingPoly": {
                                "normalizedVertices": [{"x": 0.1, "y": 0.2}]
                            },
                        }
                    },
                ],
            }
        ],
    }

    pages = processor._parse_document(document, page_number_offset=0)

    assert pages[0].text == "Left\nRight\nNext row"
    assert pages[0].language == "hi"
    assert pages[0].confidence == pytest.approx(0.9)


def test_document_ai_output_shards_support_restarted_page_numbers() -> None:
    processor = GoogleDocumentAiProcessor(
        project_id="project",
        location="us",
        processor_id="processor",
        credentials_path="unused.json",
        bucket="bucket",
    )
    payloads = [
        {"text": "one", "pages": [{"pageNumber": 1, "paragraphs": []}]},
        {"text": "two", "pages": [{"pageNumber": 1, "paragraphs": []}]},
    ]

    pages = processor._parse_output_documents(payloads)

    assert [page.page_number for page in pages] == [1, 2]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FIRST INFORMATION REPORT\n(Under Section 173 B.N.S.S.)", "fir"),
        ("FINAL FORM/REPORT\n(Under Section 193 B.N.S.S.)", "chargesheet"),
        ("AFFIDAVIT BEFORE THE HON'BLE COURT", None),
    ],
)
def test_iif_classifier_uses_distinctive_headers(text: str, expected: str | None) -> None:
    assert classify_iif_form(text) == expected


def test_chargesheeted_accused_cannot_be_marked_uncharged() -> None:
    with pytest.raises(ValidationError):
        AccusedChargesheeted.model_validate(
            {
                "details": PersonBasicDetails(name="Sample Person").model_dump(),
                "identification": IdentificationDetails().model_dump(),
                "is_charged": False,
            }
        )


def test_sarvam_payload_uses_strict_schema_and_source_grounding() -> None:
    extractor = SarvamLegalFormExtractor(api_key="test")
    model_schema = {
        "type": "object",
        "properties": {"header": {"type": "string"}},
        "required": ["header"],
        "additionalProperties": False,
    }

    payload = extractor._payload(
        "FIRST INFORMATION REPORT",
        "fir",
        model_schema,
    )

    assert payload["temperature"] == 0.1
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "Never invent" in payload["messages"][1]["content"]
