import io
import json
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from backend.app.services.resume_compiler import (
    CoverLetterSpec,
    ResumeBlock,
    ResumeCompiler,
    ResumeSection,
    ResumeSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_SPEC_PATH = REPO_ROOT / "_tools" / "specs" / "akross_backend_estagiario.json"


def test_compile_sample_spec_to_docx_and_pdf():
    raw_data = json.loads(SAMPLE_SPEC_PATH.read_text(encoding="utf-8"))
    spec = ResumeSpec.model_validate(raw_data)
    compiler = ResumeCompiler()

    docx_buf = compiler.compile_docx(spec)
    assert isinstance(docx_buf, io.BytesIO)
    docx_bytes = docx_buf.getvalue()
    assert len(docx_bytes) > 0

    doc = Document(io.BytesIO(docx_bytes))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Davi Bernardes Machado" in full_text
    assert "RESUMO PROFISSIONAL" in full_text
    assert "\u2022 " in full_text

    pdf_buf, page_count = compiler.compile_pdf(spec)
    assert isinstance(pdf_buf, io.BytesIO)
    pdf_bytes = pdf_buf.getvalue()
    assert pdf_bytes.startswith(b"%PDF-")

    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert page_count == len(reader.pages)
    assert page_count >= 1
    extracted_pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Davi Bernardes Machado" in extracted_pdf_text


def test_pdf_multi_page_count_and_structured_spec():
    compiler = ResumeCompiler()
    spec = ResumeSpec(
        title="Jane Doe",
        contact="jane@example.com | São Paulo, SP",
        sections=[
            ResumeSection(
                title="Experience",
                blocks=[ResumeBlock(type="subheading", text="Senior Engineer | Acme Corp")],
                bullets=[
                    (
                        f"Delivered mission-critical distributed system milestone #{i} "
                        "with high availability and throughput across global clusters."
                    )
                    for i in range(65)
                ],
            )
        ],
    )

    pdf_buf, page_count = compiler.compile_pdf(spec)
    reader = PdfReader(pdf_buf)
    assert page_count == len(reader.pages)
    assert page_count >= 2


def test_compile_cover_letter_pdf():
    compiler = ResumeCompiler()
    letter = CoverLetterSpec(
        title="Davi Bernardes Machado",
        contact="São Paulo - SP | davibernardes021@gmail.com",
        recipient="Hiring Team - Acme Corp",
        subject="Application for Backend Software Engineer",
        greeting="Dear Hiring Manager,",
        paragraphs=[
            "I am writing to express my strong interest in the Backend Software Engineer position.",
            (
                "During my time at Itaú Unibanco, I automated critical workflows "
                "and built scalable REST APIs in Python and AWS."
            ),
        ],
        sign_off="Sincerely,\nDavi Bernardes Machado",
    )

    pdf_buf = compiler.compile_cover_letter_pdf(letter)
    assert isinstance(pdf_buf, io.BytesIO)
    pdf_bytes = pdf_buf.getvalue()
    assert pdf_bytes.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text() or ""
    assert "Acme Corp" in text
    assert "Itaú Unibanco" in text
