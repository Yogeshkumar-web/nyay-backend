from app.features.documents.canonical_document import build_canonical_document


def test_canonical_document_allows_variable_optional_and_repeatable_sections():
    content = """
[[PAGE 1 START]]
# FIRST INFORMATION REPORT

## 2. Acts and Sections

| S.No. | Act | Section |
| --- | --- | --- |
| 1 | BNS | 74 |
| 2 | BNS | 115(2) |

## 7. Details of accused

| S.No. | Name | Address |
| --- | --- | --- |
| 1 | A | Address A |
| 2 | B | Address B |
| 3 | C | Address C |

## State-specific additional section

This content must not be discarded.
[[PAGE 1 END]]
""".strip()

    document = build_canonical_document(content, document_type="fir")

    tables = [block for block in document.blocks if block.type == "table"]
    assert [len(table.rows) for table in tables] == [2, 3]
    assert tables[0].semantic_role == "applicable_acts"
    assert tables[1].semantic_role == "accused"
    assert not any(block.semantic_role == "victims" for block in document.blocks)
    assert any(
        block.text == "This content must not be discarded."
        and block.semantic_role == "generic"
        for block in document.blocks
    )


def test_canonical_document_tracks_source_page_confidence_and_review_flags():
    content = """
[[PAGE 1 START]]
# FIRST INFORMATION REPORT

Readable content.
[[PAGE 1 END]]

[[PAGE 2 START]]
## Witnesses

- First witness
- Second witness
[[PAGE 2 END]]
""".strip()

    document = build_canonical_document(
        content,
        document_type="fir",
        page_evidence=[
            {"page_number": 1, "confidence": 0.98},
            {
                "page_number": 2,
                "confidence": 0.72,
                "warnings": ["Verify handwritten name."],
                "layout": {
                    "blocks": [
                        {
                            "text": "Witnesses",
                            "confidence": 0.62,
                            "coordinates": [10, 20, 100, 40],
                        }
                    ]
                },
            },
        ],
    )

    page_two = [
        block for block in document.blocks if block.source_refs[0].page_number == 2
    ]
    assert page_two
    assert all(block.review_required for block in page_two)
    heading = next(block for block in page_two if block.heading == "Witnesses")
    assert heading.confidence == 0.62
    assert heading.source_refs[0].coordinates == [10.0, 20.0, 100.0, 40.0]
    assert next(block for block in page_two if block.type == "list").confidence == 0.72
    assert document.unresolved_block_count == len(page_two)
    assert document.warnings == ["Verify handwritten name."]


def test_canonical_document_keeps_arbitrary_numbered_narrative_as_text():
    content = """
1. यह बयान का पहला तथ्य है और इसे heading नहीं बनाना चाहिए क्योंकि यह लंबा narrative वाक्य है जिसमें पर्याप्त विवरण मौजूद है।
2. यह दूसरा तथ्य है जो उसी narrative का अगला हिस्सा है और इसका क्रम सुरक्षित रहना चाहिए।
""".strip()

    document = build_canonical_document(content, document_type="fir")

    assert len(document.blocks) == 1
    assert document.blocks[0].type == "list"
    assert len(document.blocks[0].items) == 2


def test_canonical_fir_reconstructs_plain_acts_and_html_accused_tables():
    content = """
[[PAGE 1 START]]
N.C.R.B. (एन.सी.आर.बी.)
I.I.F.-I (एकीकृत जाँच फार्म-I)
FIRST INFORMATION REPORT

1. District/Unit (जिला/इकाई): Bareilly
P.S. (थाना): Faridpur Year (वर्ष): 2025
FIR No. (प्र.सू.रि. सं.): 0205
Date & Time of FIR (प्र.सू.रि. की दिनांक/समय): 13/03/2025 09:40

2. S.No. Acts (अधिनियम)
(क्र.सं.)
1 Bharatiya Nyaya Sanhita (BNS), 2023 191(2)
2 Bharatiya Nyaya Sanhita (BNS), 2023 103(1)
[[PAGE 1 END]]

[[PAGE 2 START]]
7. Details of known accused
<table>
<thead><tr><th>S.No.</th><th>Name</th><th>Present Address</th></tr></thead>
<tbody>
<tr><td>1</td><td>A</td><td>Address A</td></tr>
<tr><td>2</td><td>B</td><td>Address B</td></tr>
<tr><td>3</td><td>C</td><td>Address C</td></tr>
</tbody>
</table>

12: First Information contents (प्रथम सूचना तथ्य)
Narrative remains available.
[[PAGE 2 END]]
""".strip()

    document = build_canonical_document(content, document_type="other")

    assert document.schema_version == 3
    assert document.document_type == "fir"
    assert not any("N.C.R.B" in (block.heading or "") for block in document.blocks)
    tables = [block for block in document.blocks if block.type == "table"]
    assert tables[0].semantic_role == "applicable_acts"
    assert tables[0].rows == [
        ["1", "Bharatiya Nyaya Sanhita (BNS), 2023", "191(2)"],
        ["2", "Bharatiya Nyaya Sanhita (BNS), 2023", "103(1)"],
    ]
    assert tables[1].semantic_role == "accused"
    assert len(tables[1].rows) == 3
    fir_details = next(
        block
        for block in document.blocks
        if block.type == "key_value"
        and block.semantic_role == "police_station_details"
    )
    assert [(field.label, field.value) for field in fir_details.fields] == [
        ("District / Unit", "Bareilly"),
        ("Police Station", "Faridpur"),
        ("Year", "2025"),
        ("FIR Number", "0205"),
        ("FIR Date & Time", "13/03/2025 09:40"),
    ]
    assert any(
        block.semantic_role == "incident_narrative"
        and block.text == "Narrative remains available."
        for block in document.blocks
    )
