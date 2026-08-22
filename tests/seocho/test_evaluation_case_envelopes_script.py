import importlib.util
import json
from pathlib import Path

from seocho.eval.case_envelope import CASE_ENVELOPE_SCHEMA_VERSION


def _script_module():
    path = Path(__file__).parents[2] / "scripts/benchmarks/evaluation_case_envelopes.py"
    spec = importlib.util.spec_from_file_location("evaluation_case_envelopes", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_report_is_content_free_and_exposes_annotation_gaps(tmp_path) -> None:
    payload = {
        "schema_version": CASE_ENVELOPE_SCHEMA_VERSION,
        "case_id": "local:1",
        "source": {"snapshot_sha256": "b" * 64},
        "layers": {
            "ontology": {"status": "unannotated"},
            "triples": {"status": "unannotated"},
            "query": {"status": "unannotated"},
            "answer": {"status": "unannotated"},
            "governance": {"status": "unavailable"},
        },
    }
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    report = _script_module().build_report(_script_module().load_cases(path))
    assert report["valid"] is True
    assert report["annotation_coverage"]["layers"]["query"]["coverage_rate"] == 0.0
    assert "question" not in json.dumps(report)
