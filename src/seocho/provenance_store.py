"""PostgreSQL ground-truth store — facts + provenance + classification (organ 3).

hadry's architecture: **PostgreSQL is the system of record; the graph (DozerDB) is a
PROJECTION of it.** This resolves the provenance review's #1 blocker (plane mismatch):
the graph is not a decorative mirror that bypasses access control — it is a GOVERNED
PROJECTION derived from this ground truth, carrying the classification forward so the
graph read path (reusing OntologyDisclosurePolicy.filter_record + AgentPrincipal) enforces
the same sensitivity. This module is the ground-truth schema + writer + the projection.

The review's other blockers are honored here, by construction:
- **#2 RLS must actually fire:** the DDL enables AND *forces* RLS (so it applies even to a
  table owner is avoided — the app connects as a NON-owner ``seocho_reader`` role), and the
  policy keys on ``current_setting('app.workspace')`` + ``app.principal`` set by the
  AUTHENTICATED connection via ``SET LOCAL`` — never from agent input.
- **#3 provenance/classification not agent-asserted:** ``record_run`` is called by the
  TRUSTED indexing path; ``classification`` is assigned by a trusted per-source rule
  (``classify_by_source``), not by the extraction LLM. Classification is **append-only**
  (versioned by ``effective_at``) so a relabel is auditable; the current label is the latest.
- **default-DENY:** an unclassified fact defaults to ``restricted`` (invisible without a grant),
  and a sensitivity-escalation is a ROW-DROP (RLS filters the row out), not a masked-but-visible
  row (which would leak existence/cardinality).
- **#4 no duplication:** cell/sub-cell masking is NOT re-implemented here — it stays
  ``OntologyDisclosurePolicy.filter_record`` on the read path; this store owns rows + provenance.

Content-addressed ``fact_id`` (from :mod:`seocho.provenance`) ties the ground-truth row, the
graph node, and the PROV-O bundle together and makes re-indexing an idempotent upsert.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from .provenance import ProvenanceRun

# Sensitivity lattice (reuses the vocabulary of risk/preflight.OntologyDisclosurePolicy).
SENSITIVITY_ORDER = ("public", "internal", "restricted", "secret")
DEFAULT_SENSITIVITY = "restricted"        # default-DENY for unclassified facts (review)

# The app connects as this NON-owner role so RLS is actually enforced (review #2).
READER_ROLE = "seocho_reader"

GROUND_TRUTH_DDL = f"""
-- Ground-truth fact/provenance/classification. The graph is a projection of this.
CREATE TABLE IF NOT EXISTS prov_fact (
    fact_id       text PRIMARY KEY,
    workspace_id  text NOT NULL,
    subject       text NOT NULL,
    predicate     text NOT NULL,
    object        text NOT NULL,
    extracted_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS prov_provenance (
    fact_id          text NOT NULL REFERENCES prov_fact(fact_id) ON DELETE CASCADE,
    run_id           text NOT NULL,
    source_doc       text NOT NULL,
    source_platform  text NOT NULL DEFAULT '',
    ontology_version text NOT NULL DEFAULT '',
    agent            text NOT NULL DEFAULT '',
    confidence       numeric NOT NULL DEFAULT 1.0,
    generated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fact_id, run_id)
);
-- Append-only classification: a relabel is a new row; current = latest effective_at.
CREATE TABLE IF NOT EXISTS prov_classification (
    fact_id      text NOT NULL REFERENCES prov_fact(fact_id) ON DELETE CASCADE,
    sensitivity  text NOT NULL DEFAULT '{DEFAULT_SENSITIVITY}',
    set_by       text NOT NULL DEFAULT 'system',
    effective_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fact_id, effective_at)
);
CREATE INDEX IF NOT EXISTS prov_fact_ws ON prov_fact (workspace_id);
CREATE INDEX IF NOT EXISTS prov_class_fact ON prov_classification (fact_id, effective_at DESC);
"""

# RLS: enabled AND forced; the app connects as the non-owner READER_ROLE; the policy keys
# on the AUTHENTICATED connection's SET LOCAL app.workspace + the principal's max grant.
# A fact is visible iff same workspace AND its current sensitivity <= the principal's grant.
RLS_DDL = f"""
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{READER_ROLE}') THEN
        CREATE ROLE {READER_ROLE} NOLOGIN;
    END IF;
