# Prove or reject RDF-governance semantic lift

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. It follows `.PLANS.md` from the repository root.

## Purpose / Big Picture

SEOCHO must not adopt RDF governance, Oxigraph, or Rust merely because they are technically available. This plan makes their value falsifiable. A user will be able to run matched index-to-answer arms and inspect one JSON report per arm plus a comparison that says `supports_hypothesis`, `does_not_support_hypothesis`, `inconclusive`, or `insufficient_sample`.

Semantic lift means that facts extracted from raw source material give an agent better grounded answers: more required slots are supported, more selected evidence is useful, and gold answer quality does not regress. A typed evidence bundle is SEOCHO's structured answer support containing selected triples, required slots, provenance, and named missing slots. An ontology signal is the corresponding observation that an active profile helped, failed, or was mismatched.

## Progress

- [x] 2026-08-21: Created Beads item `seocho-hr3` and completed independent semantic, software, and systems reviews.
- [x] 2026-08-21: Added deterministic `seocho.agent_semantic_scorecard.v1` to E2E reports and conservative matched-arm comparison gates.
- [ ] Add a versioned gold semantic-case corpus with source spans, gold entities/triples, required slots, and seeded invalid candidates.
- [ ] Define a serialization-neutral portable ontology-bundle, profile-pool, and lock contract; measure agent readability, reuse, lifecycle cost, and RDF-tool compatibility before selecting a canonical authoring format.
- [ ] Bind immutable RDF bundle publication, active-pointer generation/fence, and projection admission together before claiming filesystem/version safety. (CLI publication, hash verification, CAS activation, and persistent single-host writer leases are now implemented; daemon admission is still not bound to the lease tuple.)
- [ ] Execute live MARA/DozerDB A-E arms with JSONL traces, resource metrics, and blinded judging.
- [ ] Run a single-host three-worker model/versioning workload using `MiniMax-M2.7`, `gpt-oss-120b`, and `gemma-4-31B-it`.
- [ ] Before paid full-factorial execution, implement canonical RDF semantic identity, persistent cross-process leases, stale-projection fencing/idempotency, and a case-level gold scorer.
- [ ] Implement and evaluate a hybrid context-delivery router: a minimal task bootstrap followed by receipt-pinned, just-in-time ontology and evidence tools.
- [ ] Add an execution-runtime receipt to every arm, and run the agent-selected JIT arm through the installed OpenAI Agents SDK rather than inferring SDK use from a prompt or model name.
- [ ] Close the RDF experiment observability gaps: content-free JSONL evidence, live Tempo readiness, Rust projection spans/metrics, and context/lock lifecycle signals.

## Surprises & Discoveries

- Observation: Oxigraph currently provides an in-memory RDF term read model; SHACL is offline pySHACL and optional OWL consistency is offline only. It is not an Oxigraph inference claim.
  Evidence: `dataplane/oxigraph_read_model/` and `src/seocho/ontology/rdf_governance.py`.
- Observation: `seochod` currently performs per-node/per-relationship Bolt writes, so Rust is not presumed faster.
  Evidence: `dataplane/seochod/src/main.rs`.
- Observation: the current bundle digest is an aggregate of serialization byte
  hashes, while the worker pin registry is process-local and `seochod` does not
  receive generation/fence/idempotency. The current code therefore cannot prove
  JSON-LD/Turtle semantic equality or reject a late N worker after N+1.
  Evidence: 2026-08-21 OS, LLM, and ontology workload review.
- Observation: this checkout has `openai-agents` 0.10.3 installed and the
  adapter/factory tests build real SDK objects, but the RDF diagnostic trace
  contains only the direct `ask_response()`/`rag.*` path. The optional dependency
  is unpinned while earlier live ADR evidence names 0.13.6, so SDK-version and
  runtime receipts are required before attributing a JIT result to Agents SDK.
  Evidence: 2026-08-21 local package inspection and RDF diagnostic JSONL audit.

## Decision Log

- Decision: Separate semantic/governance utility from Rust/Oxigraph systems utility.
  Rationale: SHACL conformance alone cannot establish grounded-answer accuracy; a faster sidecar cannot establish semantic value.
  Date/Author: 2026-08-21 / Codex.
- Decision: Do not use an opaque blended score as an adoption gate.
  Rationale: a high refusal rate or lower latency can hide lost valid facts. Primary semantic, safety, and systems gates remain independently visible.
  Date/Author: 2026-08-21 / Codex.
