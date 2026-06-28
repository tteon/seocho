"""Live MARA experiment: ontology refinement (OntoClean ensemble) + guardrail ablation.

Two phases, both recorded:

  Phase A — refine: a MARA *ensemble* (all available models, majority vote per
    meta-property) infers OntoClean meta-properties for a draft ontology that
    contains a planted formal is-a defect. The critic flags it; we fix the
    hierarchy; the scorecard measures the before/after lift.

  Phase B — guardrail payoff: the SAME documents are extracted by each MARA model
    twice — once with the DRAFT ontology injected as the extraction guardrail
    (arm A), once with the REFINED ontology (arm B). We measure how well each
    arm's extraction conforms to the refined target schema (label-in-ontology
    rate + score_extraction) and how consistent its labels are. Hypothesis:
    refined-guardrail (B) >= draft-guardrail (A).

Key is read from .env (var ontology_guardrail_mara_api_key). Records everything
to --out. Run:
    PYTHONPATH=src python3 scripts/benchmarks/ontology_guardrail_ablation.py --out <file.json>
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology_ontoclean import (
    MetaProperties,
    build_inference_prompt,
    check_ontoclean,
    dump_metaproperties,
    infer_metaproperties,
)
from seocho.ontology_scorecard import score_ontology
from seocho.store.llm import create_llm_backend

MODELS = ["DeepSeek-V3.1", "MiniMax-M2.5", "MiniMax-M2.7", "gpt-oss-120b"]

DOCS = [
    "Acme Corp announced that Jane Doe will become its new chief executive officer next month.",
    "Globex Inc acquired its smaller rival Initech in a deal worth two billion dollars.",
    "The startup Hooli launched a new cloud product called Nucleus aimed at enterprises.",
    "John Smith, a senior engineer at Hooli, was promoted to vice president of product.",
    "Initech reported quarterly revenue of 450 million dollars, up 12 percent year over year.",
    "Stark Industries offers a flagship product, the Arc Reactor, to industrial customers.",
]

CQS = [
    {
        "id": "cq1",
        "question": "Who is the CEO of a company?",
        "requires": ["Person", "CEO_OF", "Company"],
    },
    {
        "id": "cq2",
        "question": "What product does a company offer?",
        "requires": ["Company", "OFFERS", "Product"],
    },
]


def draft_ontology() -> Ontology:
    """A user's hand-written first draft with a PLANTED OntoClean defect: Person
    (rigid) is modelled as a subclass of Employee (an anti-rigid role). Also
    messy: missing definitions/identity, an untyped relationship."""
    return Ontology(
        "biz-draft",
        version="v1",
        nodes={
            "Employee": NodeDef(
                description="A person employed by a company."
            ),  # role, no identity
            "Person": NodeDef(
                broader=["Employee"], properties={"name": P(str)}
            ),  # VIOLATION + no def/identity
            "Company": NodeDef(properties={"name": P(str)}),  # no def, name not unique
            "Product": NodeDef(description="A product."),  # no identity
        },
        relationships={
            "CEO_OF": RelDef(source="Person", target="Any"),  # untyped
            "OFFERS": RelDef(source="Company", target="Product", description="offers"),
            "ACQUIRED": RelDef(
                source="Company", target="Company", description="acquired"
            ),
        },
    )


def refined_ontology() -> Ontology:
    """The governed rewrite: is-a fixed (Employee under Person), definitions,
    identity keys, typed endpoints, cardinality."""
    return Ontology(
        "biz-refined",
        version="1.1.0",
        nodes={
            "Person": NodeDef(
                description="A human being.",
                properties={"name": P(str, unique=True, description="Full name.")},
                identity_keys=["name"],
            ),
            "Employee": NodeDef(
                description="A person employed by a company (a role).",
                broader=["Person"],
                properties={"name": P(str, unique=True), "title": P(str)},
                identity_keys=["name"],
            ),
            "Company": NodeDef(
                description="A business organization.",
                properties={
                    "name": P(str, unique=True, description="Registered name.")
                },
                identity_keys=["name"],
            ),
            "Product": NodeDef(
                description="A product or service offered by a company.",
                properties={"name": P(str, unique=True)},
                identity_keys=["name"],
            ),
        },
        relationships={
            "CEO_OF": RelDef(
                source="Person",
                target="Company",
                cardinality="MANY_TO_ONE",
                description="Person leads the company as chief executive.",
            ),
            "OFFERS": RelDef(
                source="Company",
                target="Product",
                cardinality="ONE_TO_MANY",
                description="Company offers a product.",
            ),
            "ACQUIRED": RelDef(
                source="Company",
                target="Company",
                cardinality="MANY_TO_MANY",
                description="Company acquired another company.",
            ),
        },
    )


# --------------------------------------------------------------------------- #
# Phase A — ensemble OntoClean inference
# --------------------------------------------------------------------------- #

_META_KEYS = ["rigid", "carries_identity", "supplies_identity", "unity", "dependent"]


def ensemble_consensus(per_model_tags: dict, labels) -> dict:
    """Majority vote per (label, meta-property). Ties or all-unknown -> None."""
    consensus = {}
    for label in labels:
        md = {}
        for key in _META_KEYS:
            votes = [
                getattr(per_model_tags[m].get(label, MetaProperties()), key)
                for m in per_model_tags
                if label in per_model_tags[m]
            ]
            votes = [v for v in votes if v is not None]
            if not votes:
                md[key] = None
            else:
                c = Counter(votes)
                top, n = c.most_common(1)[0]
                md[key] = top if n > len(votes) / 2 else None
        consensus[label] = MetaProperties(**md)
    return consensus


def phase_a(key: str) -> dict:
    draft = draft_ontology()
    per_model = {}
    for m in MODELS:
        be = create_llm_backend(provider="mara", model=m, api_key=key)
        try:
            tags = infer_metaproperties(draft, backend=be)
            per_model[m] = tags
            print(f"[A] {m}: tagged {len(tags)} classes")
        except Exception as e:
            print(f"[A] {m}: FAILED {type(e).__name__}: {str(e)[:100]}")
    consensus = ensemble_consensus(per_model, list(draft.nodes))

    draft_oc = check_ontoclean(draft, consensus)
    refined = refined_ontology()
    refined_oc = check_ontoclean(refined, consensus)

    b = score_ontology(draft, competency_questions=CQS, ontoclean_tags=consensus)
    a = score_ontology(refined, competency_questions=CQS, ontoclean_tags=consensus)
    return {
        "models_succeeded": list(per_model),
        "per_model_tags": {m: dump_metaproperties(t) for m, t in per_model.items()},
        "consensus_tags": dump_metaproperties(consensus),
        "inference_prompt": build_inference_prompt(draft),
        "draft_ontoclean": draft_oc.to_dict(),
        "refined_ontoclean": refined_oc.to_dict(),
        "draft_score": {
            "overall": round(b.overall_score, 4),
            "grade": b.grade,
            "taxonomy_health": round(b.dimension("taxonomy_health").score, 4),
        },
        "refined_score": {
            "overall": round(a.overall_score, 4),
            "grade": a.grade,
            "taxonomy_health": round(a.dimension("taxonomy_health").score, 4),
        },
        "delta_overall": round(a.overall_score - b.overall_score, 4),
        "violations_cleared": len(draft_oc.violations) - len(refined_oc.violations),
    }


# --------------------------------------------------------------------------- #
# Phase B — guardrail extraction ablation
# --------------------------------------------------------------------------- #

_EXTRACT_SYSTEM = (
    "You extract a knowledge graph from text, STRICTLY conforming to the provided "
    "ontology. Use ONLY the listed entity labels and relationship types. Return ONLY JSON."
)


def extraction_prompt(onto: Ontology, doc: str) -> str:
    ctx = onto.to_extraction_context()
    return (
        f"ENTITY TYPES (use only these labels):\n{ctx.get('entity_types','')}\n\n"
        f"RELATIONSHIP TYPES (use only these):\n{ctx.get('relationship_types','')}\n\n"
        f"CONSTRAINTS:\n{ctx.get('constraints_summary','')}\n\n"
        f"TEXT:\n{doc}\n\n"
        'Return JSON: {"nodes":[{"id":"n1","label":"<Label>","properties":{"name":"..."}}],'
        '"relationships":[{"source":"n1","target":"n2","type":"<TYPE>"}]}'
    )


def _parse_graph(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        s = "\n".join(l for l in s.split("\n") if not l.strip().startswith("```"))
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        return json.loads(m.group(0)) if m else {"nodes": [], "relationships": []}


def _conformance(graph: dict, reference: Ontology) -> dict:
    nodes = [n for n in graph.get("nodes", []) if isinstance(n, dict)]
    rels = [r for r in graph.get("relationships", []) if isinstance(r, dict)]
    labels = [str(n.get("label", "")) for n in nodes]
    rtypes = [str(r.get("type", "")) for r in rels]
    label_ok = sum(1 for l in labels if l in reference.nodes)
    rtype_ok = sum(1 for t in rtypes if t in reference.relationships)
    sc = reference.score_extraction({"nodes": nodes, "relationships": rels})
    return {
        "node_count": len(nodes),
        "rel_count": len(rels),
        "distinct_labels": len(set(labels)),
        "label_conformance": round(label_ok / len(labels), 4) if labels else 0.0,
        "rel_conformance": round(rtype_ok / len(rtypes), 4) if rtypes else 0.0,
        "extraction_score": sc["overall"],
        "labels": labels,
    }


def phase_b(key: str) -> dict:
    reference = refined_ontology()  # common target schema for fair conformance scoring
    arms = {
        "A_draft_guardrail": draft_ontology(),
        "B_refined_guardrail": refined_ontology(),
    }
    results = {arm: {"per_model": {}} for arm in arms}

    for arm, onto in arms.items():
        for m in MODELS:
            be = create_llm_backend(provider="mara", model=m, api_key=key)
            per_doc = []
            for doc in DOCS:
                try:
                    r = be.complete(
                        system=_EXTRACT_SYSTEM,
                        user=extraction_prompt(onto, doc),
                        temperature=0.0,
                        max_tokens=4096,
                        response_format={"type": "json_object"},
                    )
                    graph = _parse_graph(r.text)
                    per_doc.append(_conformance(graph, reference))
                except Exception as e:
                    per_doc.append({"error": f"{type(e).__name__}: {str(e)[:80]}"})
            ok = [d for d in per_doc if "error" not in d]
            agg = {
                "docs_ok": len(ok),
                "mean_label_conformance": (
                    round(statistics.mean([d["label_conformance"] for d in ok]), 4)
                    if ok
                    else 0.0
                ),
                "mean_rel_conformance": (
                    round(statistics.mean([d["rel_conformance"] for d in ok]), 4)
                    if ok
                    else 0.0
                ),
                "mean_extraction_score": (
                    round(statistics.mean([d["extraction_score"] for d in ok]), 4)
                    if ok
                    else 0.0
                ),
                "total_distinct_labels": len(set(l for d in ok for l in d["labels"])),
            }
            results[arm]["per_model"][m] = {"aggregate": agg, "per_doc": per_doc}
            print(
                f"[B] {arm} / {m}: label_conf={agg['mean_label_conformance']} "
                f"score={agg['mean_extraction_score']} distinct={agg['total_distinct_labels']}"
            )

    # cross-model aggregate per arm
    summary = {}
    for arm in arms:
        ms = [
            results[arm]["per_model"][m]["aggregate"]
            for m in MODELS
            if m in results[arm]["per_model"]
        ]
        summary[arm] = {
            k: round(statistics.mean([a[k] for a in ms]), 4)
            for k in (
                "mean_label_conformance",
                "mean_rel_conformance",
                "mean_extraction_score",
                "total_distinct_labels",
            )
        }
    summary["delta_B_minus_A"] = {
        k: round(summary["B_refined_guardrail"][k] - summary["A_draft_guardrail"][k], 4)
        for k in summary["A_draft_guardrail"]
    }
    return {
        "documents": DOCS,
        "reference_schema": reference.name,
        "results": results,
        "summary": summary,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--phase", choices=["a", "b", "both"], default="both")
    args = ap.parse_args()

    env = Path(".env").read_text(encoding="utf-8")
    key = re.search(r'ontology_guardrail_mara_api_key\s*=\s*"([^"]+)"', env).group(1)

    record = {"experiment": "ontology-guardrail-ablation", "models": MODELS}
    if args.phase in ("a", "both"):
        print("=== Phase A: ensemble OntoClean refinement ===")
        record["phase_a"] = phase_a(key)
    if args.phase in ("b", "both"):
        print("=== Phase B: guardrail extraction ablation ===")
        record["phase_b"] = phase_b(key)

    Path(args.out).write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\n[written] {args.out}")
    if "phase_a" in record:
        pa = record["phase_a"]
        print(
            f"\nPhase A: draft {pa['draft_score']['grade']} ({pa['draft_score']['overall']}) "
            f"-> refined {pa['refined_score']['grade']} ({pa['refined_score']['overall']}), "
            f"violations cleared {pa['violations_cleared']}"
        )
    if "phase_b" in record:
        s = record["phase_b"]["summary"]
        print(
            f"Phase B (cross-model mean): A label_conf={s['A_draft_guardrail']['mean_label_conformance']} "
            f"score={s['A_draft_guardrail']['mean_extraction_score']} | "
            f"B label_conf={s['B_refined_guardrail']['mean_label_conformance']} "
            f"score={s['B_refined_guardrail']['mean_extraction_score']} | "
            f"Δscore={s['delta_B_minus_A']['mean_extraction_score']:+}"
        )


if __name__ == "__main__":
    main()
