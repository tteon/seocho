# AgenticOS workshop: governed schema-contract evidence plan

## Purpose / Big Picture

This plan turns SEOCHO's current ontology, lifecycle, Rust projection, and
agent-context work into a falsifiable workshop paper. The paper does not claim
to deliver a complete operating system or to outperform vector retrieval on
narrative QA. Its narrower claim is that a portable, governed schema contract
is a useful systems primitive when agents share mutable graph memory: it binds
what may be written, which ontology version was read, how a candidate was
validated, and which canonical projection received it.

An operator should be able to run the paired workloads in this plan and inspect
one content-free JSONL/OTLP trace, immutable profile/bundle digests, lifecycle
lease/fence information, candidate receipt, and result scorecard. A negative
result is publishable: it means a primitive did not improve the stated outcome
enough to justify its cost.

## Progress

- [x] Record baseline paper scope in Beads item `seocho-vdw`.
- [x] Implement immutable RDF bundles, agent profiles, lifecycle leases/fences,
  candidate staging, SHACL receipt, and Rust `seochod` projection boundary.
- [x] Add module-quality scorecard and `ready`/`needs_reasoning`/`reject`
  profile gate.
- [x] Run a live MiniMax ontology-context pilot and a 3-model namespace smoke.
- [ ] Instrument every formal arm with one root experiment trace and run
  manifest before treating it as paper evidence.
- [ ] Run the governed RDF/SHACL/DozerDB gold-graph arm.
- [ ] Run the module-quality gate and version/lease contention arms.
- [ ] Freeze results, statistical analysis, and 2-page versus 6-page decision.

## Problem Definition

Agents can persist facts, call tools, and act across sessions, but current
memory components usually expose storage and retrieval rather than a contract
for shared state. A graph schema alone is insufficient: an agent may read an
old ontology, write an unvalidated fact, use a different relation vocabulary,
or project after its authority is stale. These failures are hard to notice from
fluent output and are amplified when several models/processes share one graph.

We define a *governed schema contract* as the tuple
`(bundle_digest, profile_digest, data_digest, validation_receipt, workspace,
generation, epoch, fencing_token, lease_id)`. It is a compact capability that
ties an agent's bounded ontology context to the candidate it produced and the
canonical graph projection it is authorized to make.

The research question is: **when does carrying this tuple through an agent
workflow provide a measurable benefit over passing schema text or writing to a
graph directly, and what is its operational cost?**

## Scope and Claims

Tier 1, evaluated claim: a governed schema contract provides verifiable
admission, version attribution, and portable type/address-space behavior for
agent graph memory.

Tier 2, systems framing: these primitives compose as a small Agentic OS layer:
ontology/profile is a type interface, interned identity is an address space,
workspace is a protection domain, lease/fence is change control, and the Unix
socket projection boundary is a syscall-like write boundary. Scheduling and
token budgets are useful but commodity mechanisms; they are not SEOCHO's
principal novelty claim.

Non-claims: no universal QA-quality superiority over vector RAG, no proof of
OWL entailment from a structural module scorecard, no distributed multi-host
lock service, no production throughput claim from mocks, and no hidden model
chain-of-thought as evidence.

## Primitives and Repository Mapping

| Primitive | Contract | Implementation | Observable evidence |
| --- | --- | --- | --- |
| Immutable ontology bundle | one authored JSON-LD graph with derived Turtle/SHACL | `src/seocho/ontology/rdf_bundle.py` | manifest and bundle SHA-256 |
| Bounded profile/slice | agent sees purpose-specific, JIT context rather than a full file | `lifecycle.py`, `openai_agents.py` | profile hash, slice size, tool span |
| Module-quality gate | structural quality changes allowed agent action | `module_scorecard.py` | gate disposition, verification-call count |
| Candidate receipt | data RDF conformed to the pinned shapes/profile | `rdf_governance.py`, `candidate_stage.py` | receipt/data graph digest, SHACL outcome |
| Lifecycle capability | a writer must hold a live matching version lease/fence | `active_pointer.py`, `lifecycle.py` | generation/epoch/fence, stale rejection |
| Canonical projection syscall | only approved typed payload reaches DozerDB | `dataplane/seochod` | daemon span, Rust driver receipt, idempotency |
| Typed evidence bundle | answer carries slots, triples, provenance, insufficiency | `src/seocho/query/` | coverage, missing slots, provenance recall |

## Hypotheses and Falsifiers

H1, context contract: declared ontology context improves schema conformance and
lowers invented identifiers over a thin schema, with identical model/question
conditions. Fail if the paired difference is not positive or regressions exceed
the pre-registered confidence interval.

