from __future__ import annotations

from pathlib import Path

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology_governance import (
    conformance_score,
    load_competency_questions,
)
from seocho.ontology_resync import resync_ontology

_CQ_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples" / "finder" / "datasets" / "competency_questions.yaml"
)


def _arm(*, with_segments: bool = False) -> Ontology:
    nodes = {
        "LegalEntity": NodeDef(
            description="A registered business.",
            properties={"name": P(str, unique=True)},
            aliases=["Company"],
        ),
        "Revenue": NodeDef(description="Top-line revenue.", properties={"name": P(str, unique=True)}),
        "NetIncome": NodeDef(description="Bottom line.", properties={"name": P(str, unique=True)}),
        "OperatingIncome": NodeDef(description="Operating profit.", properties={"name": P(str, unique=True)}),
        "FinancialMetric": NodeDef(description="Abstract metric.", properties={"name": P(str, unique=True)}),
    }
    rels = {
        "REPORTED_METRIC": RelDef(source="LegalEntity", target="FinancialMetric", description="reported"),
    }
    if with_segments:
        nodes["BusinessSegment"] = NodeDef(description="Segment.", properties={"name": P(str, unique=True)})
        rels["HAS_SEGMENT"] = RelDef(source="LegalEntity", target="BusinessSegment", description="operates")
    return Ontology(name="arm", version="1.0.0", graph_model="lpg", nodes=nodes, relationships=rels)


# --- conformance_score (GRL Artefact 7) -------------------------------------

def test_conformance_score_clean_arm_passes() -> None:
    score = conformance_score(_arm(), run_reasoner=False)
    assert score["passed"] is True
    assert score["score"] >= 0.8
    assert score["components"]["structural_ok"] is True
    assert score["components"]["lint_error_count"] == 0
    # reasoner off -> consistency unknown, must NOT block
    assert score["components"]["consistency"] == "unknown"


def test_conformance_score_with_cqs_reflects_expressibility() -> None:
    cqs = load_competency_questions(_CQ_PATH)
    small = conformance_score(_arm(with_segments=False), competency_questions=cqs)
    medium = conformance_score(_arm(with_segments=True), competency_questions=cqs)
    # small arm cannot express segment CQs -> ratio < 1.0 and below medium
    assert small["components"]["cq_expressible_ratio"] < 1.0
    assert medium["components"]["cq_expressible_ratio"] >= small["components"]["cq_expressible_ratio"]


def test_conformance_score_hard_gate_blocks_on_lint_error() -> None:
    # 'X' used as BOTH a class and a relationship type -> lint ERROR
    broken = Ontology(
        name="broken",
        graph_model="lpg",
        nodes={"X": NodeDef(description="dup", properties={"name": P(str, unique=True)})},
        relationships={"X": RelDef(source="X", target="X", description="dup rel")},
    )
    score = conformance_score(broken)
    assert score["components"]["lint_error_count"] >= 1
    assert score["passed"] is False  # hard gate fails regardless of score


def test_conformance_score_rewards_shacl_messages() -> None:
    # the unique 'name' property yields a constrained shape carrying sh:message
    score = conformance_score(_arm())
    assert score["components"]["shacl_message_coverage"] == 1.0


# --- resync_ontology (fix-and-resync; GRL principles 3 & 4) ------------------

def test_resync_regenerates_artefacts_and_passes_for_clean_arm() -> None:
    report = resync_ontology(_arm(), workspace_id="ws-1")
    assert report["ok"] is True
    assert report["workspace_id"] == "ws-1"
    assert report["shacl"]["node_shape_count"] >= 1
    assert report["shacl"]["property_shape_count"] >= 1
    assert report["jsonld_present"] is True
    assert report["conformance"]["passed"] is True
    assert report["diff"] is None  # no prior supplied


def test_resync_surfaces_schema_impossible_cqs() -> None:
    cqs = load_competency_questions(_CQ_PATH)
    report = resync_ontology(_arm(with_segments=False), competency_questions=cqs)
    assert report["competency"]["schema_impossible_count"] >= 1
    assert any("structurally impossible" in n for n in report["notes"])


def test_resync_diffs_against_prior() -> None:
    prior = _arm(with_segments=False)
    current = _arm(with_segments=True)  # added BusinessSegment + HAS_SEGMENT
    report = resync_ontology(current, prior=prior)
    assert report["diff"] is not None
    assert report["diff"]["recommended_bump"] in {"major", "minor", "patch"}
