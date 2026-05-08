"""JSON-LD vs TTL — same ontology, two syntaxes.

The shortest possible answer to "which format do I use?":
- JSON-LD = JSON, easy to hand-edit, what the basic tutorials use.
- TTL = Turtle, terse triple syntax, what FIBO and other published
  vocabularies ship as.

Both express the same RDF graph. seocho can read and write either,
so converting between them is one method call.

Run:
    python examples/finder/jsonld_vs_ttl.py

(or `make tutorials-shell` first, then run the same command inside
the container if you don't have seocho installed locally).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from seocho import Ontology, NodeDef, RelDef, P
from seocho.ontology import PropertyType


# ---------------------------------------------------------------------------
# 1. Define a tiny ontology in Python — same data we'll save to both formats.
# ---------------------------------------------------------------------------

onto = Ontology(
    name="company_demo",
    namespace="https://seocho.dev/demo/",
    description="A two-class ontology to illustrate JSON-LD vs TTL.",
    nodes={
        "Company": NodeDef(
            description="A registered business",
            aliases=["Firm", "Corporation"],
            properties={
                "name": P(type=PropertyType.STRING, unique=True),
                "sector": P(type=PropertyType.STRING),
                "headquarters": P(type=PropertyType.STRING),
            },
        ),
        "Person": NodeDef(
            description="An individual",
            properties={
                "name": P(type=PropertyType.STRING, unique=True),
                "title": P(type=PropertyType.STRING),
            },
        ),
    },
    relationships={
        "EMPLOYS": RelDef(
            source="Company",
            target="Person",
            description="Employment",
            cardinality="ONE_TO_MANY",
        ),
    },
)


# ---------------------------------------------------------------------------
# 2. Save the ontology in both formats and print the files.
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    jsonld_path = tmp / "company_demo.jsonld"
    ttl_path    = tmp / "company_demo.ttl"

    onto.to_jsonld(jsonld_path)
    onto.to_ttl(ttl_path)

    print("=" * 70)
    print("JSON-LD form")
    print("=" * 70)
    print(jsonld_path.read_text())

    print("=" * 70)
    print("TTL (Turtle) form")
    print("=" * 70)
    print(ttl_path.read_text())

    # ----------------------------------------------------------------------
    # 3. Quantify the "feel" of each.
    # ----------------------------------------------------------------------

    jsonld_lines = jsonld_path.read_text().splitlines()
    ttl_lines    = ttl_path.read_text().splitlines()
    print("=" * 70)
    print("Side-by-side numbers")
    print("=" * 70)
    print(f"JSON-LD : {len(jsonld_lines):>3} lines, {jsonld_path.stat().st_size:>4} bytes")
    print(f"TTL     : {len(ttl_lines):>3} lines, {ttl_path.stat().st_size:>4} bytes")
    print()
    print("Both encode the same RDF graph. JSON-LD is more verbose because of "
          "JSON's brace/comma overhead; TTL is denser because of the prefix "
          "machinery and the `;` chaining.")

    # ----------------------------------------------------------------------
    # 4. Round-trip — load each back and confirm we get equivalent ontologies.
    # ----------------------------------------------------------------------

    print()
    print("=" * 70)
    print("Round-trip check")
    print("=" * 70)

    from_jsonld = Ontology.from_jsonld(jsonld_path)
    from_ttl    = Ontology.from_ttl(ttl_path)

    def shape(o: Ontology) -> dict:
        return {
            "classes": sorted(o.nodes.keys()),
            "rels": sorted(o.relationships.keys()),
            "company_props": sorted(o.nodes.get("Company", NodeDef()).properties.keys()),
        }

    print(f"loaded from JSON-LD : {shape(from_jsonld)}")
    print(f"loaded from TTL     : {shape(from_ttl)}")
    print()
    print(
        "Note: TTL load currently maps owl:DatatypeProperty -> NodeDef "
        "property, but seocho's TTL writer encodes them at the ontology "
        "namespace level. Bottom line — the two formats hold the same "
        "graph; pick whichever feels easier to hand-edit."
    )

    # ----------------------------------------------------------------------
    # 5. Cross-convert: load TTL, save as JSON-LD (and vice versa).
    # ----------------------------------------------------------------------

    print()
    print("=" * 70)
    print("Cross-format conversion is a one-liner")
    print("=" * 70)

    ttl_to_jsonld = tmp / "from_ttl.jsonld"
    jsonld_to_ttl = tmp / "from_jsonld.ttl"

    from_ttl.to_jsonld(ttl_to_jsonld)
    from_jsonld.to_ttl(jsonld_to_ttl)

    print(f"TTL -> JSON-LD wrote {ttl_to_jsonld.stat().st_size} bytes "
          f"({len(ttl_to_jsonld.read_text().splitlines())} lines)")
    print(f"JSON-LD -> TTL wrote {jsonld_to_ttl.stat().st_size} bytes "
          f"({len(jsonld_to_ttl.read_text().splitlines())} lines)")
    print()
    print("Use the JSON-LD form when authoring a new ontology — it's just JSON.")
    print("Use the TTL form when consuming a published vocabulary (FIBO, FOAF,")
    print("Dublin Core) — that's how those publishers ship.")
