# Prove or reject RDF-governance semantic lift

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. It follows `.PLANS.md` from the repository root.

## Purpose / Big Picture

SEOCHO must not adopt RDF governance, Oxigraph, or Rust merely because they are technically available. This plan makes their value falsifiable. A user will be able to run matched index-to-answer arms and inspect one JSON report per arm plus a comparison that says `supports_hypothesis`, `does_not_support_hypothesis`, `inconclusive`, or `insufficient_sample`.

Semantic lift means that facts extracted from raw source material give an agent better grounded answers: more required slots are supported, more selected evidence is useful, and gold answer quality does not regress. A typed evidence bundle is SEOCHO's structured answer support containing selected triples, required slots, provenance, and named missing slots. An ontology signal is the corresponding observation that an active profile helped, failed, or was mismatched.

## Progress

- [x] 2026-08-21: Created Beads item `seocho-hr3` and completed independent semantic, software, and systems reviews.
- [x] 2026-08-21: Added deterministic `seocho.agent_semantic_scorecard.v1` to E2E reports and conservative matched-arm comparison gates.
- [ ] Add a versioned gold semantic-case corpus with source spans, gold entities/triples, required slots, and seeded invalid candidates.
- [ ] Define a portable ontology-bundle and profile-pool contract, then measure agent readability, reuse, and lifecycle cost separately from semantic quality.
- [ ] Bind immutable RDF bundle publication, active-pointer generation/fence, and projection admission together before claiming filesystem/version safety.
- [ ] Execute live MARA/DozerDB A-E arms with JSONL traces, resource metrics, and blinded judging.

## Surprises & Discoveries

- Observation: Oxigraph currently provides an in-memory RDF term read model; SHACL is offline pySHACL and optional OWL consistency is offline only. It is not an Oxigraph inference claim.
  Evidence: `dataplane/oxigraph_read_model/` and `src/seocho/ontology/rdf_governance.py`.
- Observation: `seochod` currently performs per-node/per-relationship Bolt writes, so Rust is not presumed faster.
  Evidence: `dataplane/seochod/src/main.rs`.

## Decision Log

- Decision: Separate semantic/governance utility from Rust/Oxigraph systems utility.
  Rationale: SHACL conformance alone cannot establish grounded-answer accuracy; a faster sidecar cannot establish semantic value.
  Date/Author: 2026-08-21 / Codex.
- Decision: Do not use an opaque blended score as an adoption gate.
  Rationale: a high refusal rate or lower latency can hide lost valid facts. Primary semantic, safety, and systems gates remain independently visible.
  Date/Author: 2026-08-21 / Codex.

## Outcomes & Retrospective

The initial outcome is a report-level baseline, not an adoption claim. It records what the existing E2E path actually observed and makes insufficient data explicit. This section will record live results, cost, and the resulting routing/threshold recommendation after the matched experiment.

## Context and Orientation

`src/seocho/e2e.py` produces the CLI's `report.json`; query records already carry coverage, support status, selected-triple count, missing slots, and latency. `src/seocho/eval/semantic_scorecard.py` aggregates those records. `src/seocho/ontology/rdf_governance.py` produces a hash-pinned receipt. `dataplane/seochod` is the Rust Unix-domain-socket daemon that projects approved LPG payloads to DozerDB. `ActiveOntologyPointer` is the existing SQLite compare-and-swap primitive; it is not yet the RDF file publication lifecycle.

An ontology bundle is a portable, content-addressed directory containing one
canonical JSON-LD ontology, deterministic Turtle/SHACL derivatives, a manifest,
and purpose-specific agent profiles. A profile pool is an OS-managed cache of
immutable, already-verified profiles that agents lease by `(bundle digest,
purpose, workspace, generation)` instead of parsing or prompting with the full
ontology on every task. A lease is a read-only version pin, not a mutable shared
object: it expires when the agent finishes, while the pinned files remain valid
for replay and rollback.

## SEOCHO Evidence Contract

Each evaluated query must preserve the ontology/profile bundle digest, source and graph provenance, selected relation path/triples, required slots, missing slots, support assessment, and answer. A gold case adds accepted entity/triple labels and source spans so relation precision/recall and slot accuracy can be computed. Invalid candidates must be labelled so rejection precision, rejection recall, and false-promotion rate are meaningful.

## SEOCHO Review Panel

