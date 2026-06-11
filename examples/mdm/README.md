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

## Honesty contract (§20)

- every dashboard number traces to a JSONL/JSON artifact under
  `outputs/evaluation/mdm_demo/`
- extraction failures are recorded per (case, department), never imputed
- at least one quarantine case is *expected* and shown as the escalation
  queue working — an empty steward queue would be a red flag, not a success
- department DBs are verified unmutated (node counts before/after pipeline)
