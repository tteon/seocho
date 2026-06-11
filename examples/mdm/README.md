# MDM Demo — Multi-LLM Department Graphs on DozerDB Multi-Database

Master Data Management at laptop scale: three departments of a fictional bank
extract knowledge graphs from the **same** FinDER 10-K filings with **different
LLMs**, land them in separate DozerDB databases, then consolidate them into a
golden-record master with full provenance — using composite-database federation
when available and GDS for cross-database entity resolution.

## The problem (why this is realistic)

Financial reference-data fragmentation is the canonical MDM story: Lehman 2008
(7,000+ legal entities; counterparties could not aggregate their exposure
because "Lehman Brothers" was an inconsistent string across thousands of
systems) → LEI/GLEIF → BCBS 239 risk-data aggregation rules → OpenFIGI for the
CUSIP/ISIN/SEDOL/ticker mapping mess. The 2026 twist: the silos are no longer
only human-made. Departments run their own LLMs over the same filings, and
**different models produce materially different graphs** — same 10-K, three
models, three incompatible entity masters. MDM must now reconcile
*model-extracted* facts, with per-model provenance.

## Scenario: Seocho Capital

| Department | Model (MARA gateway) | Database | Why they chose it |
|---|---|---|---|
| Risk | `DeepSeek-V3.1` | `mdmrisk` | cheapest per-token; nightly batch |
| Research | `gpt-oss-120b` | `mdmresearch` | self-hosted for IP protection |
| Compliance | `MiniMax-M2.5` | `mdmcompliance` | vendor-approved, audit logging |

Everything except the model is held constant: same FinDER cases, same FIBO
`medium` ontology, same vendor-neutral extraction prompt, same chunking, same
store. **The model is the only moving part.**

The inciting incident: the CRO asks *"what is our consolidated view of
Company X?"* and gets three conflicting answers, none traceable to why they
differ. The resolution: federate → resolve → golden records with lineage.
Department databases stay sovereign (never mutated) — MDM that requires
departments to abandon their systems never ships.

## Architecture

```
mdmrisk / mdmresearch / mdmcompliance    ← dept DBs (write once, then READ-ONLY)
        │  federated read:
        │    mode=composite → CALL () { USE mdmcomp.risk … UNION ALL … }
        │    mode=fanout    → 3 driver sessions, client-side union (primary)
        ▼
mdmstaging   ← EntityProxy + SAME_AS_CAND candidate edges; GDS WCC runs here
        ▼
mdmmaster    ← GoldenEntity + GoldenFact + SourceRef provenance + StewardTask queue
```

DozerDB 5.26 ships multi-database but lists fabric/composite as a v2.0+
roadmap item (its kernel self-reports "enterprise", so it's worth a smoke
test). `00_preflight.py` decides the mode at runtime and persists it; every
later step branches on it. GDS write modes are unsupported on composite
databases anyway, so golden records are always streamed and written through a
normal driver session — the same code path in both modes.

## MDM terminology map (for practitioners)

| Classical MDM | This demo |
|---|---|
| Source system | department DB (`mdmrisk` …) |
| Landing/staging area | `mdmstaging` (+ composite DB when supported) |
| Match engine / match rules | normalized-key + token-prefix + local-BGE embedding candidates → GDS WCC |
| Cross-reference (XREF) table | `SAME_AS` edges + `DERIVED_FROM` provenance edges |
| Golden record | `(:GoldenEntity)` / `(:GoldenFact)` in `mdmmaster` |
| Survivorship rules | versioned `config/survivorship.yaml` (sha-locked) |
| Data-steward work queue | `(:StewardTask {status:"open"})` nodes |

Hub style: **consolidation hub with registry-grade lineage** — golden records
materialized separately, all source records retained and linked.

## Survivorship for LLM-extracted data (the novel part)

Recency and source-trust rules are meaningless when all three sources read the
same 10-K at the same time. The rule family is **majority with abstention**:

- numeric values are normalized to base units; `$242.3B` vs `$242,290 million`
  is *rounding* (0.5% relative tolerance), not disagreement
- ≥2 models agree → golden value = the **least-rounded** member of the winning
  group; dissents retained on provenance
- no majority (1-vs-1, three-way split) → **QUARANTINE**: no golden value, an
  open `StewardTask` instead. A golden record that silently picks one of three
  conflicting revenue numbers is worse than no golden record (CLAUDE.md §20.2)
- missing is not a vote against; confidence = agreement / panel size
- rules are versioned and sha-locked: editing `survivorship.yaml` without
  bumping `rule_set_version` fails the run (§20.7 reproducibility)

## Money questions the consolidated graph answers (and silos cannot)

1. **Entity duplication census** — N department nodes → M resolved entities
2. **The CRO question** — one consolidated revenue figure with the three
   source values, variance, and full lineage
3. **Conflict detection** — every (company, metric, period) where models
   disagree, ranked by relative disagreement
4. **Coverage asymmetry** — what did only one model find? what did all three?
5. **Provenance audit** — golden fact → models → survivorship rule applied
6. **Model reliability** — per model: % of facts corroborated by another model
7. **Alias resolution** — name variant → golden entity → all department aliases
8. **Steward queue** — open quarantines by reason (shown as a feature, not hidden)

## Run

