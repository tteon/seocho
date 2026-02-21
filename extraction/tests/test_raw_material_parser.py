import base64
import sys
import types
from unittest.mock import patch

import pytest

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