- Decision: Treat the next slice as experiment-enabling correctness work, not
  as an optimization. Do not spend on a full live factorial until the lock,
  semantic identity, and gold-score contracts are executable.
  Rationale: without them model/provider variance can be mistaken for ontology
  effect, and a stale write can make a lock experiment falsely pass.
  Date/Author: 2026-08-21 / Codex.

## Outcomes & Retrospective

The initial outcome is a report-level baseline, not an adoption claim. It records what the existing E2E path actually observed and makes insufficient data explicit. This section will record live results, cost, and the resulting routing/threshold recommendation after the matched experiment.

## Context and Orientation

`src/seocho/e2e.py` produces the CLI's `report.json`; query records already carry coverage, support status, selected-triple count, missing slots, and latency. `src/seocho/eval/semantic_scorecard.py` aggregates those records. `src/seocho/ontology/rdf_governance.py` produces a hash-pinned receipt. `dataplane/seochod` is the Rust Unix-domain-socket daemon that projects approved LPG payloads to DozerDB. `ActiveOntologyPointer` is the existing SQLite compare-and-swap primitive; it is not yet the RDF file publication lifecycle.

An ontology bundle is a portable, content-addressed directory containing one
declared RDF source serialization (JSON-LD, Turtle, or another supported RDF
serialization), optional deterministic interoperable derivatives, a manifest,
and purpose-specific agent profiles. JSON-LD is itself RDF and can be parsed by
RDF tooling; Turtle is not a prerequisite for SHACL validation or RDF storage.
The source serialization is therefore an experimental choice, not an assumed
product truth. A profile pool is an OS-managed cache of immutable,
already-verified profiles that agents lease by `(bundle digest, purpose,
workspace, generation)` instead of parsing or prompting with the full ontology
on every task. A lease is a read-only version pin, not a mutable shared object:
it expires when the agent finishes, while the pinned files remain valid for
replay and rollback.

### Context-delivery model

Ontology governance and context engineering solve different layers of the
same problem. Governance establishes which semantic snapshot is admissible;
context delivery decides which small, high-signal view of that snapshot reaches
an LLM on a given turn. Sending a complete JSON-LD/Turtle/TriG file in every
prompt is retained only as an experimental baseline, never as the assumed
runtime design.

The production candidate is hybrid progressive disclosure. Initial context has
only the task, compact purpose-profile summary, allowed relation/slot
vocabulary, pinned semantic/profile digests, and a small unambiguous tool set.
A deterministic stage router supplies the known minimum; the agent may expand
only through receipt-pinned tools if needed. Results return bounded semantic
closures and stable slice/evidence handles, not an arbitrary filesystem listing
or a complete ontology dump. After compaction, preserve a structured run note
(pinned tuple, retrieved handles, confirmed facts/provenance, unresolved slots,
and rejected candidates/reasons) rather than raw tool output.

Normal agent tools are deliberately limited to `ontology.profile`,
`ontology.slice`, `ontology.constraint`, and `ontology.evidence-pack`.
Curator operations such as bundle diff, activation, and rollback are excluded
from normal task context. The lock is a capability and consistency boundary in
the handle/receipt, not verbose prompt text. Logical namespace, purpose,
freshness, and size metadata may guide tool choice; host paths and unbounded
directory scanning must not be exposed as an implicit retrieval API.

### Execution-runtime truthfulness

`SEOCHO` is the common engine for every arm, but an OpenAI Agents SDK agent is
an additional execution runtime, not a synonym for an LLM call. The current
`seocho run` E2E path invokes `Seocho.ask_response()` and the deterministic
local query pipeline. It does not construct an Agents SDK `Agent` or invoke
`Runner.run`; its traces must be labelled `execution_runtime=seocho_direct`.
This makes A-E and J0/J1/J2/J4 useful deterministic baselines.

J3 (agent-selected just-in-time expansion) must instead construct the
SEOCHO-owned tool set through the installed OpenAI Agents SDK and call its
`Runner` through `AgentsRuntimeAdapter`. Its receipt must name
`execution_runtime=agents_sdk`, `agents_sdk_version`, `max_turns`, tool-set
digest, tool-call count, terminal outcome, and the underlying SEOCHO/LLM
versions. A test that only builds an `Agent` is wiring evidence, not an E2E
claim. Do not aggregate direct and Agents-SDK results as one arm; the
orchestration runtime is an independent experimental factor.

### Observability planes and vocabulary

