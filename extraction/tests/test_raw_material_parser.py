import base64
import sys
from unittest.mock import patch

import pytest

import raw_material_parser
from raw_material_parser import MaterialParseError, parse_raw_material_record


def test_parse_text_material():
    parsed = parse_raw_material_record(
        {"source_type": "text", "content": "ACME acquired Beta.", "content_encoding": "plain"}
    )
    assert parsed.source_type == "text"
    assert "ACME acquired Beta." in parsed.text


def test_parse_csv_material_to_structured_text():
    parsed = parse_raw_material_record(
        {
            "source_type": "csv",
            "content": "company,employee_count\nACME,100\nBETA,80\n",
            "content_encoding": "plain",
        }
    )
    assert parsed.source_type == "csv"
    assert "columns: company, employee_count" in parsed.text
    assert "row 1:" in parsed.text


def test_parse_pdf_base64_without_pypdf_raises_error():
    fake_pdf = base64.b64encode(b"%PDF-1.4 fake").decode("utf-8")
    with patch.dict(sys.modules, {"pypdf": None}):
        with pytest.raises(MaterialParseError):
            parse_raw_material_record(
                {"source_type": "pdf", "content_encoding": "base64", "content": fake_pdf}
            )


def test_parse_pdf_plain_fallback_as_text():
    parsed = parse_raw_material_record(
        {"source_type": "pdf", "content_encoding": "plain", "content": "already extracted pdf text"}
    )
    assert parsed.source_type == "pdf"
    assert "already extracted pdf text" in parsed.text
    assert parsed.warnings


def test_parse_pdf_uses_ocr_fallback_when_pypdf_text_empty():
    fake_pdf = base64.b64encode(b"dummy-pdf").decode("utf-8")

    class _Page:
        @staticmethod
        def extract_text():
            return ""

    class _Reader:
        def __init__(self, *_args, **_kwargs):
            self.pages = [_Page()]

    with patch.object(raw_material_parser, "_ocr_pdf_payload", return_value=("ocr text", "ocr used", {"ocr_engine": "x"})):
        with patch.dict(sys.modules, {"pypdf": type("pypdf", (), {"PdfReader": _Reader})}):
            parsed = parse_raw_material_record(
                {"source_type": "pdf", "content_encoding": "base64", "content": fake_pdf}
            )
    assert parsed.text == "ocr text"
    assert parsed.metadata["parser"] == "ocr_fallback"
    assert parsed.warnings[0] == "ocr used"
