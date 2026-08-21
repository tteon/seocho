# DataHub interchange — review a SEOCHO ontology in DataHub, absorb the review

SEOCHO authors and grades ontologies; DataHub renders a glossary tree, search,
and a place for **non-developers to review**. This example runs the full
round-trip offline so you can see the loop before standing up any
infrastructure:

> SEOCHO drafts an ontology → exports it to a DataHub Business Glossary →
> a domain expert edits definitions / proposes terms in the DataHub UI →
> SEOCHO pulls the approved edits back and versions the governed result.

## Run it

Offline (no Docker, no DataHub — the review step is simulated):

```bash
python examples/datahub/roundtrip_demo.py
```

Against a live DataHub (emits the glossary for real in STAGE 2):

```bash
pip install "seocho[datahub]"     # installs the acryl-datahub SDK + CLI
datahub docker quickstart         # brings up the DataHub UI (Docker required)
GMS=http://localhost:8080 python examples/datahub/roundtrip_demo.py
```

`pip install seocho[datahub]` installs the **SDK and CLI** only. The DataHub
**UI, metadata service, and storage run as Docker containers** started by
`datahub docker quickstart` (a multi-service stack — budget ~8 GB RAM). SEOCHO's
own export / pull / apply need no Docker; only viewing the glossary in a browser
does.

## The seven stages

| Stage | What happens | Code |
|------|--------------|------|
| 0 | A governed ontology (here a tiny `pets` seed with a deliberately blank `Animal` definition) | `Ontology(...)` |
| 1 | Ontology → DataHub glossary MCPs (class → glossaryTerm, `broader` → Is-A; deterministic URNs) | `ontology_to_glossary_mcps` |
| 2 | Emit to a live GMS (idempotent UPSERT by URN) | `emit_to_datahub` |
| 3 | A reviewer edits definitions / proposes terms in the DataHub UI | *(simulated)* |
| 4 | Pull the reviewed terms back as normalized records | *(live GraphQL reader is a follow-up; simulated here)* |
| 5 | Reviewed terms → SEOCHO mapping-spec | `datahub_glossary_to_mapping_spec` |
| 6 | Apply → a new draft ontology (minor version bump) | `apply_mapping_spec` |
| 7 | Re-export **without clobbering** the human's edited text | `ontology_to_glossary_mcps(..., preserve_definitions=)` |

## Two mechanics worth understanding

**`annotate` carries an edit back onto an existing class.** A reviewer filling
in a blank definition is editing a class that already exists — `new_class`
would wrongly create a duplicate. The `annotate` action updates the
`description` (and optionally adds an alias) on the resolved class, and errors
if the class is not found. In the demo it fills the blank `Animal` definition
and rewrites `Breed`, while a genuinely new `Shetland pony` term still comes in
as `new_class` and an unapproved `Habitat` term is correctly left out.

**Definition ownership, so re-export is safe.** A term's definition lives in the
`glossaryTermInfo` aspect, which SEOCHO UPSERTs on every export — so a naive
re-export would overwrite the reviewer's text. Once a definition is
human-owned, pass that class label to `preserve_definitions=`: SEOCHO then skips
the `glossaryTermInfo` aspect for that term (the taxonomy / Is-A edges stay
SEOCHO-owned and are always emitted). Aspects are atomic, so definition and
custom properties cannot be updated independently — definition ownership wins.

## Boundary discipline (ADR-0222)

DataHub is a **serialization target, not SEOCHO's internal model**. `urn:li:*`
strings and DataHub aspect names live only inside `seocho.datahub_export` (and
the connector); everything else in this example speaks SEOCHO's own `Ontology`,
mapping-spec, and term-record contracts. That keeps the core stable if the
review surface is ever swapped.

Tracking: epic `seocho-v6w` (`annotate` = `seocho-v6w.9`; live GraphQL pull =
`seocho-v6w.3`; snapshot + runtime re-registration = `seocho-v6w.4`).
