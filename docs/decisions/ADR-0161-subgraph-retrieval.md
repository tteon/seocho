# ADR-0161: string→subgraph retrieval — intern resolution vs fuzzy-vector RAG

Date: 2026-08-15 · Status: accepted (measurement record) · seocho-zfe

## Context

Paper spine (wiki/paper-spine-string-to-subgraph.md): answering a text request
from graph memory is string→entity→subgraph, and boundary 1 (resolve a surface
mention to the ONE canonical entity it denotes) is the highest-leverage failure —
a wrong anchor returns a confidently wrong subgraph. Claim under test: the
write-time intern table (`compute_node_identity`) is the correct read-time
resolver, where generic vector-RAG is not.

This ADR **partly confirms and partly refutes** that claim, honestly.

## Method

`scripts/agentos/subgraph_retrieval_bench.py`. FinBench Company/Person entities
that own ≥1 Account; ground-truth subgraph = the accounts an entity owns
(`Account.owner_id`), so a wrong anchor ⇒ wholly wrong owned-account set. Query
mentions carry surface variation in two families (normalizable: case/whitespace;
suffix: legal suffix) and half the entities are planted homonyms (shared surface
name, distinct disambiguator: Company→sector, Person→country). Five arms with
ceiling+floor controls (seocho-02t discipline — report the effect against the
null spread): `floor_random`, `vector_name` (bge NN over name), `vector_disamb`
(bge NN over "name | disambiguator"), `intern` (composite-key lookup, the
method), `ceiling_oracle`. bge = BAAI/bge-small (`fastembed_backend`). Primary
metric: wrong-anchor rate; downstream: subgraph precision/recall.

## Result — scale-invariant across Company SF1, Person SF1, Company SF10

wrong-anchor rate by condition (Company SF10 shown; the other two match within
~1pt):

| arm | normalizable/unique | normalizable/homonym | suffix/unique | suffix/homonym |
|---|---|---|---|---|
| floor_random | 81% | 76% | 81% | 76% |
| **vector_name** (naive) | 0% | **50%** | 0% | **50%** |
| **vector_disamb** (strong) | 0% | 0% | 0% | 0% |
| **intern** (method) | **0%** | **0%** | **100%** | **100%** |
| ceiling_oracle | 0% | 0% | 0% | 0% |

## Reading it honestly

1. **CONFIRMED — naive vector-RAG collides on homonyms.** `vector_name` (embed
   the entity name, the common graph-RAG practice) is wrong **50%** of the time
   on homonym queries at every scale — half its retrieved subgraphs are the
   wrong entity's, silently. This is exactly the boundary-1 precision failure
   the spine predicts, and it is the strongest result here.

2. **CONFIRMED — intern gives GUARANTEED homonym precision.** On the surface
   forms its normalizer covers (case/whitespace), intern is **0% wrong on
   homonyms**, by construction: distinct composite keys → distinct addresses.
   Not empirically-usually-right — exact and auditable (a key match, not a
   similarity threshold).

3. **REFUTED (overclaim) — vector is NOT structurally incapable.** A
   disambiguator-aware vector (`vector_disamb`) resolves at ~0% error across all
   conditions on this data. So "vector structurally cannot do boundary 1" was
   too strong; given the disambiguating context, bge separates the homonyms.
   **Caveat:** the entity names here are clean synthetic tokens; on noisy real
   names with subtle disambiguators, embedding separation degrades, whereas the
   composite-key match does not. The honest distinction that survives real data
   is *guaranteed vs empirical* precision, and cost/auditability (below) — not
   capability.

4. **HONEST WEAKNESS — intern has a normalizer recall ceiling.** intern misses
   **100%** of suffix variants ("company-5 Inc"): the normalizer folds case and
   whitespace but not legal suffixes, so the lookup finds nothing and the
   subgraph is empty. This is a normalizer-coverage gap (closeable by suffix
   normalization / alias / `same_as`), not a capability limit — but it is real
   and dominates intern's aggregate (ALL = 40% wrong = the 2-of-5 suffix
   variants). Vector handles suffix forms gracefully (bge embeds "…Inc" near
   the name).

## What actually distinguishes intern (the defensible claim)

Not "vector can't" — it's that intern gives **guaranteed, exact, auditable,
model-free** resolution: an O(1) hashtable lookup with 0% homonym error by
construction, no embedding model, no ANN over the entity set, and a resolution
that can be shown (key match) for the provenance/governance story SEOCHO sells.
Its cost is normalizer recall coverage. Vector gives surface robustness but only
*empirical* precision and needs an embedding+ANN pass.

## Consequence — the design is a hybrid, and the paper says so

Neither arm dominates: intern owns precision + cost + auditability with a recall
ceiling; vector owns surface robustness with a homonym-precision risk (severe
when name-only). The recommendation is **intern-first with a vector fallback**
for forms the normalizer misses — capturing intern's guaranteed 0% homonym error
*and* vector's suffix robustness. This is a stronger, more credible paper claim
than "we beat vector," and it is what boundary 1 should ship as. Follow-ups:
suffix/alias normalization to lift intern recall; measure the hybrid; stress
`vector_disamb` on noisier names where its empirical precision should crack.
