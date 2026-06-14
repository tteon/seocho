"""Before/after experiment for the ontology quality scorecard.

Demonstrates that ``seocho.ontology_scorecard.score_ontology`` (a) detects
concrete defects in a real example ontology and (b) measurably rewards the
fixes it recommends. The "before" arm is the shipped quickstart schema; the
"after" arm applies exactly the weak points the scorecard surfaced.

Run:
    PYTHONPATH=src python3 scripts/benchmarks/ontology_scorecard_experiment.py

Writes a JSON record to ``--out`` (default: stdout only).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology_scorecard import score_ontology

CQS = [
    {"id": "cq1", "question": "Who is the CEO of a company?", "requires": ["Person", "CEO_OF", "Company"]},
    {"id": "cq2", "question": "What products does a company offer?", "requires": ["Company", "OFFERS", "Product"]},
    {"id": "cq3", "question": "Which company acquired another?", "requires": ["Company", "ACQUIRED"]},
]


def messy_first_draft() -> Ontology:
    """The 'before' arm — a realistic hand-written first-draft ontology, the kind
    a user produces before any governance: missing definitions, no identity keys,
    a flat structure, an orphan class, and an untyped relationship."""
    return Ontology(
        "quickstart-draft",
        version="v1",  # invalid semver
        nodes={
            "Company": NodeDef(properties={"name": P(str)}),  # no description, name not unique → no identity
            "Person": NodeDef(description="A person."),  # no identity
            "Founder": NodeDef(description="A founder."),  # no identity, single child of nothing
            "Product": NodeDef(description="A product."),  # no identity
            "Deal": NodeDef(description="A transaction."),  # no identity
            "Metric": NodeDef(description="A KPI."),  # orphan: no broader, no relationship
        },
        relationships={
            "CEO_OF": RelDef(source="Person", target="Any"),  # untyped target, no description
            "OFFERS": RelDef(source="Company", target="Product", description="offers"),
            "ACQUIRED": RelDef(source="Company", target="Company", description="acquired"),
        },
    )


def improved_ontology() -> Ontology:
    """The 'after' arm — the quickstart schema with the scorecard's recommended
    fixes applied: an is-a taxonomy root (Agent), declared cardinalities, and
    explicit identity keys."""
    return Ontology(
        "quickstart",
        version="1.1.0",
        description="Minimal company/person/product schema (taxonomy + cardinality hardened).",
        nodes={
            "Agent": NodeDef(description="An entity that can participate in business activity."),
            "Company": NodeDef(
                description="A business organization.",
                broader=["Agent"],
                properties={"name": P(str, unique=True, description="Registered company name.")},
                identity_keys=["name"],
            ),
            "Person": NodeDef(
                description="A person such as an executive or founder.",
                broader=["Agent"],
                properties={"name": P(str, unique=True, description="Full name.")},
                identity_keys=["name"],
            ),
            "Founder": NodeDef(
                description="A person who founded a company.",
                broader=["Person"],
                properties={"name": P(str, unique=True, description="Full name.")},
                identity_keys=["name"],
            ),
            "Product": NodeDef(
                description="A product or service offered by a company.",
                properties={"name": P(str, unique=True, description="Product name.")},
                identity_keys=["name"],
            ),
            "Deal": NodeDef(
                description="An acquisition or transaction between companies.",
                properties={"label": P(str, unique=True, description="Deal identifier.")},
                identity_keys=["label"],
            ),
            "Metric": NodeDef(
                description="A key performance indicator reported by a company.",
                properties={"name": P(str, unique=True, description="Metric name.")},
                identity_keys=["name"],
            ),
        },
        relationships={
            "CEO_OF": RelDef(source="Person", target="Company", cardinality="MANY_TO_ONE",
                             description="Person leads the company as chief executive."),
            "ACQUIRED": RelDef(source="Company", target="Company", cardinality="MANY_TO_MANY",
                               description="Company acquired another company."),
            "OFFERS": RelDef(source="Company", target="Product", cardinality="ONE_TO_MANY",
                             description="Company offers a product or service."),
            "REPORTS": RelDef(source="Company", target="Metric", cardinality="ONE_TO_MANY",
                              description="Company reports a performance metric."),
        },
    )


def _summarise(card) -> dict:
    return {
        "overall_score": round(card.overall_score, 4),
        "grade": card.grade,
        "blocking": card.blocking,
        "dimensions": {d.name: round(d.score, 4) for d in card.dimensions},
        "weak_point_count": len(card.weak_points),
        "weak_points": [wp.to_dict() for wp in card.weak_points],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    before_onto = messy_first_draft()
    after_onto = improved_ontology()

    before = score_ontology(before_onto, competency_questions=CQS)
    after = score_ontology(after_onto, competency_questions=CQS)

    record = {
        "experiment": "ontology-scorecard-before-after",
        "domain": "company/person/product first-draft → governed",
        "before": _summarise(before),
        "after": _summarise(after),
        "delta": {
            "overall_score": round(after.overall_score - before.overall_score, 4),
            "weak_point_count": after.weak_point_count if hasattr(after, "weak_point_count") else len(after.weak_points) - len(before.weak_points),
            "by_dimension": {
                d.name: round(
                    d.score - (before.dimension(d.name).score if before.dimension(d.name) else 0.0), 4
                )
                for d in after.dimensions
            },
        },
    }
    # weak_point_count delta computed plainly
    record["delta"]["weak_point_count"] = len(after.weak_points) - len(before.weak_points)

    text = json.dumps(record, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"\n[written] {args.out}")


if __name__ == "__main__":
    main()