JSONL is the portable, append-only **trace evidence artifact**; it is not an
observability stack and it is not a metrics database. The local observability
stack is `SEOCHO -> OTLP Collector -> Tempo (traces) + Prometheus (metrics) ->
Grafana`. Reports, gold labels, manifests, and receipts form a separate
reproducibility/evaluation plane. Every live experiment needs all three:

1. JSONL and Tempo traces answer per-run causal questions. Default traces are
   content-free; raw prompts/Cypher/evidence require explicit local capture.
2. Prometheus metrics answer aggregate latency, traffic, error, saturation, and
   bounded quality questions. Digests, workspace IDs, paths, request IDs, and
   source text belong only in traces/receipts, never metric labels.
3. An immutable run manifest/report binds the trace IDs and aggregate metric
   query window to exact corpus, code, model, ontology, profile, and lock
   identities.

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

Run a third context-delivery matrix using the same semantic graph, purpose,
task, model settings, and retrieval corpus:

1. J0: full source ontology in the initial prompt (baseline only).
2. J1: static compact purpose profile in the initial prompt.
3. J2: minimal bootstrap plus deterministic just-in-time profile/slice loading
   (the production candidate).
4. J3: minimal bootstrap plus agent-selected just-in-time expansion under a
   strict token and tool-call budget.
5. J4: answer synthesis receives only a typed evidence pack and its missing
   slot/provenance receipt, never the ontology source.

F/G/H measure agent and OS efficiency; I measures snapshot correctness. These
arms must preserve the exact requested bundle/profile digest and agent output
contract. J0-J4 measure context curation, not source serialization. Neither
matrix substitutes for A-E semantic comparison.

### Portable bundle and profile-pool hypotheses

H6 (portable replay): A bundle copied to a clean host remains verifiable and
produces the same profile digests and semantic context. It passes only when the
manifest, JSON-LD, Turtle, SHACL, profiles, and receipt hashes verify without a
host-specific path, database state, or mutable registry.

H7 (serialization and agent-readable view): JSON-LD, Turtle, and a compact
purpose profile are compared rather than assumed. A representation is better
only when it preserves RDF parse/SHACL results and gold triple F1,
required-slot recall, provenance coverage, and governance-rejection accuracy,
while improving a declared combination of tokenized prompt bytes, selection
latency, and agent repair rate. Compact profile JSON is a candidate agent
transport format, not a preselected winner.

H8 (profile pooling): A local pool reduces aggregate cold-start parsing and
resident memory for repeated agents without cross-workspace/profile leakage.
It passes only if p95 lease acquisition or prompt assembly improves, aggregate
RSS and file reads decline, and every response reports the exact pinned digest.

H9 (CLI lifecycle safety): Publish, lease, rollback, garbage collection, and
daemon reload never expose mixed artifacts. It passes only if a 10,000-operation
concurrent workload observes zero torn manifest/profile/receipt tuple, zero
stale-fence projection admission, and deterministic recovery after a killed
publisher or daemon.

H10 (ontology lock correctness): An ontology lock pins exactly one approved
bundle/profile/receipt tuple for an agent run and prevents concurrent activation
from changing that run's semantic contract. It passes only if a lock has an
owner, workspace, purpose, bundle digest, profile digest, generation, fencing
token, expiry, and renewal/audit outcome; stale or mismatched lock holders are
rejected before projection. A lock must not become a global long-lived mutex:
immutable bundle reads remain concurrent and only activation/lease bookkeeping
uses a short CAS-protected critical section.

H11 (model variance under one semantic contract): Three worker processes using
MARA models `MiniMax-M2.7`, `gpt-oss-120b`, and `gemma-4-31B-it` will produce
different extraction candidates, but a pinned ontology profile makes those
differences inspectable rather than silently incompatible. Measure candidate
triple agreement, relation direction, required-slot/provenance recall, SHACL
rejection, repair rate, and grounded answer quality by model.

H12 (lock protects cross-model version races): When one worker extracts under
generation N while another activates N+1 or rolls back, every candidate and
projection remains attributed to exactly one `(model, semantic digest, profile
digest, generation, fencing token)` tuple. An N candidate can be audited or
revalidated under N+1, but cannot project as though it had been generated under
N+1. Stale projection is rejected and recorded.

