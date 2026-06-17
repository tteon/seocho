# Pre-Registration — RAG vs. GraphRAG on SEOCHO (replication of arXiv 2502.11371)

**Frozen:** 2026-06-16 (before any scored multi-case run). Edits after results are seen VOID the affected slice (CLAUDE.md §20.4/§20.5).
**Paper:** Han et al., *RAG vs. GraphRAG: A Systematic Evaluation and Key Insights* (arXiv 2502.11371).

## 0. Honest scope (binding)
We compare **BGE-384 vector RAG** against **SEOCHO ontology-governed typed-graph-as-context** (a structured-RAG / local-search analog) at **equal raw-text budget**, with **ontology size** as an added variable. This is **NOT** Microsoft community-summary GraphRAG (no Leiden communities / global search). Any claim is scoped to "vector vs SEOCHO graph-as-context," never "RAG vs GraphRAG" unscoped. (A GDS-Leiden community-summary lane is deferred to a possible phase 2.)

## 1. Central hypothesis under test
**H0 (paper, complementarity):** vector wins single-hop / fine-grained; graph wins multi-hop / reasoning-intensive.
**Co-registered null (SEOCHO priors — FinDER/BC3/GraphRAG-Bench):** at equal raw budget, graph-as-context **TIES** vector, **no crossover**, **flat ontology curve**. The loss in prior work was serializer + budget, **not** recall. A TIE is the expected, publishable outcome.

## 2. Lanes (fair by construction — same cases/chunks/generator/metric)
- `vector` — BGE-384 dense top-k over the case chunks. `ontology:n-a`.
- `graph` — typed structure **+ the same top-k raw chunks vector gets** (equal budget), per ontology arm.
- `vector_graph` — same raw chunks + structure, same total budget.

## 3. Ontology arms (open-domain; FIBO does NOT apply) — nested supersets, only the ontology moves (§20.9)
`non-ontology` (Entity/RELATED_TO floor) ⊂ `generic-er` (`ger`: Person/Org/Location/Work/Event + 5 rels) ⊂ `event-temporal` (`ger`+`evt`: +TimePoint/Role + ordering/causation).
Per-dataset pre-registered: HotPotQA→`generic-er` Goldilocks; MultiHop-RAG temporal→`event-temporal` has a reason to help; NovelQA→`generic-er`. NQ control→all arms ≈ vector (graph win = red flag).

## 4. Datasets / slices / metrics (seed 42, stratified)
| Dataset | Status | Slices | Metric | Pre-reg direction |
|---|---|---|---|---|
| HotPotQA (dev-distractor) | ✅ live | HP_bridge, HP_comparison | official token-F1 + EM (deterministic, no judge) | graph (bridge); comparison borderline |
| NovelQA-proxy (GraphRAG-Bench `novel`, **4-class, NOT official 21-type — disclosed substitution**) | ✅ live | NV_Fact_Retrieval (**RAG control**), NV_Complex_Reasoning, NV_Contextual_Summarize | LLM judge-panel accuracy; Creative Generation **excluded** (n=67, not scorable) | graph (reasoning/summarize) |
| MultiHop-RAG (`yixuantt/MultiHopRAG`) | ❌ phase 2 | Inference, Comparison, Temporal, **Null (abstention control)** | accuracy | graph |
| NQ (`nq_open`) | ❌ phase 2 (optional) | single factoid | token-F1 | **RAG control floor** |

References = **all 10 distractor paragraphs** (HotPotQA) / full source novel (NovelQA): graph build and vector index read **byte-identical** source per case (symmetric provenance).

## 5. VOID-unless guardrails (red-team)
1. Equal raw-text budget across lanes; log `ctx`/`prompt_tokens`.
2. **Extraction recall logged per case** beside every graph score (C2). *(NOTE: the title-match proxy in the S2 smoke is unreliable — must be redefined content-based before trusted.)*
3. Symmetric provenance (byte-identical source to graph-build and vector-index).
4. Deterministic metric primary for short-answer sets; ≥3-model MARA judge panel + Cohen's κ only for NovelQA long-form; headline the **paired delta**, never a single-judge mean (DeepSeek lenient — never solo).
5. Per-(dataset×model) DozerDB isolation (`rag<mmdd><dataset><model>`); arms isolated by `workspace_id`; detached resume-safe sweep (§13); no silent fallback (§20.2) — failures recorded, N attempted vs N scored reported.
6. Embedder = local BGE everywhere (MARA hosts no embeddings; OpenAI not used). Generator/extractor = MARA `gpt-oss-120b`.

## 6. Harness (built 2026-06-16)
- `examples/open_domain/ontology_modules/{ger,evt}.yaml`, `compose.py`
- `scripts/benchmarks/open_domain_loaders.py` (HotPotQA + NovelQA-proxy → case-dict)
- `scripts/benchmarks/open_domain_vector_arm.py` (BGE vector lane; `--smoke` = $0)
- `scripts/benchmarks/open_domain_graph_arm.py` (graph + hybrid lanes, equal budget, token-F1)

## 7. Staging
S0–S1 ($0 BGE smoke) ✅ · S2 (1 case ×3 arms, ~36 calls) ✅ 2026-06-16 — path proven, n=1 so **no findings**. S3 (mid sample, judge panel for NovelQA) and full run require explicit cost sign-off (~32K calls full).
