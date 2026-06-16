---
title: "Ontology Guardrails: Stop Guessing Which Schema Helps Your LLM — Measure It"
date: 2026-06-16
authors:
  - seocho
tags:
  - Release
  - Ontology
  - Finance
  - Evaluation
excerpt: A guardrail is only as good as its fit to your corpus. SEOCHO now profiles your corpus, scores candidate ontologies, picks the best guardrail automatically, and measures the LLM before vs after — so you adopt a schema on evidence, not vibes.
---

A **guardrail** in SEOCHO is a curated ontology — a typed schema of classes and
relationships — that constrains what an LLM is allowed to extract from your
documents. The promise is real: higher precision, fewer hallucinated entity
types, answers you can trace to a typed path in a graph.

But there is a question almost every "use an ontology to constrain the LLM" pitch
skips:

> **How do you know the guardrail actually helps — on *your* corpus?**

A schema that is too lean misses what your documents are about. A schema that is
too rich adds noise the model never needed. And a schema that looks great but
omits one type your corpus depends on will *silently drop facts*. The only honest
way to settle it is to measure the LLM with the guardrail and without it.

That measurement loop is now a first-class part of SEOCHO. This post walks the
whole thing on a small financial corpus — and shows you the real numbers,
including the one that surprised us.

## The thesis in one sentence

> **The ontology should carry the quality bar — but you should never adopt one
> without measuring what it does to the answer.**

## The loop: profile → score → select → measure

There are four moves, and SEOCHO ships an API for each.

### 1. Profile your corpus — what types does it actually need?

The trick is to start *schema-free*. Run an open extraction over your corpus and
count the entity types that come back. Those frequencies describe what the corpus
demands, independent of any candidate ontology.

```python
from seocho.ontology_scorecard import build_corpus_profile
from seocho.guardrail_selector import numeric_intensity

# graphs = open, ontology-free extraction over your documents
profile = build_corpus_profile(graphs, source="FinDER tutorial subset")
print(profile.label_frequencies)   # {'FinancialMetric': 31, 'Company': 16, 'Product': 9, ...}
print(numeric_intensity(profile))  # 0.4  -> entity-leaning, not purely numeric
```

On our 10-case FinDER slice the corpus needs `FinancialMetric`, `Company`,
`Product`, `Person`, `BusinessSegment`, `CurrencyAmount`, `Location`, and more —
20 distinct types. `numeric_intensity ≈ 0.4` tells us it is **entity/qualitative-leaning**.
That single number drives the selector later.

### 2. Score candidate ontologies — with a corpus-aware scorecard

`score_ontology` grades an ontology across five quality dimensions — structural
integrity, taxonomy health, definitional completeness, constraint richness,
functional coverage — and, when you pass a corpus profile, a sixth:
**corpus coverage**, i.e. how well the schema's vocabulary covers what the corpus
actually needs. The `guardrail` weight profile emphasizes the dimensions that
predict extraction-guardrail value.

```python
from seocho.ontology import Ontology
from seocho.ontology_scorecard import score_ontology

for name, onto in candidates.items():        # lean (2 classes), base (4), rich (9)
    card = score_ontology(onto, corpus_profile=profile, profile="guardrail")
    cov = card.dimension("corpus_coverage").score
    print(f"{name}: grade={card.grade} corpus_coverage={cov:.2f}")
    for w in card.weak_points:                # concrete, fixable gaps
        if w.dimension == "corpus_coverage":
            print("   gap:", w.message)
```

```text
lean: grade=B corpus_coverage=0.52
   gap: Corpus mentions 'Person' 5× but the ontology has no matching class — add it (or an alias).
   gap: Corpus mentions 'Product' 9× but the ontology has no matching class — add it (or an alias).
base: grade=B corpus_coverage=0.60
rich: grade=B corpus_coverage=0.71
   gap: Corpus mentions 'CurrencyAmount' 3× but the ontology has no matching class — add it (or an alias).
```

The scorecard is doing the work an ontology engineer would otherwise do by hand:
it tells you *exactly which corpus-needed types each candidate is missing*. Notice
that even the richest candidate (`rich`, coverage ≈ 0.71) still has gaps. Hold
that thought.

### 3. Select the guardrail — automatically and domain-adaptively

`select_guardrail` combines coverage with the corpus's numeric intensity and
applies a rule we *measured* across every FinDER category: a richer guardrail
materially improves answers in entity/qualitative domains, but is
neutral-to-harmful in numeric ones (where the real lever is numeric *validation*,
not more vocabulary). So:

