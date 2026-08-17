"""SEOCHO ⇄ DataHub interchange round-trip (offline, no Docker required).

The full loop the DataHub interchange track builds, run as ONE self-contained
script so the story is legible before any of it is wired into the CLI:

    STAGE 0  a governed ontology                    (here: hand-built seed)
    STAGE 1  ontology → DataHub glossary MCPs        (shipped: ontology_to_glossary_mcps)
    STAGE 2  emit to a live GMS                       (shipped: emit_to_datahub; Docker/live only)
    STAGE 3  a human reviews in the DataHub UI        (Docker required; here: simulated edit)
    STAGE 4  pull reviewed terms back                 (live GraphQL reader is a follow-up;
                                                        here: hand-built normalized term_records)
    STAGE 5  reviewed terms → mapping-spec            (shipped: datahub_glossary_to_mapping_spec)
    STAGE 6  apply → new draft ontology               (shipped: apply_mapping_spec)
    STAGE 7  re-export without clobbering human text   (shipped: preserve_definitions=)

What is real here: export, dry-run emit, the `annotate` round-trip that carries
a human-edited definition back onto an EXISTING class, apply, and a re-export
that preserves the human's text. What is simulated: the DataHub UI edit and the
live GraphQL pull (STAGE 3/4) — the normalized `term_records` are hand-built to
stand in for what a live reader will produce.

Run offline (no Docker, no GMS):   python examples/datahub/roundtrip_demo.py
Run against a live GMS (STAGE 2):   GMS=http://localhost:8080 python examples/datahub/roundtrip_demo.py
                                    (bring the UI up first with `datahub docker quickstart`)

Boundary discipline (ADR-0214): `urn:li:*` / aspect shapes stay inside
`datahub_export`; everything else speaks SEOCHO's own Ontology / mapping-spec /
term_records contracts.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Run from a repo checkout without `pip install`: put src/ on the path. A real
# `pip install seocho[datahub]` makes this a no-op.
_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from seocho.datahub_export import (
    datahub_glossary_to_mapping_spec,
    emit_to_datahub,
    export_summary,
    ontology_to_glossary_mcps,
)
from seocho.ontology import NodeDef, Ontology, P
from seocho.ontology_ambiguity import apply_mapping_spec

GMS = os.environ.get("GMS")  # set to emit live in STAGE 2


def build_seed_ontology() -> Ontology:
    """STAGE 0 — the ontology to interchange. In a real run this is the output
    of `seocho run` indexing or an LLM ontology proposer; inlined so the
    round-trip is the subject. `Animal.description` is deliberately blank — a
    reviewer will author it in DataHub."""
    return Ontology(
        name="pets",
        package_id="demo.pets",
        version="0.1.0",
        description="Seed ontology to be reviewed in DataHub.",
        nodes={
            "Animal": NodeDef(
                description="",  # blank on purpose — filled by the reviewer
                properties={"name": P(str, unique=True), "species": P(str)},
                identity_keys=["name"],
            ),
            "Breed": NodeDef(
                description="A breed of an animal.",
                properties={"name": P(str, unique=True)},
                identity_keys=["name"],
                broader=["Animal"],
            ),
        },
    )


def simulate_human_review() -> list[dict]:
    """STAGE 3+4 STAND-IN — what a live GraphQL pull will return after review.

    Shape matches `datahub_glossary_to_mapping_spec`'s contract: `name` plus
    `review_status` / `action` / `target` / `parent` / `description` (read from
    the term's aspects + tags). Simulates a reviewer who edited two existing
    definitions (`annotate`), proposed one new class (`new_class`), and left one
    term still under review (must NOT flow back)."""
    return [
        {"name": "Breed", "review_status": "APPROVED", "action": "annotate", "target": "Breed",
         "description": "A recognized variety within an animal species (e.g. Shetland pony)."},
        {"name": "Animal", "review_status": "APPROVED", "action": "annotate", "target": "Animal",
         "description": "A living creature that appears as a subject in the corpus."},
        {"name": "Shetland pony", "review_status": "APPROVED", "action": "new_class",
         "target": "ShetlandPony", "parent": "Breed",
         "description": "A small, hardy pony breed originating in the Shetland Isles."},
        {"name": "Habitat", "review_status": "PROPOSED", "action": "new_class", "target": "Habitat"},
    ]


def main() -> None:
    print("=== STAGE 0: seed ontology ===")
    onto = build_seed_ontology()
    print(f"  {onto.name} v{onto.version}: {len(onto.nodes)} classes "
          f"(Animal.description={onto.nodes['Animal'].description!r})")

    print("\n=== STAGE 1: ontology → DataHub glossary MCPs ===")
    mcps = ontology_to_glossary_mcps(onto)
    print("  summary:", json.dumps(export_summary(mcps)))

    print("\n=== STAGE 2: emit to GMS (live only) ===")
    res = emit_to_datahub(mcps, gms_server=GMS, token=os.environ.get("DATAHUB_TOKEN"),
                          dry_run=GMS is None)
    print(f"  mode={res['mode']} emitted={res['emitted']}"
          + (f" sent={res.get('sent')}" if res.get("sent") else "")
          + ("   (set GMS=... after `datahub docker quickstart` to emit live)"
             if GMS is None else ""))

    print("\n=== STAGE 3+4: human reviews in DataHub, pull back (SIMULATED) ===")
    term_records = simulate_human_review()
    for r in term_records:
        print(f"    {r['name']:14} status={r['review_status']:9} action={r['action']}")

    print("\n=== STAGE 5: reviewed terms → mapping-spec ===")
    spec = datahub_glossary_to_mapping_spec(term_records, only_status="APPROVED",
                                            ontology_name=onto.name)
    print("  mappings:", json.dumps(spec["mappings"], ensure_ascii=False))

    print("\n=== STAGE 6: apply → new draft ontology ===")
    new_onto = apply_mapping_spec(onto, spec)
    print(f"  {onto.version} → {new_onto.version}, {len(onto.nodes)} → {len(new_onto.nodes)} classes")
    print(f"  Animal.description now={new_onto.nodes['Animal'].description!r}")
    assert new_onto.nodes["Animal"].description, "annotate should have filled the blank definition"
    assert "ShetlandPony" in new_onto.nodes, "new_class should have added the proposed class"
    assert "Habitat" not in new_onto.nodes, "PROPOSED (unapproved) terms must not flow back"

    print("\n=== STAGE 7: re-export without clobbering the human's text ===")
    human_owned = [r["target"] for r in term_records
                   if r["action"] == "annotate" and r["review_status"] == "APPROVED"]
    mcps2 = ontology_to_glossary_mcps(new_onto, preserve_definitions=human_owned)
    from seocho.datahub_export import _term_urn  # boundary helper, example-only
    animal_info = [m for m in mcps2
                   if m["entityUrn"] == _term_urn(f"{new_onto.package_id}.Animal")
                   and m["aspectName"] == "glossaryTermInfo"]
    print(f"  preserved definitions for: {human_owned}")
    print(f"  glossaryTermInfo re-asserted for Animal? {bool(animal_info)}  "
          "(False = human definition is safe from re-export)")
    assert not animal_info, "preserved term must not have its definition re-asserted"

    print("\nDONE — SEOCHO drafts, a human reviews in DataHub, SEOCHO absorbs the review.")


if __name__ == "__main__":
    main()
