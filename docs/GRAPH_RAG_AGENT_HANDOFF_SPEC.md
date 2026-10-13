# SEOCHO Graph-RAG Agent Handoff Spec

Date: 2026-04-09
Status: Active design brief

This note turns the finance tutorial failure analysis into a SEOCHO-specific
handoff contract for graph-grounded answering work.

Use it when redesigning or extending:

- `extraction/semantic_query_flow.py`
- `runtime/public_memory_api.py`
- `extraction/memory_service.py`
- answer grounding contracts in the memory-first SDK and runtime APIs

## Why This Brief Exists

SEOCHO already has the right building blocks:

- runtime raw ingest
- graph-aware memory APIs
- fulltext-first semantic resolution
- vocabulary and ontology hint layers
- debate and semantic answer paths

The remaining risk is familiar: graph retrieval can look good in traces while
the final answer still behaves like broad text retrieval with graph-flavored
hints.

The design target is therefore not "more graph metadata."

The design target is:

`question intent -> required slots -> selected subgraph -> evidence bundle -> grounded answer`

## Core Design Position

Treat the graph as the answer substrate, not as a weak reranking hint.

A graph-grounded SEOCHO answer is successful only when the runtime can:

1. infer the question intent
2. identify the required relation paths and answer slots for that intent
3. retrieve the specific subgraph that fills those slots
4. serialize the result into an inspectable evidence bundle
5. answer from that bundle while naming what is still missing

If the runtime only finds a broadly relevant neighborhood and then falls back to
nearby text, it is not yet solving the right problem.

## SEOCHO Requirements

### 1. Intent-First Retrieval

Every semantic answer path should map the question to a lightweight
`intent_id` with:

- `required_relations`
- `required_entity_types`
- `focus_slots`

The initial catalog can stay small and deterministic. Prefer:

- rule-based intent hints
- ontology/vocabulary-aware keyword triggers
- narrow prompt-assisted intent mapping only when rules are insufficient

Do not build a large router taxonomy before the minimal causal path works.

### 2. Evidence Bundle Contract

The retrieval layer should emit a compact internal bundle instead of only raw
candidate nodes or edge IDs.

Recommended fields:

- `intent_id`
- `candidate_entities`
- `selected_triples`
- `slot_fills`
- `missing_slots`
- `provenance`
- `confidence`
- `database` or `graph_id`

This bundle should be diffable, traceable, and suitable for API/debug payloads.

### 3. Missing Information Must Stay Visible

The answerer must not silently fill missing slots from nearby but ungrounded
text.

If required slots are absent:

- answer only from grounded fields
- name the missing slots
- avoid hallucinated slot completion

Conservative incompleteness is better than fluent fabrication.

### 3b. Graph-CoT Agent Lane Stays Typed

When the semantic query surface runs with `query_mode="graph_cot"`, keep the
answering lane explicit:

`SemanticLayer -> QuerySupervisorAgent -> Text2CypherAgent -> AnswerGenerationAgent -> AnswerGuardrailAgent -> Finalize`

Recommended artifact chain:

- `GraphCoTQuestionFrame`
- `SupervisorDirective`
- `QueryEvidencePacket`
- `AnswerDraft`
- `GuardrailVerdict`

The answer guardrail may use ontology checks and model suspicion to revise or
refuse, but that suspicion must stay evidence-negative only: it can flag a leap
or ask for abstention, never invent a missing fact.

### 3c. Multi-Agent Swarms Assemble Evidence, Not Prose

When several specialists are required, route by required answer slots and run
the smallest authorized coalition. Every specialist must declare the slots it
can fill and return typed evidence with provenance. Filter protected evidence,
surface conflicts, and preserve specialist failures before synthesis.

The swarm output is one evidence bundle. Independent answer debate is a
separate mode and must not be used merely to increase agent count. A partial
coalition result must retain `missing_slots` and may not be rewritten as a
complete answer.

### 4. Quality Signals Must Change Evidence Selection

Keep quality-aware logic only when it changes one of:

- edge inclusion
- edge ranking
- slot confidence
- abstention behavior

If a quality flag does not alter the chosen bundle, it is reporting metadata,
not answer-layer logic.

### 5. Fair Evaluation Keeps The Answerer Fixed

When comparing:

- question-only
- reference-only
- graph-grounded

keep the following fixed:

- answer model
- answer instruction
- output style
- context budget

Only the evidence source should change.

### 6. Failure States Must Be Inspectable

Runtime artifacts should make it obvious whether the miss came from:

- wrong intent
- wrong entity resolution
- missing required relations
- missing entity types
- partial slot fill
- low-confidence support
- abstained answer

This is required for honest evaluation and triage.

