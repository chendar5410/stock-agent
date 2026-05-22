"""Extract text from uploaded company documents (PDFs, transcripts)."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass
class ParsedDocument:
    filename: str
    kind: str  # "pdf" | "text"
    text: str
    pages: int  # 0 for plain text


def _extract_pdf(name: str, data: bytes) -> ParsedDocument:
    reader = PdfReader(io.BytesIO(data))
    chunks: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        page_text = page_text.strip()
        if page_text:
            chunks.append(f"[page {i}]\n{page_text}")
    return ParsedDocument(
        filename=name,
        kind="pdf",
        text="\n\n".join(chunks),
        pages=len(reader.pages),
    )


def _extract_text(name: str, data: bytes) -> ParsedDocument:
    text = data.decode("utf-8", errors="replace")
    return ParsedDocument(filename=name, kind="text", text=text, pages=0)


def parse_upload(filename: str, data: bytes) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(filename, data)
    if suffix in {".txt", ".md", ".markdown", ".text", ""}:
        return _extract_text(filename, data)
    raise ValueError(
        f"Unsupported file type '{suffix}' for {filename}. "
        "Upload PDFs (.pdf) or plain text (.txt, .md)."
    )


def render_for_prompt(docs: list[ParsedDocument]) -> str:
    """Concatenate parsed documents into a single labeled block for the LLM."""
    parts: list[str] = []
    for d in docs:
        header = f"=== DOCUMENT: {d.filename} ({d.kind}"
        header += f", {d.pages} pages" if d.pages else ""
        header += ") ==="
        parts.append(f"{header}\n\n{d.text.strip()}\n")
    return "\n\n".join(parts)
