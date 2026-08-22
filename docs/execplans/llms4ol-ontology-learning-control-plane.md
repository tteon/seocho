# Offline LLMs4OL ontology-learning control plane

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept current. It follows `.PLANS.md` from the repository root.

## Purpose / Big Picture

SEOCHO currently uses an ontology to constrain extraction, validation, graph projection, and query answering. It must also help domain experts improve that ontology from evidence without permitting an LLM to silently alter a canonical schema. This plan implements the missing offline ontology-learning loop: term candidates, term typing, taxonomy candidates, non-taxonomic relation candidates, axiom candidates, review artifacts, scorecards, and governed promotion.

An operator will be able to run `seocho ontology learn` over a previously extracted graph, inspect a local JSON review artifact, approve a bounded mapping/change specification, and then use the existing RDF/SHACL lifecycle to promote a new version. No candidate is written to the active ontology or canonical graph merely because an LLM or frequency heuristic suggested it.

## Progress

- [x] 2026-08-22: Created Beads item `seocho-drr`, linked to the ontology proposal and semantic-scorecard work.
- [ ] Implement deterministic candidate ledger, structural discovery, and LLMs4OL-compatible scorecard.
- [ ] Add offline CLI and content-free trace/metric receipts.
- [ ] Integrate optional injected LLM term-typing/taxonomy/relation proposal calls with source-evidence requirements.
- [ ] Add review-to-versioned-bundle promotion command; verify SHACL, OntoClean, lease, and receipt boundaries.
- [ ] Execute GraphRAG-Bench-derived corpus experiment only after question-to-corpus evidence bindings and approved ontology changes exist.

## Surprises & Discoveries

- Existing `scripts/serve_track/annotate_graphrag_bench.py` is a different GraphRAG benchmark family, not the `jeremycp3/GraphRAG-Bench` textbook release.
- Current SEOCHO already has useful pieces: OOV quarantine and class proposals, hierarchy lint/OntoClean, and structural axiom mining. Their outputs are not yet one learning artifact or scorecard.

## Decision Log

- Decision: Implement ontology learning only in the offline control plane.
  Rationale: online agent requests must consume receipt-pinned, approved profiles rather than mutable model suggestions.
  Date/Author: 2026-08-22 / Codex.
- Decision: Treat term typing, taxonomy discovery, and relation discovery as separate labelled tasks with separate scores.
  Rationale: a relation candidate cannot compensate for a bad is-a edge, and an aggregate score would hide failure modes.
  Date/Author: 2026-08-22 / Codex.
- Decision: Reuse the existing ambiguity/mapping and axiom mechanisms instead of introducing a second ontology mutation path.
  Rationale: one review and promotion boundary is auditable and versionable.
  Date/Author: 2026-08-22 / Codex.

## Outcomes & Retrospective

No ontology-learning quality claim is made until gold labels and SME-reviewed promotions are evaluated against a fixed corpus. This section will record the measured proposal precision, review burden, downstream extraction conformance, Text2Cypher correctness, and governance outcomes.

## Context and Orientation

`src/seocho/ontology/ambiguity.py` collects out-of-vocabulary entities and can propose aliases/new classes. `src/seocho/axioms.py` mines structural logical candidates from an extracted graph. `src/seocho/ontology/scorecard.py` evaluates an ontology artifact, and `src/seocho/ontology/lifecycle.py` plus RDF/SHACL bundles control promotion. The new control plane composes these components but does not replace them.

A term is a corpus surface form with frequency and provenance. Term typing maps that term to an existing type, a proposed type, or ignore. A taxonomy candidate is a directed `child is-a parent` edge. A non-taxonomic relation candidate is a directed semantic relation with domain/range. An axiom candidate is a constraint or rule inferred from observed graph structure. A review artifact is an immutable local file containing candidates, evidence references, status, and exact inputs. A typed evidence bundle is the existing query-time contract; it is not replaced by this offline proposal artifact.

## SEOCHO Evidence Contract

Every candidate records its source graph/corpus identity, support count, bounded evidence references, producer (deterministic or LLM), and disposition. LLM proposals must additionally record model, prompt/profile digest, schema version, parse/repair outcome, and no raw prompt content unless explicit local capture is enabled. Review artifacts contain no active-ontology mutation. Approved mapping specs and new ontology bundles retain their separate hashes, SHACL outcome, OntoClean outcome, lifecycle generation, lease, and projection receipt.

## SEOCHO Review Panel

The professor lens checks semantic validity: surface frequency is not proof of a type, a taxonomic edge, or an axiom. The software-engineer lens requires typed, deterministic artifacts and exact promotion boundaries. The computer-systems lens requires local append-only files, bounded metric labels, no request-time LLM governance work, and measurable review/latency cost. A proposal only advances when all three lenses can inspect its evidence.

## Plan of Work

1. Build one offline candidate artifact from observed graph payloads and existing ambiguity/axiom outputs.
2. Score term typing, taxonomy, and non-taxonomic relation tasks separately when labelled gold is supplied; report unavailable labels rather than zero scores.
3. Add a CLI that reads an explicit graph artifact and writes an explicit local report; it never modifies a schema.
4. Add optional injected LLM proposal adapters that produce candidates under the same artifact schema.
5. Require human approval plus existing lint, OntoClean, SHACL/RDF bundle verification, active-pointer CAS, and lease before activation.

## Concrete Steps

1. Implement `src/seocho/ontology/learning.py` with candidate, report, and scorecard models.
2. Re-export the public offline API from `seocho.ontology` and add tests for support aggregation, type/taxonomy/relation task separation, and no-auto-promotion.
3. Add `seocho ontology learn --schema --graph --output`; default output is local and contains no database mutation action.
4. Emit bounded candidate-count and duration metrics; store graph identity and candidate detail only in report/trace data.
5. Add the promotion adapter only after its review format is stable.

## Validation and Acceptance

Run:

    uv run pytest tests/seocho/test_ontology_learning.py -q
    uv run seocho ontology learn --help
    bash scripts/ci/run_basic_ci.sh

Acceptance requires a graph with unknown terms/relations to produce review-only candidates, separate task metrics to return `unavailable` absent gold, and an unchanged input ontology after every learning command.

## Idempotence and Recovery

Learning reads a graph artifact and writes only the explicit output path. Re-running with the same input must yield stable candidate identifiers and ordering. If a proposal is malformed, fail before writing a partial report. Promotion remains a separate existing atomic bundle publication and CAS activation flow.

## Artifacts and Notes

Use `.seocho/ontology-learning/` for local candidate reports and traces. Do not commit source documents, raw prompts, generated dataset output, keys, or review state. Promote only aggregate evidence and public contracts.

## Interfaces and Dependencies

The first milestone depends only on the Python SDK and existing `Ontology`, ambiguity, axiom, and scorecard modules. Optional LLM proposals use an injected structured backend and MARA only when explicitly invoked. Owlready2 stays offline; PySHACL/RDF governance stays after human approval.

## Cost, Latency, and Provider Policy

The deterministic discovery path makes no model call. Each optional LLM proposal is a paid, explicit offline operation with model/version, retry, token, and latency receipts. Begin model experiments on a fixed small sample and use blinded gold evaluation before running the full academic-only GraphRAG-Bench corpus.
