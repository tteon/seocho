"""text2cypher repair loop: a guardrail rejection is fed back for a retry, so a
fixable non-conformance does not force an abstain (ADR-0208 follow-up)."""

from __future__ import annotations

from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology.run_context import OntologyRunContext
from seocho.query.arm_config import ArmConfig
from seocho.query.structured_orchestrator import StructuredQueryOrchestrator

SAFE = "MATCH (c:Company {_workspace_id: $workspace_id, name: $n}) RETURN c.name AS name LIMIT $limit"
UNSAFE = "MATCH (f:Foo) RETURN f LIMIT $limit"


def _onto():
    return Ontology("t", package_id="t", version="1.0.0", nodes={
        "Company": NodeDef(description="c", properties={"name": P(str, unique=True)})})


class _FakeGraph:
    def __init__(self): self.calls = []
    def query(self, cypher, **k): self.calls.append(cypher); return [{"c": {"name": "Acme"}}]
    def get_schema(self, *, database="neo4j"): return {"labels": ["Company"], "relationship_types": []}


class _RepairableGen:
    """Emits UNSAFE first; once it sees repair feedback, emits SAFE."""
    def __init__(self): self.calls = 0; self.saw_feedback = False
    def __call__(self, question, schema_text, feedback=None):
        self.calls += 1
        if feedback:
            self.saw_feedback = True
            return SAFE, {"n": "Acme"}
        return UNSAFE, {}


def _orch(arm, gen, graph, budget):
    return StructuredQueryOrchestrator(
        arm=arm, graph_store=graph, ontology=_onto(), cypher_generator=gen,
        synthesizer=lambda q, rows: f"answer:{len(rows)}", repair_budget=budget)


def _ctx():
    return OntologyRunContext(workspace_id="acme", ontology_id="t")


def test_repair_recovers_a_rejected_query():
    g, gen = _FakeGraph(), _RepairableGen()
    orch = _orch(ArmConfig.governed(), gen, g, budget=1)
    r = orch.answer("who is Acme?", _ctx(), workspace_id="acme")
    assert gen.saw_feedback, "the guardrail violations were fed back for a retry"
    assert r.repair_attempts == 1
    assert not r.guardrail_rejected, "the repaired query passed the guardrail"
    # the intern organ's read-side canonical resolver may issue lookup queries
    # before execute; the execute call itself must be exactly the repaired one.
    executes = [c for c in g.calls if "toLower(" not in c]
    assert executes == [SAFE] and r.answer == "answer:1"


def test_no_budget_means_no_repair():
    g, gen = _FakeGraph(), _RepairableGen()
    orch = _orch(ArmConfig.governed(), gen, g, budget=0)
    r = orch.answer("who is Acme?", _ctx(), workspace_id="acme")
    assert r.repair_attempts == 0 and r.guardrail_rejected
    assert gen.calls == 1 and len(g.calls) == 0


def test_repair_gives_up_after_budget_if_still_bad():
    g = _FakeGraph()
    always_bad = lambda q, s, feedback=None: (UNSAFE, {})  # noqa: E731
    orch = _orch(ArmConfig.governed(), always_bad, g, budget=2)
    r = orch.answer("q", _ctx(), workspace_id="acme")
    assert r.repair_attempts == 2 and r.guardrail_rejected and len(g.calls) == 0
