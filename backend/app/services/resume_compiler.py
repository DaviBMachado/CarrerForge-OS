import io
import re
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor
from pydantic import BaseModel, Field
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

BULLET_CHAR = "\u2022"
CONTACT_SEP = " | "
FONT_FAMILY = "Carlito"

BlockType = Literal[
    "title",
    "contact",
    "section",
    "subheading",
    "paragraph",
    "bullet",
    "skillline",
]


class ResumeBlock(BaseModel):
    type: BlockType
    text: str


class ResumeSection(BaseModel):
    title: str
    blocks: list[ResumeBlock] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)

    def all_blocks(self) -> list[ResumeBlock]:
        combined = list(self.blocks)
        for bullet in self.bullets:
            combined.append(ResumeBlock(type="bullet", text=bullet))
        return combined


class ResumeSpec(BaseModel):
    name: str = ""
    title: str = ""
    contact: str | list[str] = ""
    filename_title: str = "Resume"
    sections: list[ResumeSection] = Field(default_factory=list)

    def resolved_sections(self) -> list[ResumeSection]:
        has_header = any(s.title.lower() == "header" for s in self.sections)
        if has_header or not (self.title or self.name or self.contact):
            return self.sections

        header_blocks: list[ResumeBlock] = []
        display_title = self.title or self.name
        if display_title:
            header_blocks.append(ResumeBlock(type="title", text=display_title))
        if isinstance(self.contact, list):
            for line in self.contact:
                if line:
                    header_blocks.append(ResumeBlock(type="contact", text=line))
        elif self.contact:
            header_blocks.append(ResumeBlock(type="contact", text=self.contact))

        return [ResumeSection(title="Header", blocks=header_blocks), *self.sections]


class CoverLetterSpec(BaseModel):
    title: str = ""
    name: str = ""
    contact: str | list[str] = ""
    date: str = ""
    recipient: str = ""
    subject: str = ""
    greeting: str = "Dear Hiring Manager,"
    paragraphs: list[str] = Field(default_factory=list)
    body: str = ""
    sign_off: str = ""


def _resolve_font_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    candidates = [
        repo_root / "assets" / "fonts",
        repo_root / "backend" / "assets" / "fonts",
    ]
    for candidate in candidates:
        if (candidate / "Carlito-Regular.ttf").is_file():
            return candidate
    raise FileNotFoundError("Bundled fonts not found in assets/fonts/Carlito-*.ttf")


def _register_fonts(font_dir: Path | None = None) -> None:
    resolved_dir = font_dir or _resolve_font_dir()
    registered = set(pdfmetrics.getRegisteredFontNames())
    font_map = {
        FONT_FAMILY: resolved_dir / "Carlito-Regular.ttf",
        f"{FONT_FAMILY}-Bold": resolved_dir / "Carlito-Bold.ttf",
        f"{FONT_FAMILY}-Italic": resolved_dir / "Carlito-Italic.ttf",
    }
    for font_name, font_path in font_map.items():
        if font_name not in registered:
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))


def _pdf_styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "title",
            fontName=f"{FONT_FAMILY}-Bold",
            fontSize=17,
            leading=20,
            alignment=1,
            spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "contact",
            fontName=FONT_FAMILY,
            fontSize=9.5,
            leading=12,
            alignment=1,
            spaceAfter=6,
        ),
        "section": ParagraphStyle(
            "section",
            fontName=f"{FONT_FAMILY}-Bold",
            fontSize=11,
            leading=13,
            textColor=HexColor("#1F3A5F"),
            spaceBefore=8,
            spaceAfter=3,
        ),
        "subheading": ParagraphStyle(
            "subheading",
            fontName=f"{FONT_FAMILY}-Bold",
            fontSize=10,
            leading=12,
            spaceBefore=4,
            spaceAfter=1,
        ),
        "paragraph": ParagraphStyle(
            "paragraph",
            fontName=FONT_FAMILY,
            fontSize=9.5,
            leading=12,
            spaceAfter=3,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            fontName=FONT_FAMILY,
            fontSize=9.5,
            leading=12,
            leftIndent=8,
            spaceAfter=1.5,
        ),
        "skillline": ParagraphStyle(
            "skillline",
            fontName=FONT_FAMILY,
            fontSize=9.5,
            leading=12,
            spaceAfter=2,
        ),
    }


