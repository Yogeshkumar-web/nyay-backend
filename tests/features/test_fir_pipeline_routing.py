"""FIR typing pipeline routing tests."""
from app.features.documents.models import DocumentType
from app.workers.typing_tasks import _should_use_fir_template


FIR_TEXT = """
FIRST INFORMATION REPORT
(Under Section 173 B.N.S.S)
प्रथम सूचना रिपोर्ट
1. District/Unit (जिला/इकाई): मुरादाबाद
FIR No. (प्र.सू.रि. सं.): 0059
"""


def test_typing_template_routes_selected_fir():
    assert _should_use_fir_template(DocumentType.fir, "not enough OCR yet")


def test_typing_template_routes_auto_detected_fir_even_when_uploaded_as_other():
    assert _should_use_fir_template(DocumentType.other, FIR_TEXT)


def test_typing_template_leaves_non_fir_on_generic_path():
    assert not _should_use_fir_template(
        DocumentType.chargesheet,
        "Charge sheet with witness statements and no FIR heading.",
    )
