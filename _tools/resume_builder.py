#!/usr/bin/env python3
"""Gera currículo ATS em DOCX e PDF a partir de um spec JSON.

Uso:
    python resume_builder.py <spec.json> [--out-dir <dir>]
"""
import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.resume_compiler import ResumeCompiler, ResumeSpec  # noqa: E402


def load_spec(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_docx(spec: dict | ResumeSpec, out_path: str) -> None:
    buf = ResumeCompiler().compile_docx(spec)
    Path(out_path).write_bytes(buf.getvalue())


def build_pdf(spec: dict | ResumeSpec, out_path: str) -> int:
    buf, page_count = ResumeCompiler().compile_pdf(spec)
    Path(out_path).write_bytes(buf.getvalue())
    return page_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera currículo ATS em DOCX e PDF.")
    parser.add_argument("spec", help="Caminho do arquivo JSON com o conteúdo.")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Diretório de saída (default: mesmo do spec, pasta 'out').",
    )
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
