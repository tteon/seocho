import importlib.util
from pathlib import Path

from seocho.eval.case_envelope import CASE_ENVELOPE_SCHEMA_VERSION, validate_case_envelope


def _module():
    path = Path(__file__).parents[2] / "scripts/benchmarks/bootstrap_graphrag_case_envelopes.py"
    spec = importlib.util.spec_from_file_location("bootstrap_graphrag_case_envelopes", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_keeps_every_missing_gold_layer_explicit() -> None:
    row = {
        "case_id": "graphrag-bench:FB:000001",
        "upstream": {
            "repository": "https://example.invalid/upstream",
            "source_file": "FB.jsonl",
            "source_sha256": "c" * 64,
        },
    }
    envelope = _module().envelope_from_ledger_row(row)
    assert envelope["schema_version"] == CASE_ENVELOPE_SCHEMA_VERSION
    assert validate_case_envelope(envelope) == []
    assert envelope["layers"]["triples"]["status"] == "unannotated"
    assert envelope["layers"]["query"]["status"] == "unannotated"
    assert envelope["layers"]["answer"]["status"] == "unavailable"