H13 (context curation utility): J2/J4 preserve or improve gold triple F1,
required-slot/provenance recall, and correct abstention relative to J0, while
reducing initial-context tokens and avoiding excess retrieval turns. J3 is
adopted only if its extra autonomy improves a task-quality gate enough to pay
for its navigation latency, failed tool calls, and context use.

H14 (long-horizon semantic continuity): A structured run note survives
compaction without changing the pinned semantic contract. It passes only if
later-turn slot/triple/provenance accuracy is non-inferior to an un-compacted
control and stale handle, digest mismatch, or unresolved-slot loss is visible
in the trace.

## Concrete Steps

1. Create a local-only gold corpus manifest and record its SHA-256, model, prompt/profile digest, git revision, DozerDB/Oxigraph/seochod versions, resource limits, warmup, and concurrency in every JSONL trace.
2. Extend E2E tracing so each index candidate records parse validity, source/provenance coverage, relation endpoints, receipt tuple, projection outcome, and latency stages. Each query must retain the evidence bundle and answer judge result.
3. Implement immutable bundle directories `.seocho/ontology/bundles/<bundle-digest>/`: write to a same-filesystem temporary directory, fsync, verify manifest hashes, atomically rename, then CAS the per-workspace `current` pointer. The pointer must include bundle/profile/context digests plus generation, epoch, and fencing token.
4. Require projection requests to carry the pinned pointer tuple and idempotency key. The daemon rejects stale/mismatched tuples, bounds requests, uses a private socket directory, and records applied/no-op/error results.
5. Use MARA as a blinded answer/evidence judge only after deterministic gold metrics are emitted; preserve prompts, judge model, and judge result in the local trace.
6. Compare report artifacts with `compare_semantic_utility`; record per-case deltas rather than only averages.
7. Define the portable, serialization-neutral on-disk layout before implementing pooling:

       .seocho/ontology/
         bundles/<bundle-sha256>/
           manifest.json
           source.<jsonld|ttl>
           derived/ontology.<jsonld|ttl>
           derived/shapes.<jsonld|ttl>
           agent-profiles/indexing.json
           agent-profiles/query.json
           agent-profiles/projection.json
           governance-receipt.json
         current/<workspace>/<package>.json
         locks/<workspace>/<lock-id>.json

   The manifest is the only file an agent/CLI resolves first. It declares the
   source MIME type/RDF syntax, parser/derivation version, every immutable
   artifact, and their SHA-256 digests. `current` and `locks` are the only
   mutable records; update them with atomic replace/CAS and fsync. Never let
   agents scan arbitrary ontology directories or infer the active version from
   filenames.
8. Add a CLI contract with explicit lifecycle operations: `ontology bundle
   build`, `ontology bundle verify`, `ontology activate`, `ontology lease`,
   `ontology lock acquire`, `ontology lock renew`, `ontology lock release`,
   `ontology rollback`, `ontology gc --dry-run`, and `ontology status`. The
   first implementation must expose read-only status/verify before destructive
   garbage collection. A lease response supplies only the purpose profile,
   manifest receipt, generation/fence, and filesystem-safe path or UDS handle.
   A lock response additionally has a bounded TTL and must be present in the
   projection receipt/event; `release` is idempotent and expiry is auditable.

### Implemented single-host CLI slice (2026-08-21)

`seocho ontology bundle build` writes a new bundle to a same-filesystem staging
directory, verifies its manifest hashes, fsyncs artifacts, and atomically
renames it. It refuses to overwrite an existing bundle. `bundle verify` is a
content-free integrity check. `activate` performs the existing SQLite/WAL CAS
using an explicit fencing token and optional `generation:epoch` expectation.
`lease` and its `lock` compatibility spelling persist an expiring exclusive
writer lease in the same state database; `status` exposes only the active
pointer and live leases. `gc --dry-run` is intentionally report-only.

This is a one-host operator control plane, not an etcd substitute and not yet
projection admission. A `seochod` request still cannot prove its lease/profile/
receipt tuple against this database, so this slice must not be used to claim
cross-process stale-write protection end-to-end.
9. Execute a format-neutral workload before choosing the authoring canonical:

   - parse the identical ontology authored/serialized as JSON-LD and Turtle;
   - run the same SHACL shapes/data through the selected RDF validator and
     Oxigraph load path; assert equal normalized triples and conformance;
   - give each representation, then each compact profile, to the same MARA
     indexing/query tasks with fixed prompts; judge triple/slot/provenance
     outputs against gold labels;
   - measure bytes, token count, parse/load p50-p95-p99, RSS, file reads,
     deterministic diff quality, agent repair turns, and error rate.

   Select the canonical source format only after this workload. It is valid to
   retain multiple canonical-equivalent serializations when one is best for
   author review and another is best for RDF tooling, provided the manifest
   explicitly names the authoritative graph digest.
