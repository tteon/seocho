"""Fact-level provenance chain + ground-truth store (organ 3, narrow gap).

Postgres is the system of record; the graph is a governed projection of it. Live RLS
(non-owner role, SET LOCAL principal) is validated with a pg container; here we test the
deterministic surface: content-addressed ids, value-free PROV-O, the security properties
baked into the DDL, trusted (not agent-asserted) classification, and idempotent writes.
"""

from __future__ import annotations

from seocho.provenance import (
    Fact,
    ProvenanceRun,
    build_run_from_extraction,
    content_fact_id,
)
from seocho.provenance_store import (
    DEFAULT_SENSITIVITY,
    GROUND_TRUTH_DDL,
    PROJECTION_QUERY,
    READER_ROLE,
    RLS_DDL,
    ProvenanceGroundTruthStore,
    classify_by_source,
)


# --- provenance chain ------------------------------------------------------
def test_fact_id_is_content_addressed_and_idempotent():
    a = content_fact_id("ws", "Acme", "AFFECTS", "SUP-1")
    b = content_fact_id("ws", " acme ", "affects", "SUP-1")   # normalized
    assert a == b and a.startswith("fact_")
    assert content_fact_id("other", "Acme", "AFFECTS", "SUP-1") != a   # workspace-scoped


def test_provenance_ttl_is_valid_prov_o_and_value_free():
    import rdflib
    run = ProvenanceRun(run_id="r1", workspace_id="ws", source_doc="jira__inc1",
                        ontology_version="1.0.0", source_platform="jira",
                        facts=[Fact("SUP-29410", "AFFECTS", "SECRET_CUSTOMER_XYZ", 0.9)])
    ttl = run.to_ttl()
    g = rdflib.Graph().parse(data=ttl, format="turtle")   # valid turtle
    assert len(g) > 0
    assert "prov:wasGeneratedBy" in ttl or "wasGeneratedBy" in ttl
    assert "SoftwareAgent" in ttl
    # value-free: the sensitive OBJECT value is NOT embedded in the provenance
    assert "SECRET_CUSTOMER_XYZ" not in ttl
    # the fact is referenced by its content-addressed id
    assert content_fact_id("ws", "SUP-29410", "AFFECTS", "SECRET_CUSTOMER_XYZ") in ttl


def test_build_run_from_extraction():
    run = build_run_from_extraction(
        run_id="r", workspace_id="ws", source_doc="d", ontology_version="1.0.0",
        relationships=[{"source": "A", "type": "REL", "target": "B"},
                       {"source": "", "type": "REL", "target": "B"}])   # empty subj dropped
    assert len(run.facts) == 1 and run.facts[0].subject == "A"


# --- ground-truth store DDL / security properties --------------------------
def test_ddl_bakes_in_the_review_security_fixes():
    # RLS enabled AND forced, app connects as a NON-owner role (review #2)
    assert "ENABLE ROW LEVEL SECURITY" in RLS_DDL and "FORCE ROW LEVEL SECURITY" in RLS_DDL
    assert READER_ROLE in RLS_DDL and "NOLOGIN" in RLS_DDL
    # principal/workspace from the connection setting, never agent input
    assert "current_setting('app.workspace'" in RLS_DDL
    assert "current_setting('app.grant'" in RLS_DDL
    # default-DENY: unclassified defaults to restricted
    assert DEFAULT_SENSITIVITY == "restricted"
    assert "DEFAULT 'restricted'" in GROUND_TRUTH_DDL
    # classification append-only (versioned by effective_at)
    assert "effective_at" in GROUND_TRUTH_DDL and "prov_classification" in GROUND_TRUTH_DDL


def test_classification_is_trusted_per_source_not_agent_asserted():
    assert classify_by_source("jira") == "restricted"
    assert classify_by_source("confluence") == "internal"
    assert classify_by_source("unknown-source") == DEFAULT_SENSITIVITY   # default-DENY


def test_projection_carries_sensitivity_forward():
    # the projection selects sensitivity so the graph nodes are stamped (graph = governed
    # projection, not a bypass)
    assert "sensitivity" in PROJECTION_QUERY and "prov_fact" in PROJECTION_QUERY


# --- record_run idempotent write (fake psycopg connection) -----------------
class _FakeCur:
    def __init__(self, log): self.log = log; self.description = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self.log.append((" ".join(sql.split())[:60], params))
    def fetchall(self): return []


class _FakeConn:
    def __init__(self, log): self.log = log
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return _FakeCur(self.log)
    def commit(self): self.log.append(("COMMIT", None))
    def rollback(self): pass


def test_record_run_writes_content_addressed_and_trusted_classification():
    log: list = []
    store = ProvenanceGroundTruthStore(lambda: _FakeConn(log))
    run = ProvenanceRun(run_id="r1", workspace_id="ws", source_doc="jira__x",
                        ontology_version="1.0.0", source_platform="jira",
                        facts=[Fact("SUP-1", "AFFECTS", "Acme", 0.9)])
    fids = store.record_run(run)
    assert fids == [content_fact_id("ws", "SUP-1", "AFFECTS", "Acme")]
    inserts = [s for s, _ in log]
    assert any("INSERT INTO prov_fact" in s for s in inserts)
    assert any("INSERT INTO prov_provenance" in s for s in inserts)
    # classification written with the TRUSTED per-source value (jira -> restricted),
    # never a value the fact/agent asserted
    cls = [p for s, p in log if "INSERT INTO prov_classification" in s]
    assert cls and cls[0] == (fids[0], "restricted", "seocho-indexing")
