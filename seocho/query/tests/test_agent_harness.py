from seocho.query.agent_harness import AgentIdentity, EvidenceBoundary, RetrievalSubagent, evaluate_rubric
from seocho.query.sdcr import Evidence


def test_boundary_enforces_identity_and_protected_fields() -> None:
    identity = AgentIdentity("legal-agent", "w1", "legal", frozenset({"risk"}))
    evidence = [Evidence("ok", "legal", "risk", "high"), Evidence("other", "financials", "risk", "low"), Evidence("secret", "legal", "risk", "x", protected=True)]
    assert [item.source_id for item in EvidenceBoundary.authorize(identity, evidence)] == ["ok"]


def test_retrieval_loop_abstains_then_recovers() -> None:
    def retrieve(attempt, _slots):
        return [] if attempt == 1 else [Evidence("s", "legal", "risk", "high")]
    result = RetrievalSubagent(max_attempts=2).run(required_slots=["risk"], retrieve=retrieve)
    assert result["status"] == "sufficient"
    assert result["attempts"] == 2


def test_rubric_reports_boundary_metrics() -> None:
    evidence = [Evidence("s", "legal", "risk", "high", provenance={"uri": "x"})]
    result = evaluate_rubric(receipt={"required_slots": ["risk"], "missing_slots": [], "authorization_passed": True}, evidence=evidence, answer="high")
    assert result["slot_coverage"] == 1.0
    assert result["authorization_passed"] is True
