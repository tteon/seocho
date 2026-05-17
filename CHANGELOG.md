# Changelog

All notable changes to this project are documented here. Versioning follows
[Semantic Versioning](https://semver.org/).

## [0.4.1] — 2026-05-16

### Added
- **`seocho.ontology.Ontology.from_ttl(path)`** — load an ontology from an
  OWL/SKOS Turtle file. Reads ``owl:Class``, ``owl:ObjectProperty`` (with
  ``rdfs:domain`` / ``rdfs:range``), and descriptions from
  ``rdfs:label`` / ``rdfs:comment`` / ``skos:definition``. Requires
  ``rdflib`` (``pip install rdflib``). Closes the in-curriculum gap where
  notebooks expected ``Ontology.from_ttl`` but only ``from_yaml`` /
  ``from_jsonld`` / ``from_dict`` were available.

## [0.4.0] — 2026-05-16

Minor release that consolidates the engineering-improvement candidates
surfaced while building the `teaching-resource/` curriculum (8 bd tickets
closed). All additions are new top-level submodules; nothing in 0.3.x is
removed or renamed, so 0.3.x consumers upgrade without code changes.

### Added

- **`seocho.store.llm.LLMBackend.chat(text, *, system=None, ...)`** — single-shot
  convenience for notebooks / REPL. Production callers should keep using
  `.complete(system=..., user=...)`. Closes [`seocho-9s80`].
- **`seocho.index.sanity`** — read-only temporal sanity checks for indexed
  graphs. `run_temporal_checks()` returns a `TemporalReport` with 5 metrics:
  future-dated provenance, inverted temporal ranges, orphan extractions,
  stale entities, non-monotonic source versions. `assert_clean()` raises
  `TemporalAnomalyError` on any violation. Closes [`seocho-le4c`].
- **`seocho.index.metadata`** — canonical property-name constants for
  `(:Source)`, `(:Chunk)`, ontology-class entities, `[:MENTIONS]` and
  `[:RELATED_TO]`. Plus `RunContext` + `provenance_stamp()` helpers for
  callbacks that want to write standardized extraction provenance.
  Closes [`seocho-hpml`] (MVP — full IndexingPipeline integration pending).
- **`seocho.eval.benchmarks.finder`** — HuggingFace `Linq-AI-Research/FinDER`
  loader with `load() / by_category / sample_random / sample_per_category /
  category_distribution`. Schema is normalized to notebook-friendly fields
  (`id, question, document_text, answer, category, reasoning_required,
  type, references`). Cache directory honours `$SEOCHO_DATASET_CACHE_DIR`.
  Closes [`seocho-ci24`].
- **`seocho.gds`** — `gds_session()` context manager + `MetricSpec` enum +
  `GDSEstimate.fits()` heap-fraction guard + `.louvain()` that auto-writes a
  `GDSRunMeta` node. Projection auto-dropped on `__exit__` even on
  exception. Closes [`seocho-xuof`].
- **`seocho.query.guards`** — 12-pattern Cypher validator for free-form LLM
  output. `validate_cypher()` returns `list[CypherIssue]` with severities
  `block | warn | info` covering label hallucination, property typo,
  missing LIMIT, destructive ops, unbounded paths, cartesian products,
  missing DISTINCT, wrong relationship direction, temporal-ignorant
  queries. Coexists with the existing constrained-plan validator at
  `seocho.query.cypher_validator`. Closes [`seocho-ixsk`].
- **`seocho.routing`** — `RoutingPolicy.decide()` + `RoutingDecision`
  declarative routing surface, with confidence thresholds, adaptive
  context-window budget, exponential `staleness_penalty()`, and a refusal
  decision tree. `RoutingDecision.to_metadata()` emits a stable
  Opik-friendly dict. Closes [`seocho-mcg1`].
- **`seocho.debate`** — `DebatePolicy`, `convergence_curve()` (citation
  Jaccard), `should_stop()` with 5 early-stop criteria, intent →
  participants heuristic, and 5 anti-pattern detectors (echo chamber,
  sycophancy, citation drift, context drop). Closes [`seocho-vij5`]
  (orchestrator bridge to `extraction.debate.DebateOrchestrator` deferred
  to a follow-up).

### Curriculum
- New `teaching-resource/` with 5 chapter notebooks + Reveal.js slide
  decks + chapter-by-chapter depth appendices (property design, GDS
  engineering, Cypher failure taxonomy, routing decision design, debate
  convergence). All chapters demonstrate the 4-provider (OpenAI / Kimi /
  DeepSeek / Grok) comparison pattern and Opik per-member project routing.

### Notes
- `seocho.index.metadata` MVP only documents the schema and ships
  helpers; the `IndexingPipeline` write path will adopt the helpers in a
  follow-up so existing graphs are not affected by this release.
- `seocho.debate` currently exposes the telemetry primitives without
  wrapping `extraction.debate.DebateOrchestrator` — the orchestrator
  bridge will land in 0.4.x once the SDK orchestration surface
  stabilises.

[`seocho-9s80`]: https://github.com/tteon/seocho/issues
[`seocho-le4c`]: https://github.com/tteon/seocho/issues
[`seocho-ci24`]: https://github.com/tteon/seocho/issues
[`seocho-xuof`]: https://github.com/tteon/seocho/issues
[`seocho-ixsk`]: https://github.com/tteon/seocho/issues
[`seocho-mcg1`]: https://github.com/tteon/seocho/issues
[`seocho-vij5`]: https://github.com/tteon/seocho/issues
[`seocho-hpml`]: https://github.com/tteon/seocho/issues


## [0.3.2] — prior release
Earlier history is tracked in git commits; see `git log` and the
`docs/decisions/` ADRs for context up to 0.3.2.