```python
from seocho.guardrail_selector import select_guardrail

rec = select_guardrail(candidates, profile)
print(rec.chosen)        # 'rich'
print(rec.domain_kind)   # 'entity'
print(rec.rationale)
# entity/qualitative corpus (numeric_intensity=0.4); a richer guardrail materially
# improves answers here, so chose the highest-coverage guardrail 'rich' (coverage 0.71).
```

For an entity-leaning corpus it picks the highest-coverage candidate. For a
numeric-heavy one it would instead pick the *leanest adequate* guardrail and
advise applying numeric validation — the opposite reflex, and the right one.

### 4. Measure — BEFORE vs AFTER

Now the part most tools skip. Answer the same questions twice — once from the
open extraction, once from a guardrailed extraction — and have a separate judge
(temperature 0) grade each against the reference.

On our run, the open baseline scored **0.90** and the guardrailed extraction
scored **0.80** on N=10. The guardrail *lowered* accuracy. That is not a bug in
the tutorial — it is the most important thing it teaches.

## What the measurement revealed

Look at the one case that flipped from right to wrong:

> *"Where is Apple headquartered?" → "Cupertino, California."*

The **open** extraction captured a `Location` entity, so the baseline answered
correctly. The **`rich`** guardrail has **no `Location` class** — so the
guardrailed extraction discarded the headquarters, and the model answered
"cannot determine from the provided context."

That is *exactly* the coverage gap the scorecard flagged in step 2. The lesson:

- **A guardrail only helps where its coverage matches the corpus.** Constraining
  extraction to a schema that omits a needed type silently drops facts. The
  corpus-coverage dimension exists to make that risk visible *before* you ship.
- **Coverage is a selection proxy, not a guarantee of answer accuracy.** In a
  powered evaluation — 400 cases, paired McNemar test, bootstrap confidence
  intervals, two-judge consensus — we found a fully-automated, official-FIBO-derived
  guardrail to be *statistically equivalent* to a hand-curated one on answers
  (Δ ≈ 0.0, p = 1.0), even when their coverage differed. A ±0.10 swing on N=10 is
  noise. **Don't conclude from small N; run a powered sweep before claiming a win.**
- **This is the virtuous cycle, and it's the actual product.** The scorecard told
  us `rich` is missing `Location`, `Product`, `CurrencyAmount`. Add those classes,
  re-score (coverage rises), re-measure, and the answer number recovers. *Measuring
  is the feature; the guardrail is just the lever.*

We think honest negative results like this are more useful than a demo where the
guardrail always wins. In regulated finance especially, you want a system that can
*tell you when a schema would quietly cost you an answer* — not one that hides it.

## Try it — runs with no infrastructure

There is a new, self-contained tutorial notebook that runs the whole loop end to
end. The offline core (profile → score → select) is pure Python — no graph
database, no API key. The before/after measurement ships a cached run, so the
notebook executes as-is; set an API key only to re-run the LLM steps live.

```bash
pip install seocho
jupyter lab tutorials/ontology_guardrail_before_after.ipynb
```

Or open it straight from the repo with the **Open in Colab** badge at the top.

## Declare it in a run spec — no Python

If you use SEOCHO's YAML runner, you can hand it the candidates and a corpus
profile and let it choose:

```yaml
ontology:
  select:
    candidates:
      lean: fibo_minus.jsonld
      rich: fibo_plus.jsonld
    corpus_profile: my_corpus_profile.json
documents: ./filings
questions:
  - "Where is the company headquartered?"
```

There is also an `ontology.select.fibo` block that builds a **version-pinned,
official-FIBO-derived guardrail** — it bridges the EDM Council's FIBO modules to
your corpus's vocabulary and picks the best-covering one, with zero hand curation.
And we ship a canonical financial corpus profile, computed over the full 5,654-case
FinDER corpus, so the selector ranks candidates against real-world demand out of
the box.

## The takeaway

Ontology guardrails are powerful, but "add a schema and trust it" is faith, not
engineering. SEOCHO turns the schema decision into a measured one: profile the
corpus, score the candidates, let the selector pick, and check the LLM before vs
after — with the scorecard explaining *why* the number moved. Adopt a guardrail on
evidence.

*See the tutorial in `tutorials/`, the selector and scorecard in the
[Python SDK](/sdk/), and the design decisions behind the domain-adaptive rule and
the powered FIBO evaluation in the repository's `docs/decisions/`.*
