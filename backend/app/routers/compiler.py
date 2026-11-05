import base64
import re
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.services.resume_compiler import (
    CoverLetterSpec,
    ResumeCompiler,
    ResumeSpec,
)

router = APIRouter(prefix="/compiler", tags=["compiler"])

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

ExportFormat = Literal["pdf", "docx"]


class PreviewResponse(BaseModel):
    page_count: int
    is_valid_2_page: bool
    pdf_base64: str


class ExportResumeRequest(ResumeSpec):
    format: ExportFormat = "pdf"


class ExportCoverLetterRequest(CoverLetterSpec):
    format: ExportFormat = "pdf"
    filename_title: str = "Cover_Letter"


def _safe_filename(raw: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw.strip()).strip("_.")
    return cleaned or fallback


@router.post("/preview", response_model=PreviewResponse)
def preview_resume(spec: ResumeSpec) -> PreviewResponse:
    compiler = ResumeCompiler()
    pdf_buf, page_count = compiler.compile_pdf(spec)
    encoded = base64.b64encode(pdf_buf.getvalue()).decode("ascii")
    return PreviewResponse(
        page_count=page_count,
        is_valid_2_page=page_count <= 2,
        pdf_base64=encoded,
    )


@router.post("/export")
def export_resume(
    payload: ExportResumeRequest,
    format: ExportFormat | None = None,
) -> StreamingResponse:
    target_format: ExportFormat = format or payload.format
    compiler = ResumeCompiler()
    base_name = _safe_filename(payload.filename_title or payload.name or payload.title, "Resume")

    if target_format == "docx":
        buf = compiler.compile_docx(payload)
        media_type = DOCX_MIME
        ext = "docx"
    else:
        buf, _ = compiler.compile_pdf(payload)
        media_type = PDF_MIME
        ext = "pdf"

    headers = {"Content-Disposition": f'attachment; filename="{base_name}.{ext}"'}
    return StreamingResponse(buf, media_type=media_type, headers=headers)


@router.post("/cover-letter/export")
def export_cover_letter(
    payload: ExportCoverLetterRequest,
    format: ExportFormat | None = None,
) -> StreamingResponse:
    target_format: ExportFormat = format or payload.format
    compiler = ResumeCompiler()
    base_name = _safe_filename(
        payload.filename_title or payload.name or payload.title, "Cover_Letter"
    )

    if target_format == "docx":
        buf = compiler.compile_cover_letter_docx(payload)
        media_type = DOCX_MIME
        ext = "docx"
    else:
        buf = compiler.compile_cover_letter_pdf(payload)
        media_type = PDF_MIME
        ext = "pdf"

    headers = {"Content-Disposition": f'attachment; filename="{base_name}.{ext}"'}
    return StreamingResponse(buf, media_type=media_type, headers=headers)
