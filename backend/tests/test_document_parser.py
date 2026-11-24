import io

import docx
import pytest
from reportlab.pdfgen import canvas

from backend.app.services.document_parser import extract_text_from_file


def _create_sample_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, text)
    c.save()
    return buf.getvalue()


def _create_sample_docx(text: str, table_data: list[list[str]] | None = None) -> bytes:
    doc = docx.Document()
    doc.add_paragraph(text)
    if table_data:
        table = doc.add_table(rows=len(table_data), cols=len(table_data[0]))
        for r_idx, row in enumerate(table_data):
            for c_idx, val in enumerate(row):
                table.cell(r_idx, c_idx).text = val
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_text_from_pdf():
    pdf_bytes = _create_sample_pdf("Engenheiro de Software Senior")
    result = extract_text_from_file("resume.pdf", pdf_bytes)
    assert "Engenheiro de Software Senior" in result


def test_extract_text_from_docx():
    docx_bytes = _create_sample_docx(
        "Desenvolvedor Full Stack", [["Python", "FastAPI"], ["React", "TypeScript"]]
    )
    result = extract_text_from_file("curriculo.docx", docx_bytes)
    assert "Desenvolvedor Full Stack" in result
    assert "Python | FastAPI" in result


def test_extract_text_from_markdown():
    md_content = b"# Davi Machado\n\n## Experiencia\n- Desenvolvedor backend Python"
    result = extract_text_from_file("perfil.md", md_content)
    assert "# Davi Machado" in result
    assert "Desenvolvedor backend Python" in result


def test_extract_text_from_txt_utf8():
    txt_content = b"Engenharia de Software e IA Generativa"
    result = extract_text_from_file("resumo.txt", txt_content)
    assert result == "Engenharia de Software e IA Generativa"


def test_extract_text_from_txt_latin1():
    latin1_content = "Acurácia e Conectividade".encode("latin-1")
    result = extract_text_from_file("notas.txt", latin1_content)
    assert "Acurácia e Conectividade" in result


def test_unsupported_extension():
    with pytest.raises(ValueError, match="Unsupported file format"):
        extract_text_from_file("document.png", b"fake binary data")


def test_empty_content():
    with pytest.raises(ValueError, match="File is empty"):
        extract_text_from_file("resume.pdf", b"")


def test_corrupted_pdf():
    with pytest.raises(ValueError, match="Failed to parse PDF"):
        extract_text_from_file("broken.pdf", b"Not a valid PDF format stream")


def test_corrupted_docx():
    with pytest.raises(ValueError, match="Failed to parse DOCX"):
        extract_text_from_file("broken.docx", b"Not a valid zip docx stream")


def test_empty_readable_text():
    # Empty string text file
    with pytest.raises(ValueError, match="contains no readable text"):
        extract_text_from_file("empty.txt", b"   \n\n   \t  ")