def _add_docx_block(doc: Any, block: ResumeBlock) -> None:
    btype = block.type
    text = block.text

    if btype == "title":
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        r.bold = True
        r.font.size = Pt(18)
        r.font.name = "Calibri"

    elif btype == "contact":
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        r.font.size = Pt(10)
        r.font.name = "Calibri"
        p.paragraph_format.space_after = Pt(6)

    elif btype == "section":
        p = doc.add_paragraph()
        r = p.add_run(text.upper())
        r.bold = True
        r.font.size = Pt(11)
        r.font.name = "Calibri"
        r.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
        p_pr = p._p.get_or_add_pPr()
        p_bdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "1F3A5F")
        p_bdr.append(bottom)
        p_pr.append(p_bdr)

    elif btype == "subheading":
        p = doc.add_paragraph()
        r = p.add_run(text)
        r.bold = True
        r.font.size = Pt(10.5)
        r.font.name = "Calibri"
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(2)

    elif btype == "paragraph":
        p = doc.add_paragraph()
        r = p.add_run(text)
        r.font.size = Pt(10)
        r.font.name = "Calibri"
        p.paragraph_format.space_after = Pt(4)

    elif btype == "bullet":
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Inches(0.22)
        p.paragraph_format.space_after = Pt(2)
        r1 = p.add_run(BULLET_CHAR + " ")
        r1.font.size = Pt(10)
        r1.font.name = "Calibri"
        r2 = p.add_run(text)
        r2.font.size = Pt(10)
        r2.font.name = "Calibri"

    elif btype == "skillline":
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        for i, part in enumerate(text.split("||")):
            part = part.strip()
            if not part:
                continue
            if i > 0:
                sep = p.add_run("  |  ")
                sep.font.size = Pt(10)
                sep.font.name = "Calibri"
            for seg_i, seg in enumerate(re.split(r"\*\*(.+?)\*\*", part)):
                if not seg:
                    continue
                run = p.add_run(seg)
                run.font.size = Pt(10)
                run.font.name = "Calibri"
                if seg_i % 2 == 1:
                    run.bold = True


