"""Structured query orchestrator — organ flags change execution deterministically.

Proves each governed-memory organ is an independent runtime flag (the arm×organ
matrix made executable), using fakes for the LLM (cypher generator) and the graph
store so the organ SEMANTICS are testable without live infra.
"""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology.run_context import OntologyRunContext
from seocho.ontology.snapshot_store import OntologySnapshotStore
from seocho.query.arm_config import ArmConfig, ablation_arms
from seocho.query.pinned_schema import PinnedSchemaResolver
from seocho.query.structured_orchestrator import StructuredQueryOrchestrator

PKG = "acme"


def _onto():
    return Ontology("acme", package_id=PKG, version="1.0.0", nodes={
        "Company": NodeDef(description="c", properties={"name": P(str, unique=True)}),
    })


class _FakeGraph:
    def __init__(self):
        self.calls = []
        self.rows = [{"c": {"name": "Acme"}}]

    def query(self, cypher, *, params=None, database="neo4j", workspace_id=None,
              enforce_workspace_filter=False):
        self.calls.append({"cypher": cypher, "params": params, "workspace_id": workspace_id,
                           "enforce_workspace_filter": enforce_workspace_filter})
        return list(self.rows)

    def get_schema(self, *, database="neo4j"):
        return {"labels": ["Company"], "relationship_types": ["GOVERNS"]}


SAFE = "MATCH (c:Company {_workspace_id:$workspace_id}) RETURN c.name AS name LIMIT $limit"
UNSAFE = "MATCH (f:Foo) RETURN f LIMIT $limit"   # unknown label, unscoped


def _orch(arm, graph, *, gen_cypher=SAFE, tmp_path=None):
    onto = _onto()
    resolver = None
    if tmp_path is not None:
        store = OntologySnapshotStore(tmp_path / "snaps"); store.save(onto)
        resolver = PinnedSchemaResolver(store)
    return StructuredQueryOrchestrator(
        arm=arm, graph_store=graph, ontology=onto,
        cypher_generator=lambda q, schema: gen_cypher,
        synthesizer=lambda q, rows: f"answer:{len(rows)}rows",
        resolver=resolver,
    )


def _ctx(ws="acme", version="1.0.0"):
    c = OntologyRunContext(workspace_id=ws, ontology_id=PKG)
    return c.with_pinned_version(version=version, epoch=0, fingerprint="fp") if version else c


def test_arm_presets_and_leave_one_out():
    assert ArmConfig.governed().organs_on() == ["intern", "schema", "pin", "workspace", "guardrail"]
    assert ArmConfig.bare().organs_on() == []
    assert ArmConfig.governed().without("guardrail").guardrail is False
    assert ArmConfig.governed().without("workspace").workspace_enforce is False
    assert ArmConfig.governed().without("schema").schema_source == "introspected"
    names = [a.name for a in ablation_arms()]
    assert names == ["bare", "governed", "governed-no-intern", "governed-no-schema",
                     "governed-no-pin", "governed-no-workspace", "governed-no-guardrail"]


def test_schema_organ_pinned_vs_introspected(tmp_path):
    g = _FakeGraph()
    gov = _orch(ArmConfig.governed(), g, tmp_path=tmp_path)
    r = gov.answer("q", _ctx(), workspace_id="acme")
    assert r.schema_source == "pinned" and r.pinned_version == "1.0.0"

    bare = _orch(ArmConfig.bare(), g, tmp_path=tmp_path)
    r2 = bare.answer("q", _ctx(version=None), workspace_id="acme")
    assert r2.schema_source == "introspected"


def test_workspace_organ_governed_forces_filter(tmp_path):
    g = _FakeGraph()
    gov = _orch(ArmConfig.governed(), g, tmp_path=tmp_path)
    gov.answer("q", _ctx(), workspace_id="acme")
    call = g.calls[-1]
    assert call["enforce_workspace_filter"] is True and call["workspace_id"] == "acme"

    g2 = _FakeGraph()
    bare = _orch(ArmConfig.bare(), g2, tmp_path=tmp_path)
    bare.answer("q", _ctx(version=None), workspace_id="acme")
    assert g2.calls[-1]["enforce_workspace_filter"] is False


def test_guardrail_organ_rejects_unsafe_cypher(tmp_path):
    g = _FakeGraph()
    gov = _orch(ArmConfig.governed(), g, gen_cypher=UNSAFE, tmp_path=tmp_path)
    r = gov.answer("q", _ctx(), workspace_id="acme")
    assert r.guardrail_on and r.guardrail_rejected and r.guardrail_violations
    assert r.rows == [] and len(g.calls) == 0, "rejected Cypher must NOT execute"

    g2 = _FakeGraph()
    off = _orch(ArmConfig.governed().without("guardrail"), g2, gen_cypher=UNSAFE, tmp_path=tmp_path)
    r2 = off.answer("q", _ctx(), workspace_id="acme")
    assert r2.guardrail_on is False and r2.guardrail_rejected is False
    assert len(g2.calls) == 1, "guardrail OFF executes even unsafe Cypher (a real BARE risk)"


def test_synthesizer_owns_prose(tmp_path):
    g = _FakeGraph()
    gov = _orch(ArmConfig.governed(), g, tmp_path=tmp_path)
    r = gov.answer("q", _ctx(), workspace_id="acme")
    assert r.answer == "answer:1rows", "the synthesizer alone writes the answer (B5)"


def test_cross_tenant_each_query_carries_its_own_workspace(tmp_path):
    g = _FakeGraph()
    gov = _orch(ArmConfig.governed(), g, tmp_path=tmp_path)
    gov.answer("q", _ctx(ws="acme"), workspace_id="acme")
    gov.answer("q", _ctx(ws="globex"), workspace_id="globex")
    assert [c["workspace_id"] for c in g.calls] == ["acme", "globex"]
    assert all(c["enforce_workspace_filter"] for c in g.calls)
