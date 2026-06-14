"""Tests for domain-adaptive guardrail selection wired into the e2e run-spec (ADR-0123)."""

from __future__ import annotations

import json

import pytest

from seocho.e2e import resolve_guardrail
from seocho.ontology import NodeDef, Ontology, P
from seocho.run_spec import RunSpecError, parse_run_spec


def test_parse_select_block_populates_candidates():
    spec = parse_run_spec({
        "ontology": {"select": {
            "candidates": {"lean": "fibo_minus.jsonld", "rich": "fibo_plus.jsonld"},
            "corpus_profile": "profile.json",
        }},
        "documents": "./docs",
        "questions": ["q1?"],
    })
    assert spec.ontology_path == ""
    assert spec.guardrail_candidates == {"lean": "fibo_minus.jsonld", "rich": "fibo_plus.jsonld"}
    assert spec.guardrail_corpus_profile == "profile.json"


def test_parse_select_missing_corpus_profile_errors():
    with pytest.raises(RunSpecError):
        parse_run_spec({
            "ontology": {"select": {"candidates": {"lean": "a.jsonld"}}},
            "documents": "./docs", "questions": ["q?"],
        })


def test_parse_requires_path_or_select():
    with pytest.raises(RunSpecError):
        parse_run_spec({"ontology": {}, "documents": "./docs", "questions": ["q?"]})


def test_fixed_path_still_works():
    spec = parse_run_spec({"ontology": "./schema.yaml", "documents": "./docs", "questions": ["q?"]})
    assert spec.ontology_path == "./schema.yaml"
    assert not spec.guardrail_candidates


def _write_onto(path, name, labels):
    o = Ontology(name, nodes={l: NodeDef(description=f"{l}.", properties={"name": P(str, unique=True)}) for l in labels})
    o.to_jsonld(path)


def test_resolve_guardrail_picks_for_entity_corpus(tmp_path):
    lean = tmp_path / "lean.jsonld"
    rich = tmp_path / "rich.jsonld"
    _write_onto(lean, "lean", ["Company", "FinancialMetric"])
    _write_onto(rich, "rich", ["Company", "FinancialMetric", "Person", "Regulation", "Risk"])
    profile = tmp_path / "profile.json"
    # entity-heavy corpus (people, regulations, risks)
    profile.write_text(json.dumps({"label_frequencies": {"Person": 8, "Regulation": 6, "Risk": 5, "Company": 2}}))
    spec_yaml = tmp_path / "run.yaml"
    spec_yaml.write_text("x")  # just to anchor source_path

    spec = parse_run_spec({
        "ontology": {"select": {"candidates": {"lean": "lean.jsonld", "rich": "rich.jsonld"},
                                "corpus_profile": "profile.json"}},
        "documents": "./docs", "questions": ["q?"],
    }, source_path=str(spec_yaml))

    resolve_guardrail(spec)
    assert spec.ontology_path == "rich.jsonld"        # entity corpus → richer guardrail
    assert spec.selected_guardrail["chosen"] == "rich"
    assert spec.selected_guardrail["domain_kind"] == "entity"


def test_resolve_guardrail_noop_when_path_fixed(tmp_path):
    spec = parse_run_spec({"ontology": "./schema.yaml", "documents": "./docs", "questions": ["q?"]})
    resolve_guardrail(spec)
    assert spec.ontology_path == "./schema.yaml"
    assert spec.selected_guardrail is None