### 7. Prompt Optimization Must Emit A Receipt

The context composer should emit a content-free `PromptAssemblyReceipt` that
shows candidate/selected section counts, token estimates, token budget,
compression ratio, stable-prefix identity, evidence/provenance counts, and
bounded exclusion reasons. Attach only its aggregate trace attributes to the
`context.assemble` span; keep prompt text and model reasoning out of telemetry.
See `docs/PROMPT_OPTIMIZATION_OBSERVABILITY.md`.

## Target Runtime Mapping

### Semantic Layer

Primary owner: `extraction/semantic_query_flow.py`

Add or expose:

- lightweight `IntentSpec`
- question-to-intent mapping
- slot-aware evidence selection
- explicit `missing_slots`

### Memory Service And Public API

Primary owners:

- `extraction/memory_service.py`
- `runtime/public_memory_api.py`

Expose enough grounding detail to inspect:

- which graph or database answered
- which memories or triples supported the answer
- which slots remained unresolved

### SDK And Typed Payloads

Primary owners:

- `seocho/types.py`
- `seocho/client.py`

Prefer typed evidence-bundle fields over opaque nested dicts where the public
contract needs to surface answer grounding.

## Anti-Patterns To Avoid

Do not drift into these patterns:

1. "Some relevant graph edges exist, so coverage is good."
2. "Graph evidence nudged sentence selection, so this counts as graph QA."
3. "Quality-aware logic exists in code, therefore it matters."
4. "A larger multi-agent architecture will solve slot-missing failures."
5. "Ontology-specific extraction is worse because a coarse proxy metric says so."

## Staged Implementation Plan

### Stage 1. Intent And Slot Contract

- add a small `intent_id` catalog for common memory and graph queries
- define `required_relations`, `required_entity_types`, and `focus_slots`
- keep the first implementation deterministic and auditable

### Stage 2. Evidence Bundle Selection

- rank candidate evidence by slot completion, not only local relevance
- emit `selected_triples`, `slot_fills`, `missing_slots`, and provenance
- make quality metadata affect ranking or abstention explicitly

### Stage 3. Grounded Answer Synthesis

- synthesize answers from the evidence bundle first
- keep text snippets as secondary support, not hidden primary evidence
- surface missing slots in the response contract

### Stage 4. Evaluation And Tracing

- keep answer-layer comparisons fair by fixing the answerer
- add trace fields that explain intent selection, slot fill, and abstention
- prefer small manual gold or answer-slot reviews over coarse proxy-only claims

## Acceptance Criteria

Treat a Graph-RAG change as acceptable only when it can show:

1. how the runtime infers `intent_id`
2. which slots and relations are required for that intent
3. which evidence filled those slots
4. which slots remained missing
5. how the final answer stayed grounded in that bundle

## Shared semantic implementation ownership

SDK query helpers own ontology hints, profile selection, vocabulary aliases and
artifact reads; extraction exposes compatibility adapters (ADR-0234). Prompt
composition and constraint slices share pure SHACL candidate merging. This
refactor preserves intent, required slots, provenance, insufficiency and existing
ranking behavior; it is not new answer-quality evidence.


## Opt-in Collaboration Experiment

`src/seocho/agent/collaboration.py` implements the experimental evidence
assembly comparison described in
[ADR-0236](decisions/ADR-0236-budgeted-graphrag-collaboration.md).
Code-scheduled baselines are extended by native SDK `Agent.as_tool()` Manager
and `handoff()` Router modes. Both use the same retrieval workers and separate
answerer. Cross-agent context is host-owned task/evidence packets; SDK handoff
filtering removes router history. Actual native transitions and agent-tool calls
are distinguished from reference-exchange telemetry. Every nested model turn,
tool dispatch and native transfer shares the global budget, and accumulated
model input is bounded before dispatch. Reports retain agent design versions,
context bytes (not tokens), transitions and duplicate retrieval counts.
Workers use bounded, validated, workspace-scoped read recipes and return actual
query rows with source references. Planner tasks declare required slots, relation
paths, earlier-task dependencies, and per-task model-call allowances. One reviewer round may request additional
retrieval, but cannot add evidence. The fixed answerer cites assembled records
and preserves missing slots. Reference validity is enforced; claim entailment and
slot completion still require evaluation.

This path does not change normal SDK or runtime answering. See
[the runnable experiment](../examples/graphrag-collaboration/README.md) for the
FinDER original-filing pilot, synthetic regression fixture, MARA/DozerDB setup,
budget semantics, and live-validation gaps. Gold membership is evaluation-only;
source graph construction and query ranking must never consume it. Reference
coverage is conditional on exact source-span mapping and must report unmapped
gold separately.


