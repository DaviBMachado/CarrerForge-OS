import base64
import io
import json
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfReader

from backend.app.services.resume_compiler import (
    CoverLetterSpec,
    ResumeBlock,
    ResumeCompiler,
    ResumeSection,
    ResumeSpec,
)
from backend.main import app

SAMPLE_SPEC_PATH = Path(__file__).resolve().parent / "fixtures" / "sample_spec.json"


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


def test_compiler_preview_endpoint():
    client = TestClient(app)
    raw_data = json.loads(SAMPLE_SPEC_PATH.read_text(encoding="utf-8"))

    res = client.post("/api/compiler/preview", json=raw_data)
    assert res.status_code == 200
    body = res.json()
    assert body["page_count"] >= 1
    assert body["is_valid_2_page"] is (body["page_count"] <= 2)
    pdf_bytes = base64.b64decode(body["pdf_base64"])
    assert pdf_bytes.startswith(b"%PDF-")


def test_compiler_preview_detects_over_2_pages():
    client = TestClient(app)
    payload = {
        "title": "Jane Doe",
        "contact": "jane@example.com",
        "sections": [
            {
                "title": "Experience",
                "bullets": [
                    f"Delivered distributed architecture milestone #{i} with high availability "
                    "and low latency across multiple global cloud regions and availability zones."
                    for i in range(95)
                ],
            }
        ],
    }
    res = client.post("/api/compiler/preview", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["page_count"] >= 3
    assert body["is_valid_2_page"] is False


def test_compiler_export_pdf_and_docx_endpoints():
    client = TestClient(app)
    raw_data = json.loads(SAMPLE_SPEC_PATH.read_text(encoding="utf-8"))

    res_pdf = client.post("/api/compiler/export", json={**raw_data, "format": "pdf"})
    assert res_pdf.status_code == 200
    assert res_pdf.headers["content-type"] == "application/pdf"
    assert "attachment;" in res_pdf.headers["content-disposition"]
    assert '.pdf"' in res_pdf.headers["content-disposition"]
    assert res_pdf.content.startswith(b"%PDF-")

    res_docx = client.post("/api/compiler/export", json={**raw_data, "format": "docx"})
    assert res_docx.status_code == 200
    assert (
        res_docx.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "attachment;" in res_docx.headers["content-disposition"]
    assert '.docx"' in res_docx.headers["content-disposition"]
    doc = Document(io.BytesIO(res_docx.content))
    assert any("Davi Bernardes Machado" in p.text for p in doc.paragraphs)


def test_compiler_cover_letter_export_endpoints():
    client = TestClient(app)
    payload = {
        "title": "Davi Bernardes Machado",
        "contact": "São Paulo - SP | davibernardes021@gmail.com",
        "recipient": "Hiring Team - Acme Corp",
        "subject": "Application for Backend Engineer",
        "paragraphs": ["Excited to apply for the Backend Engineer role."],
        "sign_off": "Sincerely,\nDavi",
    }

    res_pdf = client.post("/api/compiler/cover-letter/export", json=payload)
    assert res_pdf.status_code == 200
    assert res_pdf.headers["content-type"] == "application/pdf"
    assert "attachment;" in res_pdf.headers["content-disposition"]
    assert res_pdf.content.startswith(b"%PDF-")

    res_docx = client.post(
        "/api/compiler/cover-letter/export?format=docx",
        json=payload,
    )
    assert res_docx.status_code == 200
    assert (
        res_docx.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    doc = Document(io.BytesIO(res_docx.content))
    assert any("Acme Corp" in p.text for p in doc.paragraphs)