END $$;
ALTER TABLE prov_fact ENABLE ROW LEVEL SECURITY;
ALTER TABLE prov_fact FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prov_fact_rls ON prov_fact;
CREATE POLICY prov_fact_rls ON prov_fact FOR SELECT TO {READER_ROLE}
USING (
    workspace_id = current_setting('app.workspace', true)
    AND (
        SELECT array_position(
            ARRAY['public','internal','restricted','secret'],
            COALESCE((SELECT sensitivity FROM prov_classification c
                      WHERE c.fact_id = prov_fact.fact_id
                      ORDER BY effective_at DESC LIMIT 1), '{DEFAULT_SENSITIVITY}'))
    ) <= (
        SELECT array_position(
            ARRAY['public','internal','restricted','secret'],
            COALESCE(current_setting('app.grant', true), 'public'))
    )
);
GRANT SELECT ON prov_fact, prov_provenance, prov_classification TO {READER_ROLE};
"""

# The projection: ground-truth rows a principal may see -> graph. Carries `sensitivity`
# so the projected graph nodes are stamped and the graph read path enforces it too
# (the graph is a GOVERNED projection, not a bypass).
PROJECTION_QUERY = """
SELECT f.fact_id, f.subject, f.predicate, f.object,
       COALESCE((SELECT sensitivity FROM prov_classification c
                 WHERE c.fact_id = f.fact_id ORDER BY effective_at DESC LIMIT 1),
                '%s') AS sensitivity
FROM prov_fact f
WHERE f.workspace_id = %%s
""" % DEFAULT_SENSITIVITY


def classify_by_source(source_platform: str) -> str:
    """Trusted per-source classification (NOT agent-asserted, review #3). A safe,
    conservative default map; unknown sources default-DENY to 'restricted'."""
    return {
        "confluence": "internal",
        "github": "internal",
        "google_drive": "restricted",
        "jira": "restricted",
        "slack": "restricted",
    }.get((source_platform or "").strip().lower(), DEFAULT_SENSITIVITY)


class ProvenanceGroundTruthStore:
    """Writes the ground-truth facts + provenance + (append-only) classification.

    ``connection_factory`` returns a psycopg connection (as
    :class:`PostgreSQLMemoryRepository`); the writer path is the OWNER (trusted), the
    read/projection path connects as ``READER_ROLE`` with ``SET LOCAL app.workspace``/
    ``app.grant`` so RLS is enforced. psycopg is imported lazily so this module imports
    without it (DDL/constants are usable offline)."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    def init_schema(self, *, with_rls: bool = True) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(GROUND_TRUTH_DDL)
                if with_rls:
                    cur.execute(RLS_DDL)
            conn.commit()

    def record_run(self, run: ProvenanceRun, *, set_by: str = "seocho-indexing") -> List[str]:
        """Idempotently upsert the run's facts + provenance + trusted classification.
        Classification comes from the trusted per-source rule, never from the agent.
        Returns the fact_ids written."""
        fids: List[str] = []
        sensitivity = classify_by_source(run.source_platform)
        with self._connect() as conn:
            with conn.cursor() as cur:
                for f in run.facts:
                    fid = f.fact_id(run.workspace_id)
                    fids.append(fid)
                    cur.execute(
                        "INSERT INTO prov_fact (fact_id, workspace_id, subject, predicate, object) "
                        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (fact_id) DO NOTHING",
                        (fid, run.workspace_id, f.subject, f.predicate, f.object))
                    cur.execute(
                        "INSERT INTO prov_provenance (fact_id, run_id, source_doc, source_platform, "
                        "ontology_version, agent, confidence) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (fact_id, run_id) DO NOTHING",
                        (fid, run.run_id, run.source_doc, run.source_platform,
                         run.ontology_version, run.agent, f.confidence))
                    cur.execute(
                        "INSERT INTO prov_classification (fact_id, sensitivity, set_by) "
                        "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (fid, sensitivity, set_by))
            conn.commit()
        return fids

    def project_for_principal(self, workspace_id: str, *, principal_grant: str = "public"
                              ) -> List[Dict[str, Any]]:
        """The governed projection: facts this principal may see (RLS-enforced),
        stamped with sensitivity for the graph write. Connects as the non-owner reader
        role and sets app.workspace + app.grant via SET LOCAL (from the AUTHENTICATED
        session, not agent input)."""
        rows: List[Dict[str, Any]] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL ROLE {READER_ROLE}")
                cur.execute("SET LOCAL app.workspace = %s", (workspace_id,))
                cur.execute("SET LOCAL app.grant = %s", (principal_grant,))
                cur.execute(PROJECTION_QUERY, (workspace_id,))
                cols = [d[0] for d in cur.description]
                for r in cur.fetchall():
                    rows.append(dict(zip(cols, r)))
            conn.rollback()      # read-only; drop the SET LOCALs
        return rows