### Fixed-evidence design comparison

`src/seocho/eval/fixed_context_designs.py` implements an earlier, no-database
comparison: Standard, CoT prompting, decomposition within one Solver, sequential
Agent.as_tool delegation to one Specialist, and parallel Agent.as_tool delegation
to multiple Specialists. It freezes a question-selected original FinDER evidence
bundle across all arms. CoT is a prompt-level instruction; private reasoning is
not exposed or scored. Typed subtask results preserve task identity, findings,
references and missing information. Citation identity is checked, while entailment
requires separate evaluation. Shared budgets include all nested agent turns.
The runnable CLI is examples/graphrag-collaboration/run_fixed_finder.py. Gold is
used only after inference; separately budgeted judgments are recorded as model
judgments. This does not alter production query routing or graph retrieval.

Research comparison runners use the [Recorder transport contract](AGENT_OBSERVABILITY.md)
for full model request/response evidence. HTTP tags declare tiers; full decision,
realization and tool lineage remain a separate instrumentation requirement.

Phase 2 research runs emit explicit decision/realization events with source
items, returned subtask contexts, call IDs and actual usage. Forced and agent-decided
information tiers are separate conditions; fixed passage plugins preserve the
FinDER comparison baseline. Evaluation gold is in a separate artifact/domain.

Recorded research runs support pinned causal branch replay and targeted
reference knock-in through the [Recorder contract](AGENT_OBSERVABILITY.md).
Cached prefix usage is inherited; modified workers and synthesis/evaluation are
fresh. This does not imply dependent-DAG replay in the normal SDK query route.

The optional [research signal analysis](AGENT_OBSERVABILITY.md) reads audited
subtask/evidence delivery records into a separate derived database. Silver consensus,
alignment confidence and reference availability are proxies; they are not subtask
gold, evidence-use proof or automatic agent memory inputs.

Recorder research Phase 5 exposes offline population reports through `obs report`.
Agent information source fingerprints and declared tool identities support fairness
checks; unknown legacy identities are not inferred. Statistical reports do not
change query routing or silently fetch new evidence. See `AGENT_OBSERVABILITY.md`.

Recorder Phase 6 UI renders recorded plan dependencies and solver routing/context
edges, with silver divergence explicitly labeled as a proxy. It does not infer a
causal task chain from execution order. Node interventions are isolated Mock branches
through Phase 3; operational query routing and production memory policy are unchanged.

Optional research Pi inference is described in `docs/AGENT_OBSERVABILITY.md`. It is a controlled, Recorder-backed experimental harness condition with the SDK evaluator retained; it does not change the production SDK/runtime handoff contract or establish Pi-native multi-agent orchestration.
FinDER evaluator v2 annotations (gold references, domain, original reasoning
boolean/type) remain evaluator-only. Architecture selection must use held-out
within-stratum evidence; ordinary inference routing must not consume evaluation
annotations. The evaluation domain also records deterministic `verify_receipts`
events separately from semantic model judgments.

Research Recorder tool-agent condition: the model can request a specialist as a
tool after fixed-source delivery. The specialist receives only those passages;
returned citation IDs must belong to that delivered context. Shared physical LLM
call limits include manager turns, specialist work and evaluation. This optional
research condition does not change the public query routing policy. See
`docs/AGENT_OBSERVABILITY.md` for evaluation limits and unsupported replay/Pi paths.

Research quality-lab coordination can pass previous findings to a later solver,
review peers against delivered evidence, or choose existing tasks for one review
round. Record source child steps and the actual context payloads, and use revised
findings in synthesis. Public task plans and fixed sources isolate coordination;
these experiments do not alter public Graph-RAG routing or graph construction.

Single-agent research metacognition keeps required-slot and unsupported-assumption
claims public and separate from private reasoning. An initial fixed-source answer
is retained before model-selected tools; specialist delegation is disabled in
this arm. Capability requests do not establish ontology/KG necessity. Missing
source or unavailable graph evidence remains explicit; see Recorder contract.

Research single-agent execution-path conditions use the same solver model for
public planning, sequential subquestion solving and synthesis. Ordered dependency
plans deliver explicit prior results and preserve missing slots; adaptive mode
can use fixed-source tools without specialists. Plan-quality judging is separate
from gold-based answer judging. This experiment does not change production
routing or claim internal reasoning-trace access.

Matched research coordination optionally reuses one worker identity for fixed
sequential tasks and synthesis. Explicit returned contexts remain host-managed
and source-audited. Compare this identity-only control with separate sequential
agents before attributing quality to multiple agents; independent and peer
review arms change context flow. No production query/handoff behavior changes.
