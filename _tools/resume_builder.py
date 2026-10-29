#!/usr/bin/env python3
"""Gera currículo ATS em DOCX e PDF a partir de um spec JSON.

Uso:
    python resume_builder.py <spec.json> [--out-dir <dir>]

O spec define o conteúdo do currículo com blocos tipados. Formato ATS-safe:
fonte padrão, layout de coluna única, sem tabelas/imagens, seções com
headings padronizados (Resumo, Competências, Experiência, etc.).
"""
import argparse
import json
import os
import re
import sys

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer
)

# Fontes TrueType com suporte completo a unicode (evita problemas de
# encoding em acentuação PT-BR, crítico para leitura por sistemas ATS).
_FONT_DIR = r"C:\Windows\Fonts"
pdfmetrics.registerFont(TTFont("Calibri", os.path.join(_FONT_DIR, "calibri.ttf")))
pdfmetrics.registerFont(TTFont("Calibri-Bold", os.path.join(_FONT_DIR, "calibrib.ttf")))
pdfmetrics.registerFont(TTFont("Calibri-Italic", os.path.join(_FONT_DIR, "calibrii.ttf")))

# ---------------------------------------------------------------- helpers

BULLET_CHAR = "\u2022"  # bullet "•"
CONTACT_SEP = " | "


def load_spec(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def blocks_for(section):
    """Retorna os blocos de conteúdo de uma seção, normalizando tipos."""
    return section.get("blocks", [])


# ---------------------------------------------------------------- DOCX

def add_docx_block(doc, block):
    btype = block.get("type")
    text = block.get("text", "")

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
        # linha separadora
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "1F3A5F")
        pBdr.append(bottom)
        pPr.append(pBdr)

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

    else:
        raise ValueError(f"Bloco DOCX desconhecido: {btype}")


def build_docx(spec, out_path):
    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(0.5)
        section.bottom_margin = Inches(0.5)
        section.left_margin = Inches(0.6)
        section.right_margin = Inches(0.6)

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)

    for section in spec["sections"]:
        if section.get("title", "").lower() != "header":
            add_docx_block(doc, {"type": "section", "text": section["title"]})
        for block in blocks_for(section):
            add_docx_block(doc, block)

    doc.save(out_path)


# ---------------------------------------------------------------- PDF

def build_pdf(spec, out_path):
    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=spec.get("filename_title", spec.get("name", "Resume")),
        author=spec.get("name", ""),
    )

    styles = {
        "title": ParagraphStyle("title", fontName="Calibri-Bold",
                                fontSize=17, leading=20, alignment=1,
                                spaceAfter=2),
        "contact": ParagraphStyle("contact", fontName="Calibri",
                                  fontSize=9.5, leading=12, alignment=1,
                                  spaceAfter=6),
        "section": ParagraphStyle("section", fontName="Calibri-Bold",
                                  fontSize=11, leading=13, textColor="#1F3A5F",
                                  spaceBefore=8, spaceAfter=3),
        "subheading": ParagraphStyle("subheading", fontName="Calibri-Bold",
                                     fontSize=10, leading=12, spaceBefore=4,
                                     spaceAfter=1),
        "paragraph": ParagraphStyle("paragraph", fontName="Calibri",
                                    fontSize=9.5, leading=12, spaceAfter=3),
        "bullet": ParagraphStyle("bullet", fontName="Calibri",
                                 fontSize=9.5, leading=12, leftIndent=8,
                                 spaceAfter=1.5),
        "skillline": ParagraphStyle("skillline", fontName="Calibri",
                                    fontSize=9.5, leading=12, spaceAfter=2),
    }

    story = []
    for section in spec["sections"]:
        if section.get("title", "").lower() != "header":
            story.append(Spacer(1, 1))
            story.append(Paragraph(section["title"].upper(), styles["section"]))
        for block in blocks_for(section):
            btype = block.get("type")
            text = block.get("text", "")

            if btype == "title":
                story.append(Paragraph(text, styles["title"]))
            elif btype == "contact":
                story.append(Paragraph(text.replace(CONTACT_SEP, "&nbsp;&bull;&nbsp;"),
                                       styles["contact"]))
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
            else:
                raise ValueError(f"Bloco PDF desconhecido: {btype}")

    doc.build(story)


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Gera currículo ATS em DOCX e PDF.")
    parser.add_argument("spec", help="Caminho do arquivo JSON com o conteúdo.")
    parser.add_argument("--out-dir", default=None,
                        help="Diretório de saída (default: mesmo do spec, pasta 'out').")
    args = parser.parse_args()

    spec = load_spec(args.spec)
    base = os.path.splitext(os.path.basename(args.spec))[0]
    out_dir = args.out_dir or os.path.join(os.path.dirname(args.spec), "out")
    os.makedirs(out_dir, exist_ok=True)

    docx_path = os.path.join(out_dir, base + ".docx")
    pdf_path = os.path.join(out_dir, base + ".pdf")

    build_docx(spec, docx_path)
    build_pdf(spec, pdf_path)

    print(f"OK: {docx_path}")
    print(f"OK: {pdf_path}")


if __name__ == "__main__":
    main()