H2, semantic admission: SHACL-receipted candidate staging prevents invalid or
stale candidates from canonical projection without reducing gold triple F1 or
required-slot recall by more than 0.02. Fail on any accepted invalid/stale
canonical write, or if non-inferiority fails.

H3, portable namespace: multiple model families using one ontology and identity
policy converge on canonical entity addresses more often than an ungoverned
name-only baseline, without unacceptable collision rate. Fail if agreement is
no better or collision/incorrect merge rises.

H4, quality-aware context: a `needs_reasoning` profile produces the prescribed
bounded slice/interface-verification action and improves interface-sensitive
gold accuracy or safe abstention. Fail if the gate does not change evidence
selection, action count, answer behavior, or outcomes.

H5, lifecycle isolation: activation/rollback during concurrent work never
causes a torn profile/receipt tuple or stale-fence canonical write. Fail on one
counterexample; performance is secondary.

## SEOCHO Evidence Contract

Every formal query records intent, required slots, relation path, selected
triples, provenance, missing slots, profile/bundle digest, and gate decision.
The answerer may abstain when slots are missing; a fluent unsupported answer is
scored as a failure. Raw documents, prompts, RDF, and Cypher stay out of the
default trace. They can be preserved only in a local, access-controlled gold
artifact referenced by a digest.

## Experimental Design

Use matched, counterbalanced case order, fixed temperature/top-p, extractor
model, answer model, context budget, and retry policy. Record model/provider
request ID when supplied. Do not compare a weak model with an ontology against
a strong model without one.

1. **E1: context A/B.** Thin labels versus declared ontology profile. Outcome:
   Cypher conformance, identifier invention, scope violations, tokens, latency,
   and repair count. The existing 8-question MiniMax pilot is diagnostic only;
   promote to 40--60 paired cases before paper inference.
2. **E2: candidate-to-canonical A/B.** Same extracted graph candidates through
   direct/shadow/governed modes. Outcome: SHACL invalid rejection precision and
   recall, gold triple precision/recall/F1, relation direction, slot and
   provenance recall, evidence coverage, missing slots, receipt rate, p50/p95
   stage latency, bytes, RSS, FDs, and DozerDB/Rust request counts.
3. **E3: profile-quality A/B.** Full profile versus declared narrow modules
   that are `ready`, `needs_reasoning`, and `reject`. Outcome: gate exposure,
   bounded verification calls, interface-slice completeness, safe abstention,
   token/latency cost, and gold task score. The gate must not be credited merely
   because it emitted metadata.
4. **E4: lifecycle/versioning.** Three Mara workers pin N, activate N+1,
   revalidate candidates, then roll back. Run 1/4/16 readers plus a writer.
   Outcome: torn tuple count, stale-fence rejection, lease acquisition/renewal
   latency, contention, recovery, and projection attribution.
5. **E5: portability.** MiniMax-M2.7, gpt-oss-120b, and gemma-4-31B-it process
   the same fixed gold corpus under one identity policy. Outcome: address
   agreement, incorrect-collapse rate, ontology conformance, candidate receipt
   rate, and per-model graph/slot metrics.

## Metrics and Decision Thresholds

Use `score_gold_graph` for triple precision/recall/F1, relation-direction
accuracy, required-slot recall, and provenance recall. Use
`score_semantic_utility` only for operational outcomes; its supported-rate is
not answer correctness. Use module structural metrics as a policy input, not a
semantic truth score.

Pre-register: H2 requires zero unreceipted/stale canonical writes; invalid
rejection precision and recall >= 0.95 on a sufficiently large labelled set;
triple F1 and slot recall paired lower confidence bound >= -0.02; evidence
coverage +0.05 and missing slots -0.05 for a positive utility claim. H4
requires action mediation plus a confidence interval excluding zero for at
least one prespecified quality/safety outcome. H5 requires zero torn tuples.
Latency overhead is reported; a p95 overhead >15% needs a documented safety or
quality benefit rather than being hidden in an average.

## Observability Contract

Before a paid formal arm, emit one `experiment.run` root span and JSONL record
with run ID, git revision, workload/gold digests, bundle/profile/receipt
digests, lifecycle tuple, model/config digest, services/versions, warmup,
concurrency, limits, and capability status. Child stages include profile load,
slice, LLM extraction, candidate staging/SHACL, admission, Rust projection,
query/evidence selection, answer/judging, and scorecard. Metrics have bounded
labels only. An unavailable service is `unsupported`, never a zero.