The professor lens requires no claim that conformance equals truth and requires gold semantic cases. The software-engineer lens requires typed reports, receipt/hash mismatch tests, and exact-payload projection parity. The computer-systems lens requires atomic immutable publication, fencing, socket ownership, resource bounds, and p95/RSS measurements. Promotion requires all three lenses; a positive result in one lens does not override a failure in another.

The agent lens adds one question: can an agent select the smallest complete
semantic view for its task without silently losing a required relation, slot,
or provenance rule? A smaller file is not an optimization if it makes a query
agent hallucinate an unavailable relation or makes an extractor omit a required
constraint.

## Plan of Work

Run five matched arms over the same raw documents, prompt/model/temperature, ontology revision, clean workspace/database, questions, and gold labels:

1. A: direct typed projection without RDF receipt.
2. B: JSON-LD schema guardrail/strict validation without RDF promotion.
3. C: RDF bundle plus pySHACL-promotable receipt, projected through the Python path.
4. D: C plus receipt-enforced `seochod` Rust/DozerDB projection.
5. E: D plus the Oxigraph read-model profile lookup.

Run cold and warm conditions separately, at concurrency 1, 4, and 16, with three repetitions. C isolates governance value; D isolates Rust OS-boundary value; E isolates Oxigraph read value. Do not attribute an unchanged evidence bundle to Oxigraph semantic lift.

Run a second, orthogonal portable-pooling matrix over the same bundle and agent
tasks:

1. F: every worker parses the canonical JSON-LD file and receives the full ontology context.
2. G: every worker reads its derived purpose profile from disk without a pool.
3. H: workers lease the same verified purpose profile from a local profile pool.
4. I: publish a new bundle while readers hold leases, then roll the current pointer back.

F/G/H measure agent and OS efficiency; I measures snapshot correctness. These
arms must preserve the exact requested bundle/profile digest and agent output
contract. They are not substitutes for A-E semantic comparison.

### Portable bundle and profile-pool hypotheses

H6 (portable replay): A bundle copied to a clean host remains verifiable and
produces the same profile digests and semantic context. It passes only when the
manifest, JSON-LD, Turtle, SHACL, profiles, and receipt hashes verify without a
host-specific path, database state, or mutable registry.

H7 (agent-readable minimal view): An indexing/query/projection profile reduces
prompt bytes and selection latency relative to full JSON-LD without reducing
gold triple F1, required-slot recall, provenance coverage, or governance
rejection accuracy. The primary agent-readable format is compact deterministic
JSON, not raw Turtle: JSON is concise for structured tools and stable key-based
diffing; JSON-LD remains the canonical human/RDF source and Turtle remains the
RDF interoperability form.

H8 (profile pooling): A local pool reduces aggregate cold-start parsing and
resident memory for repeated agents without cross-workspace/profile leakage.
It passes only if p95 lease acquisition or prompt assembly improves, aggregate
RSS and file reads decline, and every response reports the exact pinned digest.

H9 (CLI lifecycle safety): Publish, lease, rollback, garbage collection, and
daemon reload never expose mixed artifacts. It passes only if a 10,000-operation
concurrent workload observes zero torn manifest/profile/receipt tuple, zero
stale-fence projection admission, and deterministic recovery after a killed
publisher or daemon.

## Concrete Steps

1. Create a local-only gold corpus manifest and record its SHA-256, model, prompt/profile digest, git revision, DozerDB/Oxigraph/seochod versions, resource limits, warmup, and concurrency in every JSONL trace.
2. Extend E2E tracing so each index candidate records parse validity, source/provenance coverage, relation endpoints, receipt tuple, projection outcome, and latency stages. Each query must retain the evidence bundle and answer judge result.
3. Implement immutable bundle directories `.seocho/ontology/bundles/<bundle-digest>/`: write to a same-filesystem temporary directory, fsync, verify manifest hashes, atomically rename, then CAS the per-workspace `current` pointer. The pointer must include bundle/profile/context digests plus generation, epoch, and fencing token.
4. Require projection requests to carry the pinned pointer tuple and idempotency key. The daemon rejects stale/mismatched tuples, bounds requests, uses a private socket directory, and records applied/no-op/error results.
5. Use MARA as a blinded answer/evidence judge only after deterministic gold metrics are emitted; preserve prompts, judge model, and judge result in the local trace.
6. Compare report artifacts with `compare_semantic_utility`; record per-case deltas rather than only averages.
7. Define the portable on-disk layout before implementing pooling:

       .seocho/ontology/
         bundles/<bundle-sha256>/
           manifest.json
           ontology.jsonld
           ontology.ttl
           shapes.ttl
           agent-profiles/indexing.json
           agent-profiles/query.json
           agent-profiles/projection.json
           governance-receipt.json
         current/<workspace>/<package>.json
         leases/<workspace>/<lease-id>.json

   The manifest is the only file an agent/CLI resolves first. It names all
   immutable artifacts and their SHA-256 digests. `current` and `leases` are
   the only mutable records; update them with atomic replace/CAS and fsync.
   Never let agents scan arbitrary ontology directories or infer the active
   version from filenames.
