import io
import re
from pathlib import Path
from typing import Final

import docx
import pypdf

SUPPORTED_EXTENSIONS: Final[set[str]] = {".pdf", ".docx", ".md", ".txt"}


def _clean_text(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _extract_pdf(content: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("PDF is password protected and cannot be read.")
        pages_text: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages_text.append(text.strip())
        return "\n\n".join(pages_text)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to parse PDF document: {exc}") from exc


def _extract_docx(content: bytes) -> str:
    try:
        doc = docx.Document(io.BytesIO(content))
        parts: list[str] = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                parts.append(text)
        for table in doc.tables:
            for row in table.rows:
                row_cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if row_cells:
                    parts.append(" | ".join(row_cells))
        return "\n\n".join(parts)
    except Exception as exc:
        raise ValueError(f"Failed to parse DOCX document: {exc}") from exc


def _extract_plain_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return content.decode("latin-1")
        except Exception as exc:
            raise ValueError(f"Failed to decode text document: {exc}") from exc


def extract_text_from_file(filename: str, content: bytes) -> str:
    if not content:
        raise ValueError("File is empty.")

    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(
            f"Unsupported file format '{ext or filename}'. Supported formats: {supported}"
        )

    if ext == ".pdf":
        raw = _extract_pdf(content)
    elif ext == ".docx":
        raw = _extract_docx(content)
    elif ext in {".md", ".txt"}:
        raw = _extract_plain_text(content)
    else:
        raise ValueError(f"Unhandled file extension: {ext}")

    cleaned = _clean_text(raw)
    if not cleaned:
        raise ValueError("File contains no readable text.")

    return cleaned
