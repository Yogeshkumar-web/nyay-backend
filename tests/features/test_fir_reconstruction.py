from app.features.documents.layout_artifact import extract_tables
from app.features.documents.fir_reconstruction import reconstruct_fir, render_fir_html
from tests.features.fir_sample import FIR_OCR_SAMPLE


def _sample_text() -> str:
    return FIR_OCR_SAMPLE


def test_fir_schema_extracts_golden_sample_metadata():
    schema = reconstruct_fir(_sample_text())

    assert schema is not None
    assert schema.basic["fir_no"] == "0059"
    assert schema.basic["police_station"] == "ठाकुरद्वारा"
    assert schema.basic["year"] == "2025"
    assert schema.basic["fir_datetime"] == "17/02/2025 14:21 घंटे"


def test_fir_schema_preserves_acts_and_accused_rows():
    schema = reconstruct_fir(_sample_text())

    assert schema is not None
    assert [row.section for row in schema.acts] == ["74", "76", "115(2)", "351(3)"]
    assert [row.name for row in schema.accused] == ["योगेश", "गौरव सिंह", "सौरव"]
    assert [row.father for row in schema.accused] == ["हरि सिंह", "हरि सिंह", "हरि सिंह"]


def test_fir_schema_keeps_signature_and_narrative_boundaries():
    schema = reconstruct_fir(_sample_text())

    assert schema is not None
    assert schema.action["officer_name"] == "THANA THAKURDWARA"
    assert "जान से मारने की धमकी दी" in schema.fir_contents
    assert "Action taken" not in schema.fir_contents


def test_layout_artifact_extracts_and_classifies_tables():
    artifact = _layout_artifact()

    tables = extract_tables(artifact)

    assert [table.table_type for table in tables] == ["acts_sections", "accused"]
    assert tables[0].headers == ["S.No.", "Acts", "Sections"]
    assert tables[0].rows[1][2].text == "76"
    assert tables[1].rows[0][1].text == "Yogesh"


def test_fir_schema_prefers_layout_tables_before_text_fallback():
    text = """
FIRST INFORMATION REPORT
1. District / Unit: Test District P.S. ( थाना ): Test PS Year ( वर्ष ): 2026
FIR No. : 0001
Date and Time of FIR: 01/01/2026 10:30 बजे
2. S.No. Acts Sections
3. (a) Occurrence of offence
4. Type of Information: Written
5. Place of Occurrence
(b) Address ( पता ): Test place
6. Complainant / Informant
(a) Name ( नाम ): Complainant
7. Details of known / suspected / unknown accused with full particulars
8. Reasons for delay
12. First Information contents:
Narrative text
13. Action taken:
Registered the case
15. Date and time of dispatch to the court:
"""

    schema = reconstruct_fir(text, ocr_artifact=_layout_artifact())

    assert schema is not None
    assert [row.section for row in schema.acts] == ["74", "76"]
    assert [row.name for row in schema.accused] == ["Yogesh", "Gaurav Singh"]
    assert [row.father for row in schema.accused] == ["Hari Singh", "Hari Singh"]
    assert schema.source_map["acts"] == "layout_table"
    assert schema.source_map["accused"] == "layout_table"
    assert schema.confidence_score > 0.7


def test_fir_schema_prefers_form_parser_key_values_for_page_one_fields():
    text = """
FIRST INFORMATION REPORT
1. District/Unit: Wrong District P.S. (थाना): Wrong PS Year (वर्ष): 1999
FIR No. (प्र.सू.रि. सं.): 0000
Date and Time of FIR (प्र.सू.रि. की दिनांक और समय): 01/01/1999 01:01 बजे
2. S.No. Acts Sections
1 Bharatiya Nyaya Sanhita, 2023 74
3. (a) Occurrence of offence:
Day (दिन): सोमवार
Date from (दिनांक से): 01/01/1999
Date To (दिनांक तक): 01/01/1999
Time Period (समय अवधि): पहर 1
Time From (समय से): 01:01 बजे
Time To (समय तक): 01:01 बजे
(b) Information received at P.S. Date (दिनांक): 01/01/1999 Time (समय): 01:01 बजे
(c) General Diary Reference Entry No. (प्रविष्टि सं.): 001
4. Type of Information: Written
12. First Information contents:
Narrative text
"""

    schema = reconstruct_fir(text, ocr_artifact=_page_one_form_artifact())

    assert schema is not None
    assert schema.basic == {
        "district": "मुरादाबाद",
        "police_station": "ठाकुरद्वारा",
        "year": "2025",
        "fir_no": "0059",
        "fir_datetime": "17/02/2025 14:21 घंटे",
    }
    assert schema.occurrence["day"] == "शनिवार"
    assert schema.occurrence["date_from"] == "15/02/2025"
    assert schema.occurrence["date_to"] == "15/02/2025"
    assert schema.occurrence["time_period"] == "पहर 7"
    assert schema.occurrence["time_from"] == "20:00 बजे"
    assert schema.occurrence["time_to"] == "20:00 बजे"
    assert schema.occurrence["info_date"] == "17/02/2025"
    assert schema.occurrence["info_time"] == "14:21 बजे"
    assert schema.occurrence["gd_entry"] == "058"
    assert schema.source_map["basic"] == "form_field"
    assert schema.source_map["occurrence"] == "form_field"


