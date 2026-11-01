"""Best-effort text extraction for uploaded files.

The extractor is deliberately dependency-light: text formats are decoded
directly, Office/PDF use optional libraries, and everything else degrades to a
metadata-only attachment instead of failing the upload.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

MAX_CHARS = 2_000_000
CHUNK_CHARS = 2_000
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".log", ".json", ".jsonl", ".csv", ".tsv",
    ".yaml", ".yml", ".xml", ".html", ".htm", ".css", ".js", ".ts", ".tsx",
    ".py", ".sh", ".toml", ".ini",
}


@dataclass
class Extracted:
    text: str
    pages: int | None = None
    truncated: bool = False
    skipped: bool = False
    error: str | None = None


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> Extracted:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # noqa: BLE001
        return Extracted("", error=f"pypdf unavailable: {exc}")
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = len(reader.pages)
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n\n".join(parts).strip()
        return _cap(text, pages=pages)
    except Exception as exc:  # noqa: BLE001
        return Extracted("", error=f"pdf extract failed: {exc}")


def _extract_docx(data: bytes) -> Extracted:
    try:
        import docx
    except Exception as exc:  # noqa: BLE001
        return Extracted("", error=f"python-docx unavailable: {exc}")
    try:
        doc = docx.Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text]
        for table in doc.tables:
            for row in table.rows:
                parts.append("\t".join(cell.text for cell in row.cells))
        return _cap("\n".join(parts).strip(), pages=None)
    except Exception as exc:  # noqa: BLE001
        return Extracted("", error=f"docx extract failed: {exc}")


def _extract_pptx(data: bytes) -> Extracted:
    try:
        from pptx import Presentation
    except Exception as exc:  # noqa: BLE001
        return Extracted("", error=f"python-pptx unavailable: {exc}")
    try:
        prs = Presentation(io.BytesIO(data))
        parts: list[str] = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text:
                    parts.append(shape.text)
        return _cap("\n".join(parts).strip(), pages=len(prs.slides))
    except Exception as exc:  # noqa: BLE001
        return Extracted("", error=f"pptx extract failed: {exc}")


def _extract_xlsx(data: bytes) -> Extracted:
    try:
        from openpyxl import load_workbook
    except Exception as exc:  # noqa: BLE001
        return Extracted("", error=f"openpyxl unavailable: {exc}")
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts: list[str] = []
        for sheet in wb.worksheets:
            parts.append(f"# {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                values = [str(v) for v in row if v is not None]
                if values:
                    parts.append("\t".join(values))
        return _cap("\n".join(parts).strip(), pages=len(wb.worksheets))
    except Exception as exc:  # noqa: BLE001
        return Extracted("", error=f"xlsx extract failed: {exc}")


def _cap(text: str, *, pages: int | None) -> Extracted:
    truncated = len(text) > MAX_CHARS
    return Extracted(text[:MAX_CHARS].strip(), pages=pages, truncated=truncated)


def extract_text(filename: str, mime: str | None, data: bytes) -> Extracted:
    name = (filename or "file").lower()
    mime = (mime or "").lower()
    suffix = ""
    if "." in name:
        suffix = "." + name.rsplit(".", 1)[-1]
    try:
        if suffix == ".pdf" or mime == "application/pdf":
            return _extract_pdf(data)
        if suffix == ".docx" or "wordprocessingml" in mime:
            return _extract_docx(data)
        if suffix == ".pptx" or "presentationml" in mime:
            return _extract_pptx(data)
        if suffix in {".xlsx", ".xlsm"} or "spreadsheetml" in mime:
            return _extract_xlsx(data)
        if suffix == ".csv" or suffix == ".tsv" or "csv" in mime:
            text = _decode(data)
            try:
                dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
                rows = list(csv.reader(io.StringIO(text), dialect=dialect))
                text = "\n".join("\t".join(row) for row in rows)
            except Exception:  # noqa: BLE001
                pass
            return _cap(text, pages=None)
        if suffix in TEXT_SUFFIXES or mime.startswith("text/") or mime in {
            "application/json", "application/xml", "application/x-yaml"
        }:
            text = _decode(data)
            if suffix in {".json", ".jsonl"}:
                try:
                    parsed = json.loads(text)
                    text = json.dumps(parsed, ensure_ascii=False, indent=2)
                except Exception:  # noqa: BLE001
                    pass
            return _cap(text, pages=None)
        if mime.startswith("image/"):
            return Extracted("", skipped=True, error="image OCR not configured")
    except Exception as exc:  # noqa: BLE001
        return Extracted("", error=str(exc)[:300])
    return Extracted("", skipped=True, error=f"unsupported file type: {suffix or mime or name}")


def split_chunks(text: str) -> list[dict[str, Any]]:
    """Split into offset-preserving chunks suitable for read_attachment."""
    if not text:
        return []
    chunks: list[dict[str, Any]] = []
    start = 0
    seq = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        # Prefer a paragraph boundary within the tail of the chunk.
        if end < len(text):
            boundary = text.rfind("\n", start + CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary + 1
        body = text[start:end]
        chunks.append({
            "seq": seq,
            "text": body,
            "char_start": start,
            "char_end": end,
            "tokens": max(1, len(body)),
            "page_from": None,
            "page_to": None,
        })
        start = end
        seq += 1
    return chunks


def safe_name(name: str) -> str:
    base = (name or "file").replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    base = base.replace("..", ".")
    return base[:160] or "file"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