class ResumeCompiler:
    def __init__(self, font_dir: Path | None = None) -> None:
        _register_fonts(font_dir)

    @staticmethod
    def _coerce_spec(spec: ResumeSpec | dict[str, Any]) -> ResumeSpec:
        if isinstance(spec, ResumeSpec):
            return spec
        return ResumeSpec.model_validate(spec)

    @staticmethod
    def _coerce_cover_letter(
        letter: CoverLetterSpec | dict[str, Any],
    ) -> CoverLetterSpec:
        if isinstance(letter, CoverLetterSpec):
            return letter
        return CoverLetterSpec.model_validate(letter)

    def compile_docx(self, spec: ResumeSpec | dict[str, Any]) -> io.BytesIO:
        parsed = self._coerce_spec(spec)
        doc = Document()
        for section in doc.sections:
            section.top_margin = Mm(15)
            section.bottom_margin = Mm(15)
            section.left_margin = Mm(15)
            section.right_margin = Mm(15)

        normal = doc.styles["Normal"]
        normal.font.name = "Calibri"  # type: ignore[union-attr]
        normal.font.size = Pt(10)  # type: ignore[union-attr]

        for section in parsed.resolved_sections():
            if section.title.lower() != "header":
                _add_docx_block(doc, ResumeBlock(type="section", text=section.title))
            for block in section.all_blocks():
                _add_docx_block(doc, block)

        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return buf

    def compile_pdf(self, spec: ResumeSpec | dict[str, Any]) -> tuple[io.BytesIO, int]:
        parsed = self._coerce_spec(spec)
        buf = io.BytesIO()
        author = parsed.name or parsed.title
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
            title=parsed.filename_title or author or "Resume",
            author=author,
        )

        styles = _pdf_styles()
        story: list[Any] = []

        for section in parsed.resolved_sections():
            if section.title.lower() != "header":
                story.append(Spacer(1, 1))
                story.append(Paragraph(escape(section.title.upper()), styles["section"]))
            for block in section.all_blocks():
                btype = block.type
                text = escape(block.text)

                if btype == "title":
                    story.append(Paragraph(text, styles["title"]))
                elif btype == "contact":
                    story.append(
                        Paragraph(
                            text.replace(CONTACT_SEP, "&nbsp;&bull;&nbsp;"),
                            styles["contact"],
                        )
                    )
                elif btype == "section":
                    story.append(Spacer(1, 1))
                    story.append(Paragraph(text.upper(), styles["section"]))
                elif btype == "subheading":
                    story.append(Paragraph(text, styles["subheading"]))
                elif btype == "paragraph":
                    story.append(Paragraph(text, styles["paragraph"]))
                elif btype == "bullet":
                    story.append(Paragraph(f"{BULLET_CHAR}&nbsp;{text}", styles["bullet"]))
                elif btype == "skillline":
                    for i, part in enumerate(text.split("||")):
                        part = part.strip()
                        if not part:
                            continue
                        html = ("&nbsp;&nbsp;|&nbsp;&nbsp;" if i > 0 else "") + re.sub(
                            r"\*\*(.+?)\*\*", r"<b>\1</b>", part
                        )
                        story.append(Paragraph(html, styles["skillline"]))

        doc.build(story)
        page_count = int(getattr(doc, "page", 1))
        buf.seek(0)
        return buf, page_count

    def compile_cover_letter_pdf(self, letter: CoverLetterSpec | dict[str, Any]) -> io.BytesIO:
        parsed = self._coerce_cover_letter(letter)
        buf = io.BytesIO()
        display_title = parsed.title or parsed.name or "Cover Letter"
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
            title=f"Cover Letter - {display_title}",
            author=display_title,
        )

        styles = _pdf_styles()
        story: list[Any] = []

        if parsed.title or parsed.name:
            story.append(Paragraph(escape(display_title), styles["title"]))

        contacts = (
            parsed.contact
            if isinstance(parsed.contact, list)
            else ([parsed.contact] if parsed.contact else [])
        )
        for line in contacts:
            story.append(
                Paragraph(
                    escape(line).replace(CONTACT_SEP, "&nbsp;&bull;&nbsp;"),
                    styles["contact"],
                )
            )

        story.append(Spacer(1, 8))

        if parsed.date:
            story.append(Paragraph(escape(parsed.date), styles["paragraph"]))
        if parsed.recipient:
            story.append(Paragraph(escape(parsed.recipient), styles["subheading"]))
        if parsed.subject:
            story.append(Paragraph(f"<b>{escape(parsed.subject)}</b>", styles["paragraph"]))
        if parsed.greeting:
            story.append(Spacer(1, 4))
            story.append(Paragraph(escape(parsed.greeting), styles["paragraph"]))

        paragraphs = list(parsed.paragraphs)
        if parsed.body:
            paragraphs.extend(p.strip() for p in parsed.body.split("\n\n") if p.strip())
        for para in paragraphs:
            story.append(Spacer(1, 4))
            story.append(Paragraph(escape(para), styles["paragraph"]))

        if parsed.sign_off:
            story.append(Spacer(1, 8))
            for line in parsed.sign_off.splitlines():
                if line.strip():
                    story.append(Paragraph(escape(line.strip()), styles["paragraph"]))

        doc.build(story)
        buf.seek(0)
        return buf