def _form_field(name: str, value: str) -> dict:
    return {
        "name": {"text": name, "confidence": 0.99},
        "value": {"text": value, "confidence": 0.99},
    }


def _page_one_form_artifact() -> dict:
    return {
        "schema_version": 1,
        "pages": [
            {
                "page_number": 1,
                "form_fields": [
                    _form_field("District/Unit (जिला/इकाई):", "मुरादाबाद"),
                    _form_field("P.S. (थाना):", "ठाकुरद्वारा"),
                    _form_field("Year (वर्ष):", "2025"),
                    _form_field("FIR No. (प्र.सू.रि. सं.):", "0059"),
                    _form_field(
                        "Date and Time of FIR (प्र.सू.रि. की दिनांक और समय):",
                        "17/02/2025 14:21 घंटे",
                    ),
                    _form_field("Day (दिन):", "शनिवार"),
                    _form_field("Date from (दिनांक से):", "15/02/2025"),
                    _form_field("Date To (दिनांक तक):", "15/02/2025"),
                    _form_field("Time Period (समय अवधि):", "पहर 7"),
                    _form_field("Time From (समय से):", "20:00 बजे"),
                    _form_field("Time To (समय तक):", "20:00 बजे"),
                    _form_field("Date (दिनांक):", "17/02/2025"),
                    _form_field("Time (समय):", "14:21 बजे"),
                    _form_field("Entry No. (प्रविष्टि सं.):", "058"),
                ],
            }
        ],
    }


def _layout_artifact() -> dict:
    return {
        "schema_version": 1,
        "pages": [
            {
                "page_number": 1,
                "tables": [
                    {
                        "header_rows": [
                            [
                                {"text": "S.No.", "confidence": 0.99},
                                {"text": "Acts", "confidence": 0.99},
                                {"text": "Sections", "confidence": 0.99},
                            ]
                        ],
                        "body_rows": [
                            [
                                {"text": "1", "confidence": 0.98},
                                {
                                    "text": "Bharatiya Nyaya Sanhita, 2023",
                                    "confidence": 0.98,
                                },
                                {"text": "74", "confidence": 0.98},
                            ],
                            [
                                {"text": "2", "confidence": 0.98},
                                {
                                    "text": "Bharatiya Nyaya Sanhita, 2023",
                                    "confidence": 0.98,
                                },
                                {"text": "76", "confidence": 0.98},
                            ],
                        ],
                    },
                    {
                        "header_rows": [
                            [
                                {"text": "S. No.", "confidence": 0.98},
                                {"text": "Name", "confidence": 0.98},
                                {"text": "Alias", "confidence": 0.98},
                                {"text": "Relative's Name", "confidence": 0.98},
                                {"text": "Present Address", "confidence": 0.98},
                            ]
                        ],
                        "body_rows": [
                            [
                                {"text": "1", "confidence": 0.97},
                                {"text": "Yogesh", "confidence": 0.97},
                                {"text": "", "confidence": 0.97},
                                {"text": "Hari Singh", "confidence": 0.97},
                                {"text": "Test address", "confidence": 0.97},
                            ],
                            [
                                {"text": "2", "confidence": 0.97},
                                {"text": "Gaurav Singh", "confidence": 0.97},
                                {"text": "", "confidence": 0.97},
                                {"text": "Hari Singh", "confidence": 0.97},
                                {"text": "Test address", "confidence": 0.97},
                            ],
                        ],
                    },
                ],
            }
        ],
    }


