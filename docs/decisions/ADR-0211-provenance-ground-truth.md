# ADR-0211: PostgreSQL ground truth + fact-level provenance chain (organ 3, narrow slice)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4.15 (provenance + fine-grained security)
- Related: ADR-0151 (Postgres memory store), ADR-0164/0206 (workspace isolation),
  risk/preflight OntologyDisclosurePolicy, agent/identity AgentPrincipal

## Context

The provenance/security design review returned "redesign" (5 blockers). hadry's architecture
correction resolves its #1 (plane mismatch): **PostgreSQL is the system of record; the graph
(DozerDB) is a PROJECTION of it.** So the graph is not a decorative mirror that bypasses access
control — it is a GOVERNED projection derived from the governed ground truth. The other review
blockers are real regardless and are honored here; the scope is the NARROW genuine gap (a
fact-level provenance chain + the ground-truth store + governed projection), NOT re-implementing
authz/masking that already ships.

## Decision

- **`seocho.provenance`** — the fact-level provenance CHAIN: `content_fact_id =
  hash(workspace_id, canonical(s,p,o))` (ties ground-truth row / graph node / bundle IRI;
  idempotent re-index), and a per-agent-run PROV-O `Bundle` (`ProvenanceRun.to_ttl`) that is
  correct (run=Activity, agent=SoftwareAgent, ontology=Plan; fact wasGeneratedBy/wasDerivedFrom/
  wasAttributedTo) and **value-free** — references facts by id, never embeds the object content
  (so provenance is not itself a leak channel).
- **`seocho.provenance_store`** — the PostgreSQL ground truth (`prov_fact` / `prov_provenance` /
  `prov_classification`) + the governed projection. Honoring the review:
  - **RLS actually fires (#2):** DDL `ENABLE` + **`FORCE` ROW LEVEL SECURITY`; the app reads as a
    NON-owner `seocho_reader` role; the policy keys on `current_setting('app.workspace')` +
    `app.grant` set by the AUTHENTICATED connection via `SET LOCAL` — never agent input.
  - **not agent-asserted (#3):** the trusted indexing path calls `record_run`; classification is
    a trusted per-source rule (`classify_by_source`), not the extraction LLM; classification is
    **append-only** (versioned by `effective_at`, current = latest) so a relabel is auditable.
  - **default-DENY:** unclassified → `restricted` (invisible without a grant); sensitivity
    escalation is a ROW-DROP (RLS filters the row), not a masked-but-visible row.
  - **no duplication (#4):** cell/sub-cell masking stays `filter_record`; principal authz stays
    `AgentPrincipal`; this store owns rows + provenance + classification only.
  - **governed projection:** `project_for_principal` connects as the reader role, RLS-filters,
    and stamps `sensitivity` onto the projected rows so the graph nodes carry it and the graph
    read path enforces the same sensitivity — the graph is a governed projection, not a bypass.

## Consequences

- The code + DDL + security design are built and unit-tested (7 tests: content-addressed
  idempotent fact_id; value-free valid PROV-O turtle; DDL bakes FORCE-RLS + non-owner role +
  current_setting principal + default-restricted + append-only classification; trusted per-source
  classification; idempotent content-addressed write via a fake connection). `ruff` clean.
- **Deliberately deferred to a live run** (needs the `docker-compose.memory` postgres:18 container
  + `psycopg` installed, neither up in this env): RLS actually enforcing across principals end to
  end, and the Postgres→graph projection wired into indexing. And a *measured* access-control
  result needs gold per-fact sensitivity / PII-span labels the erb corpus lacks — reported honestly
  as the boundary, not asserted. This ADR lands the ground-truth substrate + the review's security
  design; the live validation mirrors how the graph e2e was gated on DozerDB.
