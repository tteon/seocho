"""engine="structured" wiring into the local runtime (ADR-0205, Step 2c/1).

Routes Seocho.ask -> _LocalEngine._run_structured_pipeline -> the organ-flagged
orchestrator: governed reads (D1), honest abstain (D5), per-request GuardrailLedger.
Exercised with injected seams (no live LLM/DB); the engine is built via __new__ so
the heavy __init__ (indexing pipeline, strategies) is not needed for the wiring test.
"""

from __future__ import annotations

from seocho.local_engine import _LocalEngine
from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology.run_context import OntologyRunContext, pinned_run_context
from seocho.ontology.active_pointer import ActiveOntologyPointer
from seocho.ontology.version_pin import VersionPinRegistry
from seocho.query.arm_config import ArmConfig


def _onto():
    return Ontology("erb", package_id="erb", version="1.0.0", nodes={
        "Company": NodeDef(description="c", properties={"name": P(str, unique=True)}),
    })


class _FakeGraph:
    def __init__(self):
        self.calls = []
        self.rows = [{"c": {"name": "Acme"}}]

    def query(self, cypher, *, params=None, database="neo4j", workspace_id=None,
              enforce_workspace_filter=False):
        self.calls.append({"cypher": cypher, "workspace_id": workspace_id,
                           "enforce_workspace_filter": enforce_workspace_filter})
        return list(self.rows)

    def get_schema(self, *, database="neo4j"):
        return {"labels": ["Company"], "relationship_types": []}


SAFE = "MATCH (c:Company {_workspace_id:$workspace_id}) RETURN c.name AS name LIMIT $limit"
UNSAFE = "MATCH (f:Foo) RETURN f LIMIT $limit"


def _engine(graph, *, arm, gen_cypher):
    e = object.__new__(_LocalEngine)          # bypass the heavy __init__
    e.graph_store = graph
    e.workspace_id = "acme"
    e.llm = None
    e._query = None
    e._structured_arm = arm
    e._pinned_schema_resolver = None          # -> introspected schema via get_schema
    e._structured_cypher_generator = lambda q, schema: gen_cypher
    e._structured_synthesizer = lambda q, rows: f"answer:{len(rows)}rows"
    e._last_query_metadata = {}
    return e


def _ctx():
    return OntologyRunContext(workspace_id="acme", ontology_id="erb")


def test_structured_pipeline_governed_execute_and_metadata():
    g = _FakeGraph()
    e = _engine(g, arm=ArmConfig.governed(), gen_cypher=SAFE)
    ans = e._run_structured_pipeline("q", database="neo4j", active_ontology=_onto(),
                                     run_context=_ctx())
    assert ans == "answer:1rows"                       # synthesizer owns the prose (B5)
    assert g.calls[-1]["enforce_workspace_filter"] is True      # governed execute (B2)
    assert g.calls[-1]["workspace_id"] == "acme"
    md = e._last_query_metadata
    assert md["engine"] == "structured" and md["answer_source"] == "structured"
    assert md["arm"]["name"] == "governed"
    assert md["guardrail_ledger"]["allowed"] == 1


def test_structured_honest_abstain_on_guardrail_reject():
    g = _FakeGraph()
    e = _engine(g, arm=ArmConfig.governed(), gen_cypher=UNSAFE)
    e._run_structured_pipeline("q", database="neo4j", active_ontology=_onto(), run_context=_ctx())
    md = e._last_query_metadata
    # a guardrail rejection is reported AS a rejection, never as "no evidence" (D5)
    assert md["answer_source"] == "structured_guardrail_rejected"
    assert md["guardrail_ledger"]["rejected"] == 1
    assert len(g.calls) == 0                           # rejected Cypher never executed


def test_bare_arm_does_not_force_workspace_filter():
    g = _FakeGraph()
    e = _engine(g, arm=ArmConfig.bare(), gen_cypher=SAFE)
    e._run_structured_pipeline("q", database="neo4j", active_ontology=_onto(), run_context=_ctx())
    assert g.calls[-1]["enforce_workspace_filter"] is False


def test_refcount_leak_safety_pin_released_on_exception(tmp_path):
    """The pin wrapping the request releases even when the pipeline raises — no
    refcount leak (hadry's OS-resource-handle concern)."""
    pointer = ActiveOntologyPointer(tmp_path / "active.db")
    pins = VersionPinRegistry(pointer)
    pointer.publish("acme", "erb", version="1.0.0", fingerprint="fp", fencing_token=1)
    try:
        with pinned_run_context(_ctx(), pin_registry=pins, package_id="erb",
                                active_pointer=pointer):
            assert pins.min_pinned_epoch("acme", "erb") == 0    # held during the body
            raise RuntimeError("boom in the structured pipeline")
    except RuntimeError:
        pass
    assert pins.min_pinned_epoch("acme", "erb") is None, "pin must be released on error"
