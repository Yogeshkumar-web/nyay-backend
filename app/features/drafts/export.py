"""
Synchronous PDF and DOCX generation from draft content.
Handles both raw markdown (AI output) and Tiptap HTML (after user save).
Used by the direct download endpoint — no Celery, no R2.
"""
import io
import re
from bs4 import BeautifulSoup
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from xhtml2pdf import pisa


# ============================================================
# MARKDOWN → HTML CONVERTER
# ============================================================


def _inline_md(text: str) -> str:
    """Convert inline markdown (bold, italic) to HTML."""
    # Bold + italic
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic (single asterisk, not greedy across lines)
    text = re.sub(r"\*([^*\n]+?)\*", r"<em>\1</em>", text)
    return text


def _md_to_html(text: str) -> str:
    """
    Lightweight markdown → HTML for AI-generated legal document patterns.
    Handles: headings, bold/italic, horizontal rules, paragraphs, lists.
    Falls through immediately if content already contains HTML tags.
    """
    if re.search(r"<[a-zA-Z][^>]*>", text):
        return text  # already HTML — from Tiptap save

    html_parts: list[str] = []
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Split into blocks on blank lines
    blocks = re.split(r"\n{2,}", text.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Horizontal rule
        if re.match(r"^[-*_]{3,}$", block):
            html_parts.append("<hr/>")
            continue

        # Heading
        m = re.match(r"^(#{1,6})\s+(.*)", block)
        if m:
            level = len(m.group(1))
            content = _inline_md(m.group(2).strip())
            html_parts.append(f"<h{level}>{content}</h{level}>")
            continue

        # Unordered list (lines starting with - or *)
        lines = block.split("\n")
        if all(re.match(r"^[-*]\s+", line.strip()) for line in lines if line.strip()):
            items = "".join(
                f'<li>{_inline_md(re.sub(r"^[-*]\s+", "", line.strip()))}</li>'
                for line in lines
                if line.strip()
            )
            html_parts.append(f"<ul>{items}</ul>")
            continue

        # Ordered list
        if all(
            re.match(r"^\d+[.)]\s+", line.strip()) for line in lines if line.strip()
        ):
            items = "".join(
                f'<li>{_inline_md(re.sub(r"^\d+[.)]\s+", "", line.strip()))}</li>'
                for line in lines
                if line.strip()
            )
            html_parts.append(f"<ol>{items}</ol>")
            continue

        # Plain paragraph — join lines within the block with a space
        combined = " ".join(line.strip() for line in lines if line.strip())
        html_parts.append(f"<p>{_inline_md(combined)}</p>")

    return "\n".join(html_parts)


# ============================================================
# PDF/DOCX CSS
# ============================================================

_A4_CSS = """
@page {
    size: A4;
    margin-left: 3cm;
    margin-right: 2cm;
    margin-top: 2.5cm;
    margin-bottom: 2.5cm;
}
body {
    font-family: "Times New Roman", Times, serif;
    font-size: 13pt;
    line-height: 1.8;
    color: #000;
}
p {
    text-align: justify;
    margin-bottom: 8pt;
    orphans: 3;
    widows: 3;
}
h1, h2, h3 {
    font-weight: bold;
    text-align: center;
    margin-top: 14pt;
    margin-bottom: 6pt;
}
h1 { font-size: 14pt; text-transform: uppercase; }
h2 { font-size: 13pt; text-transform: uppercase; }
h3 { font-size: 13pt; }
ul, ol { margin-left: 1.5cm; margin-bottom: 8pt; }
li { margin-bottom: 3pt; }
strong { font-weight: bold; }
em { font-style: italic; }
hr { border: none; border-top: 1px solid #000; margin: 10pt 0; }
table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
    margin: 8pt 0 12pt;
}
th, td {
    border: 1px solid #555;
    padding: 5pt 6pt;
    vertical-align: top;
    line-height: 1.35;
    word-wrap: break-word;
}
th {
    font-weight: bold;
    background-color: #f5f5f5;
}
"""


# ============================================================
# PUBLIC API
# ============================================================


def generate_pdf(content: str, title: str) -> bytes:
    """Convert markdown or Tiptap HTML to A4 PDF using xhtml2pdf."""
    html_body = _md_to_html(content)
    styled = (
        f"<html><head>"
        f'<meta charset="utf-8"/>'
        f"<style>{_A4_CSS}</style>"
        f"</head><body>{html_body}</body></html>"
    )
    buf = io.BytesIO()
    pisa.CreatePDF(styled, dest=buf, encoding="utf-8")
    return buf.getvalue()


def generate_docx(content: str, title: str) -> bytes:
    """Convert markdown or Tiptap HTML to DOCX using python-docx + BeautifulSoup."""
    html_body = _md_to_html(content)

    doc = Document()

    # A4 page margins (legal style)
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(3)
    section.right_margin = Cm(2)
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)

    # Default paragraph style — Times New Roman 13pt, 1.8 line spacing
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(13)
    para_fmt = style.paragraph_format
    para_fmt.space_after = Pt(6)
    para_fmt.line_spacing = Pt(23)

    soup = BeautifulSoup(html_body, "html.parser")
    body = soup.body or soup

    def _apply_inline(run, tag):
        """Walk ancestors to apply bold/italic/underline."""
        for parent in tag.parents:
            if parent.name in ("strong", "b"):
                run.bold = True
            if parent.name in ("em", "i"):
                run.italic = True
            if parent.name == "u":
                run.underline = True

    def _add_para(
        el,
        alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
        bold_all=False,
        font_size: int | None = None,
    ):
        p = doc.add_paragraph(style="Normal")
        p.alignment = alignment
        for child in el.children:
            if hasattr(child, "name") and child.name:
                run = p.add_run(child.get_text())
                _apply_inline(run, child)
            else:
                run = p.add_run(str(child))
            if bold_all:
                run.bold = True
            if font_size:
                run.font.size = Pt(font_size)
        return p

    for el in body.children:
        if not hasattr(el, "name") or not el.name:
            continue
        tag = el.name
        text = el.get_text().strip()
        if not text:
            continue

        if tag == "h1":
            _add_para(
                el, alignment=WD_ALIGN_PARAGRAPH.CENTER, bold_all=True, font_size=14
            )
        elif tag == "h2":
            _add_para(
                el, alignment=WD_ALIGN_PARAGRAPH.CENTER, bold_all=True, font_size=13
            )
        elif tag == "h3":
            _add_para(el, bold_all=True)
        elif tag == "hr":
            # Thin horizontal rule via bottom border on an empty paragraph
            p = doc.add_paragraph()
            from docx.oxml.ns import qn
            from docx.oxml import OxmlElement

            pPr = p._p.get_or_add_pPr()
            pBdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:space"), "1")
            bottom.set(qn("w:color"), "000000")
            pBdr.append(bottom)
            pPr.append(pBdr)
        elif tag in ("ul", "ol"):
            for i, li in enumerate(el.find_all("li", recursive=False), start=1):
                bullet = "•" if tag == "ul" else f"{i}."
                p = doc.add_paragraph(style="Normal")
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                p.paragraph_format.left_indent = Cm(1.5)
                p.paragraph_format.first_line_indent = Cm(-0.7)
                p.add_run(f"{bullet}  {li.get_text().strip()}")
        else:
            _add_para(el)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
