"""$0 regression tests for the hq-42k federation-agent layer."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parents[1]
ROOT = MDM_ROOT.parents[1]
for path in (MDM_ROOT, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agents.contracts import (  # noqa: E402
    ABSTAIN_MARK,
    FederationRequest,
    Provenance,
    ProviderFact,
    ProviderResponse,
)
from agents.federation_agent import FederationAgent, route_deterministic  # noqa: E402
from lib.survivorship import load_ruleset  # noqa: E402


class _Provider:
    def __init__(self, response: ProviderResponse):
        self.provider_id = response.provider_id
        self.model = response.provenance.model
        self.instance = type("Instance", (), {"uri": response.provenance.src_instance})()
        self._response = response

    def answer(self, query: str, case_id: str, *, answer_system: str, retrieve_only: bool):
        assert retrieve_only is True
        assert query
        assert case_id
        assert answer_system
        return self._response


def _response(
    provider_id: str,
    *,
    facts: tuple[ProviderFact, ...] = (),
    abstain: bool = False,
    error: str = "",
) -> ProviderResponse:
    return ProviderResponse(
        provider_id=provider_id,
        query="What was revenue in 2023?",
        case_id="case-1",
        abstain=abstain,
        context="" if abstain else f"=== {provider_id} ===\nRevenue facts",
        facts=facts,
        answer=ABSTAIN_MARK if abstain else None,
        confidence=0.0 if abstain else 1.0,
        provenance=Provenance(
            provider_id=provider_id,
            src_instance=f"bolt://{provider_id}",
            model=f"{provider_id}-model",
            workspace_id=f"fedcat-{provider_id}-case-1",
            retrieved_node_count=3,
        ),
        retrieval_ms=1.0,
        answer_ms=0.0,
        error=error,
    )


def _agent(*responses: ProviderResponse) -> FederationAgent:
    return FederationAgent(
        providers=[_Provider(r) for r in responses],
        ruleset=load_ruleset(),
        synth_client=object(),
        synth_spec=type("Spec", (), {"model": "unused"})(),
        answer_system="answer from context",
    )


def test_route_deterministic_sends_reference_figures_to_survivorship() -> None:
    assert route_deterministic("What was revenue in FY2023?") == "reference"
    assert route_deterministic("Explain revenue drivers in FY2023") == "narrative"


def test_reference_merge_counts_abstaining_provider_in_panel_confidence() -> None:
    fact_a = ProviderFact("Revenue", "FY2023", "GAAP", "$242.3 billion", "1")
    fact_b = ProviderFact("Revenue", "FY2023", "GAAP", "$242,300 million", "2")
    agent = _agent(
        _response("deepseek", facts=(fact_a,)),
        _response("gptoss", facts=(fact_b,)),
        _response("minimax25", abstain=True),
    )

    result = agent.answer(
        FederationRequest(
            query="What was revenue in FY2023?",
            case_id="case-1",
            slice_tag="CAT_FINA",
            category="Financials",
        )
    )

    assert result.route == "reference"
    assert result.degraded is True
    assert result.providers_attempted == 3
    assert result.providers_answered == 2
    assert result.unavailable == ({"provider": "minimax25", "reason": "abstain"},)
    assert result.survived is not None
    assert result.survived["golden"][0]["agreement"] == "2/2"
    assert result.survived["golden"][0]["confidence"] == 0.667


def test_reference_merge_quarantines_disagreement_without_silent_pick() -> None:
    agent = _agent(
        _response("deepseek", facts=(ProviderFact("EPS", "2023", "GAAP", "$1.00", "1"),)),
        _response("gptoss", facts=(ProviderFact("EPS", "2023", "GAAP", "$2.00", "2"),)),
    )

    result = agent.answer(
        FederationRequest(
            query="What was EPS in 2023?",
            case_id="case-1",
            slice_tag="CAT_FINA",
            category="Financials",
        )
    )

    assert result.survived is not None
    assert result.survived["golden"] == []
    assert result.survived["quarantined"][0]["reason"] == "tied_groups"
    assert "UNRESOLVED" in result.answer


def test_analysis_reports_provider_failure_degradation_curve() -> None:
    spec = importlib.util.spec_from_file_location(
        "fed_agents_runner", MDM_ROOT / "12_federation_agents.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    records = [
        {
            "lane": "silo-a",
            "case_id": "c1",
            "category": "Financials",
            "abstain": False,
            "context_chars": 10,
            "evaluation": {"number_overlap_ratio": 0.0, "token_f1": 0.2},
        },
        {
            "lane": "silo-b",
            "case_id": "c1",
            "category": "Financials",
            "abstain": False,
            "context_chars": 10,
            "evaluation": {"number_overlap_ratio": 0.0, "token_f1": 0.8},
        },
        {
            "lane": "federation",
            "case_id": "c1",
            "category": "Financials",
            "abstain": False,
            "context_chars": 20,
            "evaluation": {"number_overlap_ratio": 0.0, "token_f1": 0.7},
        },
    ]

    summary = module._analyze(records, [{"category": "Financials"}], ["a", "b"])

    assert summary["fanout_frontier"] == {
        "best_single_oracle": 0.8,
        "federation": 0.7,
    }
    assert summary["partial_failure_degradation"] == {
        "providers_2": 0.8,
        "providers_1": 0.5,
    }
