"""Unit tests for the five organ fixes the arm×organ e2e forced out (ADR-0219).

Each test pins a bug that corrupted or confounded a measured arm:
1. introspected-schema char-corruption (engine pre-joined strings re-joined char-wise)
2. governed-no-workspace guardrail confound (tenant-scope rules must gate on the arm)
3. deterministic workspace scope-strip (the organ is a system property, not a prompt)
4. read-time entity resolution gated by the intern organ AND tenant-gated by the
   workspace organ (probe-1's two isolation principles)
5. scope-optional generator prompt (single-brace Cypher in the scoped rule)
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from seocho.query.arm_config import ArmConfig
from seocho.query.grounded_text2cypher import build_text2cypher_system_prompt
from seocho.query.structured_orchestrator import (
    StructuredQueryOrchestrator,
    StructuredQueryResult,
    _introspected_schema_text,
)


# --- 1. introspected schema shape tolerance --------------------------------
def test_introspected_schema_passes_prejoined_strings_through():
    # the engine's _get_schema_info returns PRE-JOINED strings; joining them again
    # iterates characters ("D, i, s, e, a, s, e") and hands the generator garbage.
    block = _introspected_schema_text(
        {"node_labels": "Disease, Symptom", "relationship_types": "TREATED_BY"})
    assert "Disease, Symptom" in block and "D, i, s" not in block
    assert "TREATED_BY" in block


def test_introspected_schema_still_joins_raw_lists():
    block = _introspected_schema_text(
        {"labels": ["Disease", "Symptom"], "relationship_types": ["TREATED_BY"]})
    assert "Disease, Symptom" in block and "TREATED_BY" in block


# --- shared fakes -----------------------------------------------------------
class _FakeStore:
    """Records queries; returns canned rows per (cypher substring) matcher."""

    def __init__(self, resolve_rows: Dict[str, str] | None = None):
        self.calls: List[Dict[str, Any]] = []
        self._resolve_rows = resolve_rows or {}

    def query(self, cypher, params=None, database=None, **kw):
        self.calls.append({"cypher": cypher, "params": dict(params or {}), "kw": kw})
        if "toLower(n.name) = toLower($t)" in cypher:
            hit = self._resolve_rows.get(str((params or {}).get("t", "")).lower())
            return [{"name": hit}] if hit else []
        if "CONTAINS toLower($t)" in cypher:
            return []
        return []

    def get_schema(self, database=None):
        return {"labels": ["Disease"], "relationship_types": ["TREATED_BY"]}


def _orch(arm: ArmConfig, store: _FakeStore | None = None, **kw) -> StructuredQueryOrchestrator:
    class _Onto:
        nodes = {"Disease": type("N", (), {"properties": {"name": str}})()}
        relationships = {"TREATED_BY": type("R", (), {"properties": {}})()}

    return StructuredQueryOrchestrator(
        arm=arm, graph_store=store or _FakeStore(), ontology=_Onto(),
        cypher_generator=kw.pop("gen", lambda q, s: ("MATCH (d:Disease) RETURN d LIMIT $limit", {})),
        synthesizer=lambda q, rows: "ans", database="db", **kw)


# --- 2. guardrail tenant-scope rules gate on the workspace organ ------------
def test_no_workspace_arm_drops_tenant_scope_violations():
    arm = ArmConfig.governed().without("workspace")
    o = _orch(arm, gen=lambda q, s, feedback=None: (
        "MATCH (d:Disease {name: $n}) RETURN d LIMIT $limit", {"n": "x"}))
    res = o.answer("q", run_context=None, workspace_id="ws")
    # unscoped Cypher + workspace organ OFF must NOT be rejected for missing scope
    assert not res.guardrail_rejected
    assert "missing_workspace_scope_expression" not in res.guardrail_violations


def test_governed_arm_still_requires_tenant_scope():
    o = _orch(ArmConfig.governed(), gen=lambda q, s, feedback=None: (
        "MATCH (d:Disease {name: $n}) RETURN d LIMIT $limit", {"n": "x"}))
    res = o.answer("q", run_context=None, workspace_id="ws")
    assert res.guardrail_rejected
    assert "missing_workspace_scope_expression" in res.guardrail_violations


# --- 3. deterministic scope strip on the un-governed execute path -----------
@pytest.mark.parametrize("cypher,expect_gone", [
    ("MATCH (d:Disease {_workspace_id: $workspace_id, name: $n}) RETURN d", True),
    ("MATCH (d:Disease {name: $n, _workspace_id: $workspace_id}) RETURN d", True),
    ("MATCH (d:Disease {_workspace_id: $workspace_id}) RETURN d", True),
])
def test_strip_workspace_scope_all_forms(cypher, expect_gone):
    out = StructuredQueryOrchestrator._strip_workspace_scope(cypher)
    assert ("_workspace_id" not in out) is expect_gone
    assert "Disease" in out


def test_bare_execute_strips_llm_readded_scope():
    # the LLM habitually re-adds the tenant filter despite an un-scoped prompt; the
    # OFF arm must strip it deterministically or it silently re-isolates itself.
    store = _FakeStore()
    arm = ArmConfig.governed().without("workspace")
    o = _orch(arm, store=store, gen=lambda q, s, feedback=None: (
        "MATCH (d:Disease {_workspace_id: $workspace_id, name: $n}) RETURN d LIMIT $limit",
        {"n": "x"}))
    o.answer("q", run_context=None, workspace_id="ws")
    executed = store.calls[-1]
    assert "_workspace_id" not in executed["cypher"]
    # params still carry workspace_id so a residual reference can never crash unbound
    assert executed["params"].get("workspace_id") == "ws"


# --- 4. read-time entity resolution: intern-gated + workspace-gated ---------
def test_resolution_off_when_intern_organ_off():
    store = _FakeStore(resolve_rows={"bcc": "basal cell skin cancer"})
    arm = ArmConfig.governed().without("intern")
    o = _orch(arm, store=store)
    resolved, notes = o._resolve_entity_params({"disease_name": "BCC"}, "ws")
    assert resolved["disease_name"] == "BCC" and notes == {}


def test_resolution_rewrites_param_to_canonical_name():
    store = _FakeStore(resolve_rows={"bcc": "basal cell skin cancer"})
    o = _orch(ArmConfig.governed(), store=store)
    resolved, notes = o._resolve_entity_params({"disease_name": "BCC"}, "ws")
    assert resolved["disease_name"] == "basal cell skin cancer"
    assert notes == {"disease_name=BCC": "basal cell skin cancer"}
    # the resolver query itself must be tenant-scoped when the workspace organ is ON
    assert all("_workspace_id = $ws" in c["cypher"] for c in store.calls)


def test_resolution_unscoped_when_workspace_organ_off():
    # probe-1 principle: tenant scope must gate EVERY tenant-scoped op — a scoped
    # resolve would silently re-isolate the no-workspace arm and mask the leak.
    store = _FakeStore(resolve_rows={"atlas": "Atlas"})
    arm = ArmConfig.governed().without("workspace")
    o = _orch(arm, store=store)
    o._resolve_entity_params({"name": "Atlas"}, "ws")
    assert store.calls, "resolver should still run"
    assert all("_workspace_id = $ws" not in c["cypher"] for c in store.calls)


def test_entity_resolutions_reported_in_result_dict():
    r = StructuredQueryResult(answer="a", cypher="c", rows=[], schema_source="pinned",
                              entity_resolutions={"k=v": "w"})
    assert r.to_dict()["entity_resolutions"] == {"k=v": "w"}


# --- 5. scope-optional generator prompt -------------------------------------
def test_generator_prompt_scoped_has_single_brace_cypher():
    p = build_text2cypher_system_prompt("SCHEMA", workspace_scoped=True)
    assert "{_workspace_id: $workspace_id}" in p          # single braces, usable Cypher
    assert "{{_workspace_id" not in p                      # the double-brace bug
    assert "EVERY node" in p


def test_generator_prompt_unscoped_forbids_tenant_filter():
    p = build_text2cypher_system_prompt("SCHEMA", workspace_scoped=False)
    assert "Do NOT add any `_workspace_id`" in p
    assert "{_workspace_id: $workspace_id}` on EVERY node" not in p
