# ADR-0160: interning measurement — the identity table as a memory allocator

Date: 2026-08-15 · Status: accepted (measurement record)

## Context

The memory-allocator reframe (hadry 2026-08-15; obsidian
wiki/memory-allocator-model-design.md) reads SEOCHO's `identity_keys` →
composite-id MERGE as **hash-consing / string interning**, with the UNIQUE
constraint on the composite id as the intern table (the "ontology hash →
hashtable"). If that framing is load-bearing, the layer's defining metric is
not latency but the two properties every interning allocator lives or dies by:

- **Collapse** — do multiple mentions of the *same* canonical entity (surface
  variation across documents/agents) resolve to *one* address?
- **Collision** — do *distinct* canonical entities that share a member
  (homonyms) wrongly land on *one* address, corrupting the heap?

Nobody in the neighborhood (mem0/graphiti/cognee) reports these, because none
has a typed intern table to measure — a category-defining measurement, like
PORT-1.

## Method

`scripts/agentos/interning_probe.py` exercises the **real** identity function
`seocho.index.identity.compute_node_identity` (the one the write path calls —
not a reimplementation) over the FinBench entity population (Person, Company;
SF1 and SF10). Per canonical entity it injects deterministic surface variation
in two families, kept separate so collapse is measured honestly:

- **normalizable** (case, leading/trailing whitespace) — `_normalize_segment`
  should fold these onto the canonical address;
- **suffix** (appended legal suffix "Inc"/"Co."/…) — semantically the same
  entity but NOT normalized away: the intern table's real recall ceiling.

Planted homonyms force distinct canonicals to share a surface name while
differing in a disambiguating property (Person→country, Company→sector) — the
realistic collision (two people named X in different countries; PTC's vs
Tesla's "Total revenue"). Two key policies are compared: `name_only`
(`identity_keys=["name"]`, the naive allocator) vs `composite`
(`identity_keys=["name", <disambiguator>]`, seocho-uxs). Pure function, no DB /
no LLM → byte-deterministic; isolates key policy from storage.

## Result (SF1; SF10 identical in shape — scale-invariant)

| policy | norm. collapse | suffix recall | collision (planted homonyms) |
|---|---|---|---|
| `name_only` | 100% | 0% | **100%** (SF1 60/60, SF10 569/569) |
| `composite` | 100% | 0% | **0%** (SF1 0/60, SF10 0/569) |

- **Collision is the load-bearing result.** The naive name-only allocator
  aliases *every* planted homonym pair onto one address — silent heap
  corruption where the second write overwrites the first entity's values. The
  composite key separates *all* of them, at both scales. Address accounting
  makes the corruption concrete: at SF10 name_only produces 47,155 Person
  addresses vs composite's 50,000 — 2,845 addresses lost to wrongful merges.
- **Collapse of formatting noise works** (100% for case + whitespace): the
  normalizer folds three surface forms to one address.
- **Suffix recall is 0% — reported, not hidden.** Legal-suffix variants do not
  fold; this is the intern table's honest recall ceiling and the motivation for
  alias / `same_as` handling (a follow-up, not claimed here).

## Honest scope

The planted homonyms differ in the disambiguator by construction, so
`composite`'s 0% collision measures that the composite key **uses** available
disambiguating signal — not that it separates true duplicates. Homonyms
identical in *every* identity key are the same address by definition (the probe
skips those pairs and they are out of scope for a key policy). The claim is
therefore precise: *when a distinguishing property exists (the common homonym
case), the composite intern key exploits it to keep distinct entities at
distinct addresses; the name-only key does not.*

## Consequences

- Interning collapse/collision join PORT-1 as the paper's Tier-1 evidence that
  SEOCHO is a **typed interning allocator** for agent memory, not a metaphor:
  the intern table gives every canonical entity one address across all agents —
  a namespace no gateway/vector-store provides (ADR-0157 scope; seocho-gzo).
- Motivates: alias/`same_as` to lift suffix recall (follow-up); the sound
  isolation work (seocho-5zz) as the protection-domain half of the same model.
