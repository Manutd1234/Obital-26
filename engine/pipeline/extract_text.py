from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from engine.pipeline.constants import SUPPORTED_INPUT_EXTENSIONS


def extract_text_from_path(input_path: str | Path) -> str:
    """Extract normalized text from a .pdf, .txt, or .md file."""
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        raw = path.read_text(encoding="utf-8", errors="replace")
    elif ext == ".pdf":
        raw = _extract_pdf_text(path)
    else:
        supported = ", ".join(sorted(SUPPORTED_INPUT_EXTENSIONS))
        raise ValueError(f"Unsupported file type: {ext}. Use one of: {supported}.")

    return _normalize_text(raw)


def _extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    chunks: list[str] = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def _normalize_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    non_empty_lines = [line for line in lines if line]
    return "\n".join(non_empty_lines).strip()
