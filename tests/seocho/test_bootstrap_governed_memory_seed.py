import importlib.util
from pathlib import Path

from seocho.eval.case_envelope import annotation_coverage, validate_case_envelope


def _module():
    path = Path(__file__).parents[2] / "scripts/benchmarks/bootstrap_governed_memory_seed.py"
    spec = importlib.util.spec_from_file_location("bootstrap_governed_memory_seed", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_controlled_seed_has_reviewed_labels_but_is_explicitly_fixture_scoped() -> None:
    cases = _module().build_cases()
    assert len(cases) == 24
    assert all(validate_case_envelope(case) == [] for case in cases)
    coverage = annotation_coverage(cases)
    assert all(values["coverage_rate"] == 1.0 for values in coverage["layers"].values())
    assert all(case["source"]["document_refs"] == ["fixture:governed-memory-calibration.v1"] for case in cases)