The current live A/B scripts are diagnostic because they do not yet emit this
full root trace. Their numbers remain local pilot artifacts and must be rerun
after the trace wrapper is installed before use in a paper table.

## SEOCHO Review Panel

Professor lens: do not equate SHACL conformance, structural modularity, or
support proxy with semantic truth; use gold labels and blinded judging.

Software-engineer lens: preserve typed public contracts, content-free traces,
idempotent artifacts, and explicit failure states. Never make a profile gate
depend on model-private reasoning.

Computer-systems lens: measure service versions, live dependency availability,
wall latency distribution, bytes, memory/FDs, provider cost/retry, lock
contention, and daemon overhead. Separate a correctness invariant from a speed
claim.

Decision: retain the governed-schema-contract framing. It is narrower and more
falsifiable than calling the product an Agentic OS, while still contributing
concrete OS-relevant primitives to the workshop discussion.

## Plan of Work

First add a reusable experiment manifest/root-trace wrapper and make each
formal workload emit it. Then create a content-free, versioned 40--60 case
gold corpus with triples, slots, provenance, and expected invalid cases. Run
E2 before broad model sweeps because it establishes whether the governance
primitive actually changes admission. Add E3 only after the Agent SDK tool
trace can verify the mandated action. Run E4/E5 against immutable snapshots,
not mutable working files. Freeze raw artifacts by digest, analyse paired
case-level differences with confidence intervals, and write an ADR/report that
states supported, rejected, and inconclusive hypotheses.

## Concrete Steps

1. Create a local `outputs/agenticos/<run-id>/manifest.json` and JSONL root
   trace via the existing experiment observability helpers; add deterministic
   tests for required fields and content redaction.
2. Add a small gold corpus and scorer adapter under `examples/` only if it is
   shareable and content-safe; otherwise keep corpus private and commit its
   schema/manifest plus test fixtures.
3. Start `seochod` with live DozerDB credentials and `SEOCHOD_CONTROL_DB`; build
   an immutable bundle, activate it, acquire a projection lease, and execute
   direct/shadow/governed cases. Preserve receipts and report capability gaps.
4. Add an Agent SDK trace assertion that `needs_reasoning` yields the required
   verified slice before graph work, while `reject` withholds vocabulary.
5. Execute the matrix with MARA and publish only aggregate, digest-linked
   results. Update the Beads item and decision log after each hypothesis.

## Validation and Acceptance

Run focused contracts before each live arm:

    uv run pytest -q tests/seocho/test_module_scorecard.py tests/seocho/test_ontology_lifecycle.py tests/seocho/test_projection_receipt.py tests/seocho/test_seochod_projection.py
    cargo test --offline --manifest-path dataplane/seochod/Cargo.toml
    bash scripts/ci/run_basic_ci.sh

For a live arm, retain the exact command, report JSON/Markdown, JSONL trace,
OTLP availability result, service versions, and local resource sample. A mock
or a direct-only run cannot satisfy governed-E2 acceptance.

## Idempotence and Recovery

All bundles are new immutable directories. Lifecycle activation uses CAS;
leases expire or are explicitly released; Rust projection carries an
idempotency key. Use a fresh workspace/run ID for every live arm and write to a
dedicated disposable experiment graph/database where available. On failure,
preserve the partial manifest and mark the capability unavailable; do not turn
the result into a mock success or delete receipts.

## Artifacts and Notes

Local paid-run artifacts remain ignored under `outputs/`. Public commits carry
only workload schemas, fixtures without secrets, deterministic scorer code,
the ExecPlan, tests, and aggregate evidence summaries. The active Beads item is
`seocho-vdw`; RDF scorecard work is related item `seocho-hr3`.

## Interfaces and Dependencies

The baseline is OpenAI Agents SDK, Mara OpenAI-compatible endpoint, DozerDB,
Rust `seochod`, Oxigraph bounded read model, PySHACL offline validation,
SQLite/WAL single-host lifecycle store, JSONL/OTLP vendor-neutral observability,
and `workspace_id` propagation. Owlready2 remains optional/offline only.

## Surprises & Discoveries

The first MiniMax pilot showed a large context-conformance difference, but has
only eight cases and no full experiment root trace. The three-model namespace
smoke reached full agreement on two documents, which demonstrates executability
only. Neither result supports a paper-scale claim yet.

## Outcomes & Retrospective

Not complete. This section will record, per hypothesis, the frozen run IDs,
confidence intervals, negative results, operational cost, and the decision to
submit a 2-page position/experience report or a 6-page empirical paper.
