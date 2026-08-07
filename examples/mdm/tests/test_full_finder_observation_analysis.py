from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "26_full_finder_observation_analysis.py"
    spec = importlib.util.spec_from_file_location("full_finder_observation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_profile_pair_uses_case_as_inference_unit():
    module = _module()
    baseline = []
    survivorship = []
    for case_id, delta in (("c1", 2), ("c2", 4)):
        for model in ("m1", "m2"):
            common = {"case_id": case_id, "model": model, "category": "Risk", "error": ""}
            baseline.append({**common, "nodes_created": 10, "rels_created": 20, "latency_s": 1})
            survivorship.append({**common, "nodes_created": 10 + delta, "rels_created": 20 + delta, "latency_s": 2})
    result = module.profile_pair_analysis(baseline, survivorship)
    assert result["cases"] == 2
    assert result["paired_records"] == 4
    assert result["metrics"]["nodes_created"]["paired_case_mean_delta"] == 3


def test_factorial_detects_complete_balanced_design():
    module = _module()
    records = []
    prompts = ("neutral_kg@v1", "special@v1")
    ontologies = ("generic_baseline", "finance")
    for case_id in ("c1", "c2"):
        for model in ("m1", "m2"):
            for prompt in prompts:
                for ontology in ontologies:
                    records.append(
                        {
                            "case_id": case_id,
                            "model": model,
                            "scenario_id": f"{prompt}__{ontology}",
                            "nodes_created": 10,
                            "rels_created": 20,
                            "latency_s": 1,
                            "error": "",
                        }
                    )
    result = module.factorial_analysis(records)
    assert result["records"] == result["expected_records"] == 16
    assert result["errors"] == 0
