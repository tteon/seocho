# ADR-0220: arm×organ A/B — instrument pivot to GraphRAG-Bench medical, controls, and scope

- Status: accepted (pre-registered BEFORE the run)
- Date: 2026-08-17
- Tickets: seocho-5ny (OS ablation epic)

## Context

The arm×organ A/B (7 arms: BARE, GOVERNED, 5 leave-one-outs over intern/schema/pin/
workspace/guardrail; ADR-0205 structured runtime) first ran on the erb cross-source
working set's gold questions. Result (n=10/arm, `outputs/agentos/erb_xsource_workingset/
arm_organ_results.json`): **all 7 arms coverage 0.00–0.11, universal abstain** — while the
mechanism plane toggled perfectly (pinned 10/10 vs 0/10, ws_enforced 10/10 vs 0/10).
Diagnosis: those gold questions are procedural / document-QA; their answers live in prose,
not in Customer/Component/Issue/Policy triples. Wrong instrument, null by construction.

A 6-lens adversarial review of the session then verified three harness-corrupting bugs
and three design gaps that had to be fixed before any run could be interpreted.

## Decision

1. **Instrument = GraphRAG-Bench medical** (independent benchmark: 2,062 questions with
   gold answers + gold evidence; corpus indexed in full — 301 chunks, gpt-oss-120b
   extraction, 0 generic-Entity fallbacks). Question sample: deterministic (id-sorted)
   7 per type × {Fact Retrieval, Complex Reasoning, Contextual Summarize} = 21.
   Creative Generation excluded (not graph-answerable).
2. **Controls (user rule: ceiling+floor, null spread beside effects):**
   - FLOOR `floor_closed_book` — same generator, question only, NO graph. If the floor is
     high, the public-textbook corpus is memorized and coverage cannot support a graph
     claim; we report it as such rather than claiming an organ effect.
   - CEILING `ceiling_gold_evidence` — gold evidence handed as context (perfect retrieval).
   - Every organ effect is read within the floor→ceiling band.
3. **Bugs fixed before the run** (verified in code by the review, then fixed + unit-checked):
   - introspected schema block corrupted character-by-character (engine returns pre-joined
     strings; orchestrator re-joined them) → shape-tolerant `_introspected_schema_text`;
   - governed-no-workspace unsatisfiable (generator unscoped ∧ guardrail unconditionally
     requiring tenant scope → always abstain) → tenant-scope guardrail rules gated on
     `arm.workspace_enforce`;
   - deterministic `gold_hit` passed wrong answers that echo the question → question
     tokens subtracted from the gold token set before the 60% threshold.
4. **Honesty relabels:** `governed-no-intern` is reported as an **index-time no-op
   control** (interning = `cross_source_unique` at index time; a query flag cannot ablate
   it; expected identical to governed). The real intern ablation is a dual-index run
   (cross_source_unique ON vs OFF), tracked as follow-up.
5. **Scope of the clean run (pre-registered):** single tenant, single ontology version,
   sequential load. Under this load, workspace and pin/RCU are structurally unable to
   change answers — this run is the **task-parity / governance-precision control**
   (prior finding: "task 동등, 거버넌스 압도"), NOT the load-bearing test. The
   load-bearing claims are gated on two follow-up adversarial probes:
   (a) poisoned-second-tenant leak probe (workspace organ), (b) ontology-mutation-mid-run
   probe (pin/RCU organ).

## Measurement

Plane-1 mechanism (deterministic): schema_source pinned/introspected, ws_enforced,
guardrail_rejected, repair_attempts, rows. Plane-2 answer quality: DeepSeek-V3.1 judge
(cross-vendor vs gpt-oss generator) coverage of the gold answer + deterministic gold_hit;
n=21/arm is descriptive, not powered — reported as such. Artifacts pinned: graph in
`medicallpg` (workspace `med`), ontology fingerprint recorded in the results JSON,
results at `outputs/agentos/medical_arm_organ_results.json`.

## Consequences

- The clean A/B can legitimately show: governance costs nothing on task quality
  (parity within the band) while adding the mechanism guarantees (Plane-1).
- It cannot show organs are load-bearing; that evidence comes from the two probes.
- Follow-ups: dual-index intern ablation; poisoned-tenant probe; mutation probe;
  live RLS validation for the provenance organ.