8. Add a CLI contract with explicit lifecycle operations: `ontology bundle
   build`, `ontology bundle verify`, `ontology activate`, `ontology lease`,
   `ontology rollback`, `ontology gc --dry-run`, and `ontology status`. The
   first implementation must expose read-only status/verify before destructive
   garbage collection. A lease response supplies only the purpose profile,
   manifest receipt, generation/fence, and filesystem-safe path or UDS handle.

## Validation and Acceptance

Run focused checks:

    uv run pytest -q tests/seocho/test_semantic_scorecard.py tests/seocho/test_e2e_runner.py
    uv run pytest -q tests/seocho/test_projection_receipt.py tests/seocho/test_seochod_projection.py
    cargo test --offline --manifest-path dataplane/seochod/Cargo.toml
    bash scripts/ci/run_basic_ci.sh

For every live arm, capture E2E report JSON/Markdown, JSONL observability trace, process RSS/CPU/FDs, socket bytes/queue depth, Bolt round trips, stage p50/p95/p99, and DozerDB version. A passing mock is not live-performance evidence.

Pre-register initial gates: grounded answer slot accuracy and triple F1 non-inferior (paired lower confidence bound at least -0.02); evidence coverage improves by at least 0.05 and missing slots fall by at least 0.05; unsupported claims do not increase; invalid-rejection precision and recall at least 0.95; no unreceipted or stale-fence canonical write; zero torn profile/receipt reads across 10,000 swaps; Rust/Oxigraph p95 overhead no worse than 15% unless a documented quality or isolation benefit offsets it. Change these only before looking at the relevant arm results.

For F-I also record canonical/profile bytes, tokenized prompt bytes, profile
selection correctness, profile-load/lease p50-p95-p99, cold and warm process
RSS, CPU, open FDs, filesystem opens/bytes, cache hit rate, pool eviction,
lease contention, CAS retries, and cross-workspace digest mismatches. H7/H8
fail if any required ontology element is absent from an agent response or if a
lease returns another workspace's profile, even when latency improves.

## Idempotence and Recovery

Every arm uses a new workspace/database or a documented cleanup transaction, never an ambiguous reused graph. Immutable bundles are never overwritten; retrying publication verifies the same digest and then performs a no-op/CAS retry. Rollback updates only the current pointer to a previously verified digest. A failed daemon projection must record its idempotency key and outcome before retry; do not rely on client wall-clock timestamps for fencing.

## Artifacts and Notes

Keep raw documents, API keys, traces, and per-case outputs under ignored `.seocho/` or `outputs/`. Promote only aggregate reproducible measurements and a dated scrubbed report under `docs/experiments/`. `report.json` contains `agent_scorecard`; it is a baseline and comparison input, not a product-quality claim by itself.

The canonical file-format decision is therefore layered rather than a forced
single format: JSON-LD is authored and versioned; Turtle/SHACL serve RDF tools;
the manifest is the portable integrity entrypoint; compact JSON profiles are
the agent payload; the OS CLI owns atomic activation and leases. This preserves
RDF semantics without paying full-RDF prompt and parse cost for every agent.

## Cost, Latency, and Provider Policy

MARA is approved for the blinded judge and live E2E experiment. Record model name, temperature, request/response usage, retry count, and cost where the provider reports it. Do not spend on a full factorial until deterministic smoke verifies receipt admission, graph cleanup, and trace persistence. Stop an arm on repeated service errors and report the gap rather than substituting mock numbers.

## Interfaces and Dependencies

Public E2E reports gain an additive `agent_scorecard` field with schema `seocho.agent_semantic_scorecard.v1`. `compare_semantic_utility(baseline, governed)` accepts two report scorecards and returns `seocho.rdf_governance_lift_comparison.v1`. The scorecard is pure Python and has no provider, Oxigraph, DozerDB, or filesystem dependency. Live experiment wiring depends on MARA, DozerDB Bolt, the optional Rust daemon, and existing vendor-neutral tracing.
