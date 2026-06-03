"""GOPTS F3 — Layer-3 answer-quality harness contract tests (ADR-0097).

Pins the three-way answer evaluation:
  1. token-F1 (precision + recall + harmonic mean)
  2. exact-match (normalized string equality)
  3. LLM-as-judge (injected callable, optional)
plus Hit@k / MRR evidence-retrieval scoring.

The judge dimension is exercised with a deterministic mock judge_fn so
the test stays hermetic — no model call. make_llm_judge_fn's prompt
wiring is tested against a fake backend.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from seocho.eval import gopts_answer_quality as aq
from seocho.eval.gopts_ranking import GoptsFixture, load_fixtures


# --- token-F1 ----------------------------------------------------------------


def test_token_f1_perfect_match_is_one() -> None:
    r = aq.token_f1("Apple revenue 365.8B", "Apple revenue 365.8B")
    assert r.f1 == 1.0
    assert r.precision == 1.0
    assert r.recall == 1.0


def test_token_f1_partial_overlap_between_zero_and_one() -> None:
    # expected has 3 meaningful tokens; actual repeats 2 of them + 2 noise.
    r = aq.token_f1("Apple revenue 2022", "Apple revenue grew sharply")
    assert 0.0 < r.f1 < 1.0
    # recall = 2/3 (apple, revenue found; 2022 missing)
    assert r.recall < 1.0
    # precision < 1.0 because "grew"/"sharply" aren't in expected
    assert r.precision < 1.0


def test_token_f1_precision_penalizes_wrong_company() -> None:
    """An answer that returns Apple's number for a Microsoft question
    should score low — this is the precision side the old recall-only
    score_answer_slots missed."""
    expected = "Microsoft revenue 2022 198.3B"
    wrong = "Apple revenue 2022 365.8B"
    r = aq.token_f1(expected, wrong)
    # overlap = {revenue, 2022}; both precision and recall are partial.
    assert r.f1 < 0.6


def test_token_f1_empty_actual_is_zero() -> None:
    r = aq.token_f1("Apple revenue", "")
    assert r.f1 == 0.0
    assert r.precision == 0.0
    assert r.recall == 0.0


def test_token_f1_is_harmonic_mean() -> None:
    r = aq.token_f1("alpha beta gamma delta", "alpha beta noise")
    # expected 4 tokens, actual 3 tokens, overlap = {alpha, beta} = 2
    # precision = 2/3, recall = 2/4 = 0.5
    # f1 = 2*p*r/(p+r) = 2*(0.6667)*(0.5)/(1.1667) = 0.5714
    assert abs(r.precision - 0.6667) < 0.01
    assert r.recall == 0.5
    assert abs(r.f1 - 0.5714) < 0.01


# --- exact-match -------------------------------------------------------------


def test_exact_match_normalizes_punctuation_and_case() -> None:
    assert aq.exact_match("Tim Cook manages Apple.", "tim cook manages apple") is True


def test_exact_match_false_on_extra_content() -> None:
    assert aq.exact_match("Apple", "Apple is a company") is False


# --- Hit@k / MRR -------------------------------------------------------------


def test_hit_at_k_true_when_expected_in_topk() -> None:
    assert aq.hit_at_k(["TSMC", "Apple", "Foxconn"], ["Apple"], k=2) == 1.0


def test_hit_at_k_false_when_expected_below_k() -> None:
    assert aq.hit_at_k(["TSMC", "Foxconn", "Apple"], ["Apple"], k=2) == 0.0


def test_hit_at_k_normalizes_entity_names() -> None:
    # casing/punctuation differences shouldn't sink a real hit
    assert aq.hit_at_k(["apple."], ["Apple"], k=1) == 1.0


def test_mrr_reciprocal_of_first_hit_rank() -> None:
    # Apple is the 2nd retrieved → reciprocal rank 1/2
    assert aq.mrr(["TSMC", "Apple", "Foxconn"], ["Apple"]) == 0.5


def test_mrr_zero_when_no_hit() -> None:
    assert aq.mrr(["TSMC", "Foxconn"], ["Apple"]) == 0.0


# --- run_layer3 end-to-end ---------------------------------------------------


def _fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "gopts"


def _perfect_answer_fn(fixture: GoptsFixture) -> str:
    """Return the expected answer verbatim — simulates a perfect lane."""
    return fixture.expected_answer


def _perfect_evidence_fn(fixture: GoptsFixture) -> Sequence[str]:
    """Return the expected evidence verbatim, in order."""
    return list(fixture.expected_evidence_entities)


def test_run_layer3_perfect_lane_scores_all_dimensions_high() -> None:
    fixtures = load_fixtures(_fixture_dir())
    report = aq.run_layer3(
        fixtures,
        evidence_fn=_perfect_evidence_fn,
        answer_fn=_perfect_answer_fn,
        k=5,
    )
    # Only fixtures with expected_answer are scored — all 10 now have one.
    assert report.total_fixtures >= 8
    assert report.avg_token_f1 == 1.0
    assert report.exact_match_rate == 1.0
    # 07_label_count has empty expected_evidence_entities so its hit/mrr
    # are 0; every other fixture is a perfect hit. Average is < 1.0 but > 0.
    assert 0.0 < report.avg_hit_at_k <= 1.0
    # No judge supplied → judge dimension reads zero / not-evaluated.
    assert report.avg_judge_score == 0.0
    assert report.judge_evaluated == 0


def test_run_layer3_skips_fixtures_without_expected_answer() -> None:
    no_answer = GoptsFixture(
        fixture_id="bare",
        question="q",
        intent="entity_lookup",
        # no expected_answer
    )
    report = aq.run_layer3(
        [no_answer],
        evidence_fn=lambda f: [],
        answer_fn=lambda f: "anything",
    )
    assert report.total_fixtures == 0


def test_run_layer3_with_mock_judge_aggregates_judge_score() -> None:
    fixtures = [
        GoptsFixture(
            fixture_id="j1",
            question="Who manages Apple?",
            intent="relationship_lookup",
            expected_answer="Tim Cook manages Apple.",
            expected_evidence_entities=("Tim Cook", "Apple"),
        )
    ]

    def mock_judge(question: str, expected: str, actual: str) -> float:
        # Deterministic: full credit when the actual contains "Tim Cook".
        return 1.0 if "Tim Cook" in actual else 0.0

    report = aq.run_layer3(
        fixtures,
        evidence_fn=lambda f: ["Tim Cook", "Apple"],
        answer_fn=lambda f: "Tim Cook manages Apple.",
        judge_fn=mock_judge,
    )
    assert report.judge_evaluated == 1
    assert report.avg_judge_score == 1.0


def test_run_layer3_judge_score_clamped_to_unit_interval() -> None:
    fixtures = [
        GoptsFixture(
            fixture_id="clamp",
            question="q",
            intent="entity_lookup",
            expected_answer="Apple",
        )
    ]

    def over_judge(question: str, expected: str, actual: str) -> float:
        return 5.0  # out-of-range; harness must clamp to 1.0

    report = aq.run_layer3(
        fixtures,
        evidence_fn=lambda f: [],
        answer_fn=lambda f: "Apple",
        judge_fn=over_judge,
    )
    assert report.avg_judge_score == 1.0


def test_run_layer3_judge_exception_drops_only_that_score() -> None:
    fixtures = [
        GoptsFixture(
            fixture_id="boom",
            question="q",
            intent="entity_lookup",
            expected_answer="Apple",
        )
    ]

    def boom_judge(question: str, expected: str, actual: str) -> float:
        raise RuntimeError("judge backend down")

    report = aq.run_layer3(
        fixtures,
        evidence_fn=lambda f: [],
        answer_fn=lambda f: "Apple",
        judge_fn=boom_judge,
    )
    # Token-F1 / exact still scored; judge dimension just not evaluated.
    assert report.total_fixtures == 1
    assert report.avg_token_f1 == 1.0
    assert report.judge_evaluated == 0


def test_run_layer3_imperfect_lane_lowers_scores() -> None:
    fixtures = [
        GoptsFixture(
            fixture_id="wrong",
            question="What was Microsoft's revenue in 2022?",
            intent="financial_metric_lookup",
            expected_answer="Microsoft's revenue in 2022 was 198.3B.",
            expected_evidence_entities=("Microsoft", "revenue"),
        )
    ]

    # Lane returns Apple's number instead — wrong company + wrong figure.
    def wrong_answer_fn(f: GoptsFixture) -> str:
        return "Apple's revenue in 2022 was 365.8B."

    def wrong_evidence_fn(f: GoptsFixture) -> Sequence[str]:
        # Retrieves only Apple-side entities — none of the expected
        # (Microsoft, revenue) appear, so this is a genuine retrieval miss.
        return ["Apple", "Tesla"]

    report = aq.run_layer3(
        fixtures,
        evidence_fn=wrong_evidence_fn,
        answer_fn=wrong_answer_fn,
    )
    assert report.exact_match_rate == 0.0
    assert report.avg_token_f1 < 1.0
    # neither Microsoft nor revenue retrieved → hit miss
    assert report.avg_hit_at_k == 0.0
    assert report.avg_mrr == 0.0


def test_layer3_report_to_dict_is_json_serializable() -> None:
    import json

    fixtures = load_fixtures(_fixture_dir())
    report = aq.run_layer3(
        fixtures,
        evidence_fn=_perfect_evidence_fn,
        answer_fn=_perfect_answer_fn,
    )
    blob = json.dumps(report.to_dict())
    parsed = json.loads(blob)
    assert "avg_token_f1" in parsed
    assert "exact_match_rate" in parsed
    assert "avg_mrr" in parsed
    assert len(parsed["fixtures"]) == report.total_fixtures


# --- make_llm_judge_fn adapter -----------------------------------------------


def test_make_llm_judge_fn_parses_json_score() -> None:
    class FakeBackend:
        def complete(self, **kwargs: Any) -> Any:
            # capture the structured-output request shape
            assert kwargs["response_format"] == {"type": "json_object"}
            return SimpleNamespace(text='{"score": 0.75}')

    judge = aq.make_llm_judge_fn(FakeBackend())
    score = judge("q", "expected", "actual")
    assert score == 0.75


def test_make_llm_judge_fn_falls_back_to_regex_on_bad_json() -> None:
    class FakeBackend:
        def complete(self, **kwargs: Any) -> Any:
            return SimpleNamespace(text="I'd rate this 0.4 out of 1")

    judge = aq.make_llm_judge_fn(FakeBackend())
    assert judge("q", "e", "a") == 0.4


def test_make_llm_judge_fn_zero_on_unparseable() -> None:
    class FakeBackend:
        def complete(self, **kwargs: Any) -> Any:
            return SimpleNamespace(text="no number here")

    judge = aq.make_llm_judge_fn(FakeBackend())
    assert judge("q", "e", "a") == 0.0


def test_make_llm_judge_fn_wires_into_run_layer3() -> None:
    """End-to-end: a fake-backed judge plugs into run_layer3 exactly like
    a grok or deepseek judge would."""
    fixtures = [
        GoptsFixture(
            fixture_id="wired",
            question="q",
            intent="entity_lookup",
            expected_answer="Apple",
        )
    ]

    class FakeBackend:
        def complete(self, **kwargs: Any) -> Any:
            return SimpleNamespace(text='{"score": 0.9}')

    judge = aq.make_llm_judge_fn(FakeBackend())
    report = aq.run_layer3(
        fixtures,
        evidence_fn=lambda f: [],
        answer_fn=lambda f: "Apple",
        judge_fn=judge,
    )
    assert report.judge_evaluated == 1
    assert report.avg_judge_score == 0.9


# --- answerability (cold-review: the upstream gate) ---------------------------


def test_answerability_rate_basic() -> None:
    from seocho.eval.gopts_answer_quality import answerability_rate
    assert answerability_rate([0, 0, 0, 0]) == 0.0      # all empty
    assert answerability_rate([1, 1, 1, 1]) == 1.0
    assert answerability_rate([3, 0, 5, 0]) == 0.5
    assert answerability_rate([]) == 0.0