10. Execute a three-process model/versioning workload. Give each MARA model a
    unique worker/process ID but the same documents, workspace, and gold cases:

        A. all workers pin generation N/profile P_N and extract;
        B. activate N+1 while N workers finish, preserving N receipts;
        C. revalidate N candidates under N+1 before projection;
        D. roll back to N, create fresh pins, and verify new-worker attribution.

    Canonical DozerDB projection is allowed only for a candidate with the
    matching active generation and fencing token. This measures model variance
    and proves why a single-host lock matters.
11. Implement the four live-experiment blockers before Step 10:

    - Semantic identity: canonicalize ontology, shapes, and data RDF datasets;
      define `governance_contract_digest` over their canonical digests, profile
      derivation, validator configuration, and named-graph/inference policy.
    - Cross-process lease: persist read pins/leases in the local control plane
      with process nonce, owner, TTL, complete tuple, and recovery semantics;
      process-local refcounts remain only an in-process optimization.
    - Projection admission: pass workspace, digest tuple, generation, epoch,
      fence, lease, and idempotency key to `seochod`; verify admission before a
      transactional/staged graph projection and reject stale/mismatched writes.
    - Gold scorer: score RDF term equality, triple/slot/provenance precision and
      recall, correct abstention, expected SHACL violation/repair, and version
      delta outcome per case. Keep answer models, extractors, and judges
      separately identified.
12. Implement the context-delivery contract before J0-J4:

    - Bind every `ontology.slice`, `ontology.constraint`, and
      `ontology.evidence-pack` response to the pinned semantic/profile digest,
      generation, fence, and a stable result handle; reject a handle from a
      different lock tuple.
    - Make every result bounded and typed. A semantic slice includes only the
      closure needed for requested terms/slots plus required SHACL/provenance
      constraints; an evidence pack includes selected facts, source spans,
      missing-slot reasons, and no unrelated ontology dump.
    - Persist a compact structured run note outside the chat transcript. It
      records the pinned tuple, task, slice/evidence handles, decisions,
      confirmed source-grounded facts, and open slots. It is reloadable after
      compaction and auditable, but does not become an unbounded hidden prompt.
    - Use a deterministic router for known workflow stages first. Agent-driven
      expansion is a separately budgeted arm, with explicit no-result and
      insufficient-context responses so dead-end navigation is measurable.
13. Add observability admission checks before a paid arm:

    - Emit one `experiment.run` root trace with a generated run ID and runtime
      receipt; all index, query, context, LLM, Rust projection, and evaluator
      spans must be descendants or carry that ID.
    - Verify JSONL has no raw content with capture disabled and that the
      configured OTLP Collector accepts the root trace and Tempo can retrieve
      it. A running container or a Prometheus-only health check is insufficient.
    - Add bounded metrics and trace spans for ontology bundle verification,
      activation, lease acquire/renew/expiry, stale-fence admission rejection,
      profile/slice/constraint/evidence tool calls, slice token/byte size,
      insufficiency, run-note compaction/reload, and Rust daemon request bytes,
      queue wait, server duration, outcome, and idempotency/admission result.
    - Distinguish implementation status from a zero: an unavailable daemon,
      lock backend, or native DozerDB metric source is `unsupported` in the
      run manifest, never a healthy zero metric.

## Validation and Acceptance

Run focused checks:

    uv run pytest -q tests/seocho/test_semantic_scorecard.py tests/seocho/test_e2e_runner.py
    uv run pytest -q tests/seocho/test_projection_receipt.py tests/seocho/test_seochod_projection.py
    cargo test --offline --manifest-path dataplane/seochod/Cargo.toml
    bash scripts/ci/run_basic_ci.sh

For every live arm, capture E2E report JSON/Markdown, JSONL observability trace, process RSS/CPU/FDs, socket bytes/queue depth, Bolt round trips, stage p50/p95/p99, and DozerDB version. A passing mock is not live-performance evidence.

