"""
Raw material parser for runtime ingest.

Converts heterogeneous user inputs (text, CSV, PDF) into normalized text
before semantic extraction.
"""

from __future__ import annotations

import base64
import csv
import io
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal


class MaterialParseError(ValueError):
    """Raised when raw material cannot be parsed into text."""


@dataclass
class ParsedMaterial:
    source_type: Literal["text", "csv", "pdf"]
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def parse_raw_material_record(record: Dict[str, Any]) -> ParsedMaterial:
    source_type = str(record.get("source_type", "text")).strip().lower()
    content = str(record.get("content", ""))
    encoding = str(record.get("content_encoding", "plain")).strip().lower()

    if source_type == "text":
        return ParsedMaterial(source_type="text", text=content, metadata={"parser": "plain_text"})
    if source_type == "csv":
        return _parse_csv_material(content)
    if source_type == "pdf":
        return _parse_pdf_material(content=content, encoding=encoding)
    raise MaterialParseError(f"unsupported source_type: {source_type}")


def _parse_csv_material(content: str, max_rows: int = 30) -> ParsedMaterial:
    buffer = io.StringIO(content)
    sample = content[:4096]
    has_header = csv.Sniffer().has_header(sample) if sample.strip() else False
    lines: List[str] = []
    row_count = 0

    if has_header:
        reader = csv.DictReader(buffer)
        fields = reader.fieldnames or []
        for idx, row in enumerate(reader):
            if idx >= max_rows:
                break
            row_count += 1
            pairs = [f"{key}={str(row.get(key, '')).strip()}" for key in fields if key]
            lines.append(f"row {idx + 1}: " + ", ".join(pairs))
        prelude = f"CSV table with columns: {', '.join(fields)}"
    else:
        buffer.seek(0)
        reader_plain = csv.reader(buffer)
        for idx, row in enumerate(reader_plain):
            if idx >= max_rows:
                break
            row_count += 1
            lines.append(f"row {idx + 1}: " + ", ".join(str(cell).strip() for cell in row))
        prelude = "CSV-like rows without detected header"

    text = prelude if not lines else prelude + "\n" + "\n".join(lines)
    return ParsedMaterial(
        source_type="csv",
        text=text,
        metadata={"parser": "csv", "rows_parsed": row_count, "has_header": has_header},
    )


def _parse_pdf_material(content: str, encoding: str) -> ParsedMaterial:
    payload: bytes
    if encoding == "base64":
        try:
            payload = base64.b64decode(content, validate=True)
        except Exception as exc:
            raise MaterialParseError(f"invalid base64 PDF payload: {exc}") from exc
    elif encoding == "plain":
        # Best effort fallback for already-decoded text-like payloads.
        return ParsedMaterial(
            source_type="pdf",
            text=content,
            metadata={"parser": "plain_fallback"},
            warnings=["pdf content_encoding=plain; treated as pre-extracted text"],
        )
    else:
        raise MaterialParseError(f"unsupported content_encoding for pdf: {encoding}")

    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise MaterialParseError(f"pypdf is not available: {exc}") from exc

    try:
        reader = PdfReader(io.BytesIO(payload))
    except Exception as exc:
        raise MaterialParseError(f"failed to read PDF payload: {exc}") from exc

    pages: List[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    text = "\n".join(chunk.strip() for chunk in pages if chunk and chunk.strip())

    if not text:
        ocr_text, ocr_reason, ocr_meta = _ocr_pdf_payload(payload)
        if ocr_text:
            return ParsedMaterial(
                source_type="pdf",
                text=ocr_text,
                metadata={"parser": "ocr_fallback", "pages": len(reader.pages), **ocr_meta},
                warnings=[ocr_reason],
            )
        raise MaterialParseError(f"PDF text extraction returned empty content ({ocr_reason})")

    return ParsedMaterial(
        source_type="pdf",
        text=text,
        metadata={"parser": "pypdf", "pages": len(reader.pages)},
    )


def _ocr_pdf_payload(payload: bytes) -> tuple[str, str, Dict[str, Any]]:
    """Best-effort OCR fallback for scanned PDFs.

    Uses optional dependencies (`fitz`/PyMuPDF + `pytesseract` + PIL).
    Returns extracted text, reason string, and metadata.
    """
    try:
        import fitz
    except Exception as exc:
        return "", f"OCR fallback unavailable: PyMuPDF import failed ({exc})", {}
    try:
        import pytesseract
    except Exception as exc:
        return "", f"OCR fallback unavailable: pytesseract import failed ({exc})", {}
    try:
        from PIL import Image
    except Exception as exc:
        return "", f"OCR fallback unavailable: Pillow import failed ({exc})", {}

    try:
        doc = fitz.open(stream=payload, filetype="pdf")
    except Exception as exc:
        return "", f"OCR fallback failed opening PDF ({exc})", {}

    chunks: List[str] = []
    dpi = 2.0
    for page in doc:
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi, dpi))
            mode = "RGBA" if pix.alpha else "RGB"
            image = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
            chunks.append(pytesseract.image_to_string(image) or "")
        except Exception:
            chunks.append("")

    text = "\n".join(chunk.strip() for chunk in chunks if chunk and chunk.strip())
    if not text:
        return "", "OCR fallback executed but produced empty text", {"ocr_engine": "tesseract"}
    return text, "Used OCR fallback for scanned PDF", {"ocr_engine": "tesseract"}
