# ADR-0112: Where Graph-RAG Wins and Doesn't — Retrieval Quality Ties, Governance/Determinism Is the Differentiator

Date: 2026-06-13
Status: Proposed

## Context

The program's through-line: does an **ontology-governed knowledge graph** answer questions
better than plain **vector retrieval**? The product bet says yes. But every prior study
(FinDER, BC3, GraphRAG-Bench gold-triple) kept showing **graph ≤ vector**, which threatened
the thesis — and the results were confounded.

ADR-0105 cleaned up the biggest confound on the official GraphRAG-Bench harness: the prior
"graph < vector" was a **serialization defect** (the graph stored the raw passages but the
serializer dropped them when building LLM context), and once both systems serve raw text at a
**fair budget**, graph-as-context **ties** vector.

This ADR consolidates the two follow-ups — (1) cross-domain generalization, (2) a direct
measurement of graph's *actual* differentiator — into a single strategic conclusion.

## Findings (measured)

### 1. The retrieval-quality tie generalizes across domains
Re-ran the 5-lane keep_raw comparison on a **second domain** (GraphRAG-Bench `medical`,
1.05M-char doc), 2-judge (fast_judge gpt-oss-120b + official ragas):

- **H2 (serialization confound) generalizes.** Structure-only is worst; adding each system's
  stored raw text lifts answer quality on **both systems and both judges, both domains**
  (e.g. SEOCHO struct→+raw, novel CR +0.86 fast / +0.30 ragas; medical +0.28 / +0.10;
  Graphiti likewise).
- **H1 (fair-budget graph ≈ vector) generalizes.** A budget-matched graph lane
  (`seocho_keepraw_topk` = typed structure + the *same* top-k chunks vector gets) **ties**
  vector in both domains; in medical it carried **2.75× vector's context yet did not win** →
  the typed-structure layer is **neutral-to-noise** for narrative/multi-hop QA.
- **New (engineering):** at equal budget, Graphiti's *relevance-ranked* episode retrieval beat
  SEOCHO's *query-independent whole-structure dump* on the large medical corpus → SEOCHO's
  serializer should be relevance-ranked + budget-bounded, not a dump.

Mechanism: under a strong synthesizer, **retrieval representation does not move answer quality
once the underlying text is equivalent** — so narrative-QA accuracy benchmarks tie by design.

### 2. Graph's real differentiator is governance/determinism — measured, with honest scope
Two independent demonstrations of the same principle (declared-schema coverage → refuse rather
than fabricate):

- **Relation-coverage Answerability Gate** (`route_policy.py`, BC3 emails, prior work):
  refusing to serve answers from **undeclared / prompt-smuggled** edges eliminated
  experiment-0's **69% E4 silent-wrong at $0** routing time; the governed extension yields
  human-gold **F1 0.41** with 100%-grounded provenance and an **LLM-free per-topic FOR/AGAINST
  aggregation** that vector structurally cannot produce.
- **Finance arbiter** (10-K, this round; `arbiter.py` + `semantic_query.py` over the closed
  `DEFAULT_FINANCE_CONCEPTS`): out-of-schema concepts → **structural refusal 8/8,
  silent-wrong 0/8**; in-schema concepts → routed to serve 8/8.

**Honest, decisive scope:** a *well-grounded, capable* LLM (vector RAG with a real excerpt)
also avoids fabrication — vector silent-wrong was **0/8** under both naive and strict prompts.
So the gate is **not** an answer-quality win over good RAG. Its value is a **deterministic,
prompt/model/grounding-independent guarantee**, and it materializes exactly where LLMs are
dangerous: **ungrounded** (closed-book vector fabricated **3/8** out-of-schema answers — a
confident ACSI score, a brand-value figure, an ESG carbon number — all from priors), **weak
models**, or **adversarial/smuggled context** (the BC3 69% case).

## Decision

1. **Stop competing on "graph beats vector on narrative/multi-hop QA accuracy."** It ties by
   design; report ties honestly. Any future graph "win" on a retrieval-quality bench must first
   be checked for the serializer and budget confounds (ADR-0105).
2. **Position and evaluate graph on its real axes:**
   - **Governance / refusal** — declared-schema coverage gate; metric = silent-wrong rate +
     refusal precision/recall. Graph refuses structurally; vector fabricates when ungrounded/weak.
   - **Provenance / determinism / LLM-free aggregation** — per-entity joins & aggregations served
     from the graph with source quotes, reproducibly, at ~$0 (vector cannot enumerate/aggregate
     across many entities). Demonstrated on BC3.
   - **Logical inference / entailment** — derive facts absent from every passage. **NOT YET
     MEASURED** (open thread: LUBM/UOBM, ProofWriter/FOLIO) — the third differentiator to prove.
3. **Engineering:** make the SEOCHO graph serializer **relevance-ranked + budget-bounded**
   (replace the whole-structure dump), per the medical finding. `keep_raw` stays the default
   (ADR-0105).

## Consequences

- **Eval suite reorientation:** add a governance/refusal bench (Answerability Gate held-out —
  code already exists) and an entailment bench; demote narrative-QA accuracy to a parity check,
  not the headline metric.
- **Product narrative:** graph = governed, auditable, deterministic, LLM-free-at-scale — *not*
  "smarter retrieval."
- **Honest limits:** 2 corpora; n = 8–50 per cell; single-model / single-judge in places; the
  entailment axis is unmeasured. The governance gap was shown specifically under ungrounded /
  smuggled-context conditions, not over well-engineered RAG.

## Related
ADR-0105 (serializer confound + fair-budget tie), ADR-0102 (prior-resistance benchmark),
ADR-0103 (semantic-layer / arbiter). Comparison harnesses and the Answerability-Gate experiment
stay local/untracked per the established commit policy; this ADR + the per-lane score artifacts
are the shipped record.