def test_fir_html_uses_canonical_tables_and_corrected_labels():
    html = render_fir_html(_sample_text()) or ""

    assert html.count("<table>") == 11
    assert "Property Category" in html
    assert "Propertty Category" not in html
    assert "<td>योगेश</td>" in html
    assert "<td>गौरव सिंह</td>" in html
    assert "<td>सौरव</td>" in html
    assert "THANA THAKURDWARA" in html


def test_fir_template_preserves_empty_form_sections():
    schema = reconstruct_fir(_sample_text())

    assert schema is not None
    from app.features.documents.fir_reconstruction import render_fir_schema_html

    html = render_fir_schema_html(schema)

    assert "9. Particulars of properties of interest" in html
    assert "10. Total value of property" in html
    assert "11. Inquest Report" in html
    assert "Attachment to item 7 of First Information Report" in html


def test_fir_schema_handles_ocr_reading_order_noise():
    noisy = """
FIRST INFORMATION REPORT
1. District/Unit (जिला/इकाई):
मुरादाबाद
FIR No. (प्र.सू.रि. सं.): 0059
(क्र.सं.)
2. S.No.
Acts (अधिनियम)
P.S. (थाना): ठाकुरद्वारा
Year (वर्ष): 2025
Date and Time of FIR (प्र.सू.रि. की दिनांक
और समय): 17/02/2025 14:21 घंटे
Sections (धारा(एँ))
भारतीय न्याय संहिता (बी 74
एन एस), 2023
भारतीय न्याय संहिता (बी 76
एन एस), 2023
भारतीय न्याय संहिता (बी 115(2)
एन एस), 2023
भारतीय न्याय संहिता (बी 351(3)
एन एस), 2023
3. (a) Occurrence of offence (अपराध की घटना):
1 Day (दिन): शनिवार
Date from (दिनांक से):
15/02/2025
Time From (समय से):
20:00 बजे
Time To (समय
तक): 20:00 बजे
Date (दिनांक):
17/02/2025
Time (समय):
14:21 बजे
Entry No. (प्रविष्टि सं.): 058
4. Type of Information (सूचना का प्रकार): लिखित
Date and Time (दिनांक और समय): 17/02/2025 14:21 बजे
5. Place of Occurrence (घटनास्थल):
6.
1. (a) Direction and distance from P.S. (थाना से दूरी और दिशा): दक्षिण, 06 कि. मी.
(b) Address (पता): ग्राम लालापुर पीपलसाना, थाना ठाकुरद्वारा मुरा0
Complainant / Informant (शिकायतकर्ता / सूचनाकर्ता):
(a) Name (नाम): खिलराज सिंह
(b) Father's Name (पिताका नाम): लेखराज सिंह
(c) Date/Year of Birth (जन्म तिथि / वर्ष): 1973
(d) Nationality (राष्ट्रीयता): भारत
ग्राम लालापुर पीपलसाना, ठाकुरद्वारा,
मुरादाबाद, उत्तर प्रदेश, भारत
Mobile (मोबाइल सं.): 91-97197XXXXX
7. Details of known / suspected / unknown accused with full particulars
योगेश
पिता का नाम : हरि सिंह
गौरव सिंह
पिता का नाम : हरि सिंह
सौरव
पिता का नाम : हरि सिंह
8. Reasons for delay
12. First Information contents (प्रथम सूचना तथ्य):
नकल तहरीर हिन्दी जान से मारने की धमकी दी
13. Action taken:
(2) Directed (Name of I.O.) (जांच अधिकारी का नाम): mayank partap
Rank (पद): उपनिरीक्षक/
अवर निरीक्षक
No. (सं.):
14. Signature
Name (नाम): THANA
THAKURDWARA
Rank (पद): I (Inspector)
No. (सं.): 9454404056
15. Date and time
"""
    schema = reconstruct_fir(noisy)

    assert schema is not None
    assert schema.basic["district"] == "मुरादाबाद"
    assert schema.basic["police_station"] == "ठाकुरद्वारा"
    assert schema.basic["fir_datetime"] == "17/02/2025 14:21 घंटे"
    assert [row.section for row in schema.acts] == ["74", "76", "115(2)", "351(3)"]
    assert schema.information_type == "लिखित"
    assert schema.occurrence["info_time"] == "14:21 बजे"
    assert schema.place["direction_distance"] == "दक्षिण, 06 कि. मी."
    assert schema.action["io_rank"] == "उपनिरीक्षक/ अवर निरीक्षक"
    assert schema.action["officer_name"] == "THANA THAKURDWARA"
