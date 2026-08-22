# Hierarchical ontology evaluation protocol

SEOCHO evaluates ontology value as a chain of separately falsifiable effects,
not one blended score. An answer benchmark can measure answer quality without
being a gold graph benchmark; a SHACL suite can measure admission soundness
without proving answer quality. Combining either into a single average would
hide both failures and governance trade-offs.

## Evaluation layers

| Layer | Question | Primary measurement | SEOCHO comparison |
| --- | --- | --- | --- |
| Ontology learning (TBox) | Are proposed terms, taxonomies, relations, and axioms useful? | reviewed term/taxonomy/relation/axiom precision and recall | basic extraction versus LLMs4OL-framed candidate generation |
| Extracted graph (ABox) | Are typed facts, directions, and source links correct? | triple F1, direction accuracy, required-slot and provenance recall | raw candidate versus SHACL-approved candidate |
| Query | Does a question yield the required, safe result set? | intent/slot recall, result-set F1, executable rate, repair count | introspected schema versus declared profile versus JIT slice |
| Answer/context | Is the answer grounded at a fixed context budget? | official benchmark answer metric plus evidence/missing-slot contract | bare context versus static profile versus JIT slice |
| Governance | Do invalid, stale, unreceipted, or cross-workspace actions stop? | acceptance/rejection confusion matrix and torn-tuple count | direct, shadow, governed, and lockdown modes |
| Systems | What is the operational price? | p50/p95 latency, tokens, retries, RSS, FDs, DB/Rust calls | identical successful cases across arms |

The `seocho.evaluation_case_envelope.v1` contract links these labels by a
stable case identifier and source-snapshot digest. A layer is `reviewed`,
`unannotated`, or `unavailable`. Only `reviewed` layers may be scored. This
keeps an upstream answer-only corpus from becoming invented triple or Cypher
gold.

Use the local-only annotation gate before a paid arm:

```bash
uv run python scripts/benchmarks/evaluation_case_envelopes.py \
  --input .seocho/gold/cases.jsonl \
  --output outputs/evaluation/case-envelope-report.json
```

The output has only case hashes, layer states, and coverage counts; raw source
text, answers, triples, and Cypher remain in the local input artifact.

## Benchmark roles

GraphRAG-Bench remains an answer/rationale track. Its question files do not
provide source-span bindings, gold triples, or gold Cypher; those fields remain
unannotated until a separately reviewed SEOCHO extension layer exists. It must
not be reported as a Text2Cypher or RDF-governance accuracy result.

LLMs4OL framing supplies the ontology-learning decomposition, but a candidate
report is review-only. Its metrics answer whether ontology construction
improved; they do not establish that a projected graph or agent answer is
correct.

The governed Text2Cypher fixture is an execution and admission smoke workload.
It becomes a semantic benchmark only after each case has reviewed required
slots, expected result identifiers, graph triples, and source bindings.

## Integrated case lineage

```text
source snapshot
  -> ontology terms/profile
  -> reviewed triples + source bindings
  -> intent, slots, allowed path, expected result ids
  -> answer or safe abstention
  -> valid/invalid/stale governance variants and receipts
```

Do not require every layer to exist before beginning. First publish annotation
coverage, then score only the available layers. The experiment report must
separate `unannotated` from a zero result.

## Decision rule

Report an outcome vector, not a weighted grand score:

- semantic: ontology, triple, query-result, and answer metrics;
- governance: accepted-invalid, accepted-stale, accepted-unreceipted, torn
  tuple, and workspace-leak counts;
- context: selected slice/profile size and repair actions;
- systems: latency and resource overhead.

Hard invariants are zero accepted invalid/stale/unreceipted canonical writes
and zero cross-workspace leakage. A latency increase is acceptable only when a
separately reported safety or semantic outcome improves. A score from an LLM
judge is secondary until calibrated against reviewed labels; a contradictory
judge response is `unavailable`, not a fail or pass.

## Execution order

1. Create 20--30 reviewed seed envelopes from a fixed local corpus. Include
   in-schema, ambiguous, relation-direction, missing-slot, invalid, stale, and
   cross-workspace variants.
2. Run deterministic direct/shadow/governed admission mutations. This tests
   governance before spending model budget.
3. Run query arms at fixed model, seed/order, context budget, and retry policy.
   Compare introspected schema, declared profile, JIT slice, and governed mode.
4. Run answer-only GraphRAG-Bench arms separately with the official-compatible
   evaluator. Add extension labels only under a separately versioned review
   artifact.
5. Scale to lifecycle contention and multiple models only after the seed set
   has stable gold coverage and the judge agreement is measured.

This protocol implements the evaluation intent of Beads `seocho-hr3`,
`seocho-52i`, `seocho-5cz`, and `seocho-ia4.13`; it does not replace the
broader memory-contract experiments in `seocho-vdw`.