Pre-register initial gates: grounded answer slot accuracy and triple F1 non-inferior (paired lower confidence bound at least -0.02); evidence coverage improves by at least 0.05 and missing slots fall by at least 0.05; unsupported claims do not increase; invalid-rejection precision and recall at least 0.95; no unreceipted or stale-fence canonical write; zero torn profile/receipt reads across 10,000 swaps; Rust/Oxigraph p95 overhead no worse than 15% unless a documented quality or isolation benefit offsets it. Change these only before looking at the relevant arm results.

The 40-60 case corpus is a semantic pilot, not sufficient evidence for a 95%
rejection precision/recall gate. Maintain a separate, substantially larger
labelled valid/invalid validation set for that gate. Randomize/counterbalance
arm order, preserve provider retries/request IDs, and use paired case-level
confidence intervals. Record extractor model, answer model, judge model,
temperature, top-p, max tokens, prompt digest, and retry count separately.

For F-I also record canonical/profile bytes, tokenized prompt bytes, profile
selection correctness, profile-load/lease p50-p95-p99, cold and warm process
RSS, CPU, open FDs, filesystem opens/bytes, cache hit rate, pool eviction,
lease contention, CAS retries, and cross-workspace digest mismatches. H7/H8
fail if any required ontology element is absent from an agent response or if a
lease returns another workspace's profile, even when latency improves.

For H10 record lock acquisition latency, contention/retry count, TTL expiry,
renewal failures, stale-fence rejections, abandoned-lock recovery time, and the
ratio of concurrent immutable reads to activation critical-section time. The
test workload includes 1, 4, and 16 agents reading different purposes from the
same bundle while another process activates, rolls back, or crashes. A lock is
correct only if each response and projected fact can be traced to one complete
lock tuple; it is efficient only if readers do not serialize behind writers.

For H11/H12 record model/provider request ID where available, worker/process
ID, semantic/profile digests, generation/epoch/fencing token, candidate hash,
triple count, direction error, slot/provenance recall, SHACL/repair/admission/
revalidation/projection outcomes, and query scorecard. Report per-model
distributions and same-case deltas; cross-model profile leakage, stale-fence
projection, or a missing model/ontology receipt tuple is a hard failure.

For J0-J4 additionally record `execution_runtime`, runtime/Agents-SDK version,
tool-set digest, `max_turns`, initial/total context tokens, context-tool calls
by bounded tool name/outcome, no-result/insufficient responses, slice/evidence
bytes and tokens, run-note compaction/reload, and the context-to-answer trace
completeness outcome. Aggregate quality metrics are evaluation-only and retain
the bounded `(cohort, arm, model, dimension)` labels; exact case IDs, digests,
and source spans remain in the JSONL trace and immutable report.

## Idempotence and Recovery

Every arm uses a new workspace/database or a documented cleanup transaction, never an ambiguous reused graph. Immutable bundles are never overwritten; retrying publication verifies the same digest and then performs a no-op/CAS retry. Rollback updates only the current pointer to a previously verified digest. A failed daemon projection must record its idempotency key and outcome before retry; do not rely on client wall-clock timestamps for fencing.

## Artifacts and Notes

Keep raw documents, API keys, traces, and per-case outputs under ignored `.seocho/` or `outputs/`. Promote only aggregate reproducible measurements and a dated scrubbed report under `docs/experiments/`. `report.json` contains `agent_scorecard`; it is a baseline and comparison input, not a product-quality claim by itself.

The file-format decision is deliberately open: JSON-LD, Turtle, and compact
profiles each have a role only if the workload proves it. The invariant is not
the filename extension; it is a manifest-pinned normalized RDF graph digest,
approved receipt, purpose profile, and bounded ontology lock. The OS CLI owns
atomic activation and locking so agents preserve RDF semantics without being
forced to parse a full source serialization on every task.

## Cost, Latency, and Provider Policy

MARA is approved for the blinded judge and live E2E experiment. Record model name, temperature, request/response usage, retry count, and cost where the provider reports it. Do not spend on a full factorial until deterministic smoke verifies receipt admission, graph cleanup, and trace persistence. Stop an arm on repeated service errors and report the gap rather than substituting mock numbers.

## Interfaces and Dependencies

Public E2E reports gain an additive `agent_scorecard` field with schema `seocho.agent_semantic_scorecard.v1`. `compare_semantic_utility(baseline, governed)` accepts two report scorecards and returns `seocho.rdf_governance_lift_comparison.v1`. The scorecard is pure Python and has no provider, Oxigraph, DozerDB, or filesystem dependency. Live experiment wiring depends on MARA, DozerDB Bolt, the optional Rust daemon, and existing vendor-neutral tracing.