```bash
# 0. one-time: OpenGDS plugin + allowlist (restarts the DozerDB container)
bash examples/mdm/01_install_gds.sh

# 1. environment + mode decision (writes outputs/mode.json)
python examples/mdm/00_preflight.py

# 2. build the three department graphs (THE ONLY PAID STEP — MARA API calls;
#    resume-safe: re-run the same command to continue after an interruption)
python examples/mdm/02_extract_departments.py            # add --dry-run first

# 3..6 consolidation pipeline ($0, deterministic)
python examples/mdm/03_federate_and_stage.py
python examples/mdm/04_resolve_gds.py
python examples/mdm/05_write_master.py
python examples/mdm/06_showcase.py

# optional cleanup (never touches dept DBs or mdmmaster)
python examples/mdm/99_teardown.py
```

Unit tests (no DB, no API): `python -m pytest examples/mdm/tests/ -q`

## Multi-instance federation (medallion architecture) — phase 2

DozerDB 5.26 has no composite databases, but the thing composite databases
*are* — separate physical stores federated at query time, with analytics on a
designated GDS host — can be built directly. This phase moves each department
to its **own DozerDB instance** and measures whether federation + MDM
consolidation is worth it, on numbers:

```
BRONZE   dozer-risk :7688 · dozer-research :7689 · dozer-compliance :7690
         (one physical DBMS per department; no GDS on shards — exactly the
          Neo4j composite+GDS docs topology, built manually)
   │  lib/federation.py instances_read(): one driver per shard, client union
   ▼
SILVER   mdmstaging on the main instance (the "designated secondary"):
         EntityProxy + SAME_AS_CAND → OpenGDS WCC entity resolution
   ▼
GOLD     mdmmaster: GoldenEntity/GoldenFact + per-INSTANCE provenance
         (every SourceRef carries its shard's bolt URI) + StewardTask queue
```

```bash
bash   examples/mdm/07_up_instances.sh          # bronze tier up (3 containers)
python examples/mdm/08_migrate_to_instances.py  # $0 bolt-to-bolt migration
python examples/mdm/03_federate_and_stage.py --instances   # silver from shards
python examples/mdm/04_resolve_gds.py
python examples/mdm/05_write_master.py          # gold with instance provenance
python examples/mdm/09_federated_benchmark.py   # PAID: 60 MARA calls
```

### The benchmark (multi-agent, pre-registered)

Five "agents" answer the same 12 FinDER questions through the same LLM, same
prompt, same metric — only the retrieval context differs:

| Agent lane | Sees |
|---|---|
| `silo-risk` / `silo-research` / `silo-compliance` | its own instance only |
| `federation` | live fan-out union of all 3 instances per query (no MDM) |
| `gold` | consolidated golden records + confidence + provenance + quarantines |

Pre-registered hypotheses (verdicts reported even when rejected, §20.4):
**H-FED1** gold ≥ best silo on answer quality; **H-FED2** gold/federation
abstain less than every silo (union coverage); **H-FED3** federation ≈ gold on
quality but pays per-query costs (3 bolt round-trips, larger conflict-bearing
context) that consolidation amortizes — the medallion argument. Results land
in `outputs/evaluation/mdm_demo/<run>/benchmark_aggregate.json`.

### Measured results (run seocho-capital-v1, 2026-06-12, mara/DeepSeek-V3.1)

All three pre-registered hypotheses were **REJECTED** as stated — and the
per-slice breakdown is the actual finding:

| lane | overlap | abstain | retrieval ms | context chars |
|---|---|---|---|---|
| federation | **0.332** | **0.42** | 42.9 | 14,350 |
| silo-compliance | 0.331 | **0.42** | 31.2 | 8,160 |
| gold | 0.113 | 0.67 | **0.2** | **3,539** |
| silo-research | 0.110 | 0.75 | 27.4 | 3,125 |
| silo-risk | 0.098 | 0.75 | 19.6 | 2,950 |

1. **Federation's measured value = silo-blindness insurance.** It ties the
   best silo (which silo is best depends on which model the department
   happened to pick — unknowable a priori) and is never the worst, for
   +43 ms retrieval and ~4× context tokens per query.
2. **Gold's value is scoped to reference-data questions.** On the numeric
   slice S1 gold reaches 0.301 ≈ federation 0.326 with the LOWEST abstain
   (0.25 vs every silo's 0.50) at 1/4 the tokens and ~0 ms retrieval. On the
   narrative slice S2 gold scores 0.00 — golden records consolidate *numeric
   facts only*, by design; they are a reference-data product, not a general
   QA context. Using the master alone for prose questions conflates two jobs.
3. **Extraction recall still gates everything**: silo-compliance ≈ federation
   because MiniMax extracted ~13× the value-bearing facts of the other
   models' departments (consistent with the generator-dependence finding).

Interpretation (labeled as such): the medallion answer is **routing, not
replacement** — reference-data lookups go to gold (cheap, fast, provenanced),
narrative/multi-document questions go to federation. A question-type router
over these lanes is the natural follow-up experiment.

## Honesty contract (§20)

- every dashboard number traces to a JSONL/JSON artifact under
  `outputs/evaluation/mdm_demo/`
- extraction failures are recorded per (case, department), never imputed
- at least one quarantine case is *expected* and shown as the escalation
  queue working — an empty steward queue would be a red flag, not a success
- department DBs are verified unmutated (node counts before/after pipeline)
