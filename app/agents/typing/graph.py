"""
VakilSuite Typing Agent — LangGraph graph definition.

Schema-first FIR architecture:

  fetch_and_batch_pages   — FIR: skip image fetch (no images needed)
    → assess_ocr_quality  — FIR: text-only heuristic (no Claude call)
    → detect_document_structure  — FIR: deterministic regex parser (fir_parser.py)
    → validate_fir_fields        — FIR: field audit + gap-fill (no LLM)
    → reformat_sections          — FIR: tehreer-only LLM cleanup; rest passthrough
    → self_review                — FIR: auto-pass (no LLM distortion)
    → assemble_html              — FIR: tabular/narrative layout-aware renderer
    → END

For non-FIR documents all nodes run in full (Claude multimodal throughout).

FIR pipeline LLM calls: 1 (tehreer cleanup only)
Generic pipeline LLM calls: assess + detect-per-batch + reformat-per-group + review
"""
from langgraph.graph import END, StateGraph

from app.agents.typing.nodes import (
    assess,
    assemble,
    detect,
    fetch_pages,
    reformat,
    review,
)
from app.agents.typing.nodes.validate import validate_fir_fields
from app.agents.typing.state import TypingAgentState


def build_typing_graph():
    g = StateGraph(TypingAgentState)

    g.add_node("fetch_and_batch_pages", fetch_pages.fetch_and_batch_pages)
    g.add_node("assess_ocr_quality", assess.assess_ocr_quality)
    g.add_node("detect_document_structure", detect.detect_document_structure)
    g.add_node("validate_fir_fields", validate_fir_fields)  # NEW — Phase 4
    g.add_node("reformat_sections", reformat.reformat_sections)
    g.add_node("self_review", review.self_review)
    g.add_node("assemble_html", assemble.assemble_html)

    g.set_entry_point("fetch_and_batch_pages")
    g.add_edge("fetch_and_batch_pages", "assess_ocr_quality")
    g.add_edge("assess_ocr_quality", "detect_document_structure")
    g.add_edge("detect_document_structure", "validate_fir_fields")  # NEW
    g.add_edge("validate_fir_fields", "reformat_sections")  # NEW
    g.add_edge("reformat_sections", "self_review")

    # Conditional: review_router returns "retry" → reformat_sections
    #                                    "continue" → assemble_html
    # FIR: self_review always returns "continue" (auto-pass) — no retry loop
    g.add_conditional_edges(
        "self_review",
        review.review_router,
        {"retry": "reformat_sections", "continue": "assemble_html"},
    )

    g.add_edge("assemble_html", END)

    return g.compile()


# Module-level singleton — imported by typing_tasks.py
typing_graph = build_typing_graph()
