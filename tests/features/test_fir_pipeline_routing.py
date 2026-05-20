from app.features.documents.models import DocumentType
from app.workers.ocr_tasks import _should_run_fir_second_pass
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


def test_ocr_second_pass_runs_for_detected_fir_from_generic_processor():
    assert _should_run_fir_second_pass(
        document_type=DocumentType.other,
        fir_auto_detected=True,
        first_processor="generic-processor",
        fir_processor="cc5157ec54665600",
    )


def test_ocr_second_pass_skips_when_already_using_fir_processor():
    assert not _should_run_fir_second_pass(
        document_type=DocumentType.other,
        fir_auto_detected=True,
        first_processor="cc5157ec54665600",
        fir_processor="cc5157ec54665600",
    )


def test_ocr_second_pass_skips_for_user_selected_fir():
    assert not _should_run_fir_second_pass(
        document_type=DocumentType.fir,
        fir_auto_detected=True,
        first_processor="cc5157ec54665600",
        fir_processor="cc5157ec54665600",
    )
