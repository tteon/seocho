"""GOPTS F3 — Layer-3 answer-quality harness (ADR-0097 follow-up).

Layer 1 (gopts_ranking) measures "does the cost model rank plans right?".
Layer 2 (gopts_latency) measures "does the chosen plan run faster?".
Layer 3 (this module) measures "does the answer the lane produces actually
answer the question?" — the GraphRAG-style end of the eval stack.

Restores the three-way answer evaluation the repo used to carry before
the finder-benchmark reverts (git df91116 / 42901da / 2b32d6f):

  1. **token-F1** — precision + recall + harmonic mean over meaningful
     tokens. The old ``benchmarking.score_answer_slots`` only computed
     recall; F3 promotes it to a real F1 by adding the precision side.
  2. **exact-match** — normalized string equality, reusing
     ``benchmarking.compare_answers`` so the normalization rules stay
     identical across the codebase.
  3. **LLM-as-judge** — optional, injected as a ``judge_fn`` callable so
     the caller binds whatever backend (grok, deepseek, an ensemble of
     both) it wants. The harness never imports a model — same
     dependency-inversion pattern as Layer-1's ``oracle_fn`` and
     Layer-2's ``baseline_fn``/``gopts_fn``. When ``judge_fn`` is None
     the LLM dimension is simply skipped and its aggregate reads 0.0
     with ``judge_evaluated = 0``.

Evidence retrieval is scored with Hit@k and MRR against each fixture's
``expected_evidence_entities``.

All metric primitives are pure functions so the dimensions can be reused
piecemeal (e.g. a notebook computing token-F1 alone).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Reuse the canonical normalization + tokenization from the finance
# benchmark so Layer-3 scoring matches the rest of the repo bit-for-bit.
from ..benchmarking import _meaningful_tokens, compare_answers, normalize_answer
from .gopts_ranking import GoptsFixture


# ---------------------------------------------------------------------------
# Dimension 1 — token-F1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenF1:
    """Precision / recall / F1 over meaningful tokens of expected vs actual."""

    precision: float
    recall: float
    f1: float
    expected_token_count: int
    actual_token_count: int
    overlap_count: int


def token_f1(expected: str, actual: str) -> TokenF1:
    """Compute token-level precision, recall, and harmonic-mean F1.

    Uses ``benchmarking._meaningful_tokens`` (stopword-filtered, lowercased,
    1-char/digit-preserving) so the token set matches the finance benchmark.
    Empty expected OR actual yields all-zero — there's no meaningful overlap
    to score, and returning 1.0 for "both empty" would reward a non-answer.
    """
    expected_tokens = _meaningful_tokens(expected)
    actual_tokens = _meaningful_tokens(actual)
    if not expected_tokens or not actual_tokens:
        return TokenF1(
            precision=0.0,
            recall=0.0,
            f1=0.0,
            expected_token_count=len(expected_tokens),
            actual_token_count=len(actual_tokens),
            overlap_count=0,
        )
    overlap = expected_tokens & actual_tokens
    precision = len(overlap) / len(actual_tokens)
    recall = len(overlap) / len(expected_tokens)
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return TokenF1(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        expected_token_count=len(expected_tokens),
        actual_token_count=len(actual_tokens),
        overlap_count=len(overlap),
    )


# ---------------------------------------------------------------------------
# Dimension 2 — exact-match (delegated to benchmarking.compare_answers)
# ---------------------------------------------------------------------------


def exact_match(expected: str, actual: str) -> bool:
    """Normalized string equality. Reuses benchmarking.compare_answers so
    the normalization rules (lowercase, strip non-alnum, collapse space)
    are identical to the finance benchmark's exact-match."""
    exact, _contains = compare_answers(expected, actual)
    return exact


# ---------------------------------------------------------------------------
# Answerability — the upstream gate the cold review flagged
# ---------------------------------------------------------------------------


def answerability_rate(record_counts: Sequence[int]) -> float:
    """Fraction of cases whose retrieval returned >= 1 record.

    The cold-review diagnosis (2026-06-03): no synthesis directive, plan
    ranking, fusion, or grounding can help when the modal retrieval result
    is the empty set. Answerability — "can the graph even answer?" — is the
    upstream gate that bounds every downstream answer-quality metric, so it
    must be measured FIRST. Returns 0.0 for an empty input.
    """
    if not record_counts:
        return 0.0
    answered = sum(1 for c in record_counts if c and c >= 1)
    return round(answered / len(record_counts), 4)


# ---------------------------------------------------------------------------
# Evidence retrieval — Hit@k and MRR
# ---------------------------------------------------------------------------


def _normalized_set(items: Sequence[str]) -> set:
    return {normalize_answer(x) for x in items if normalize_answer(x)}


def hit_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    """1.0 if any expected entity appears in the top-k retrieved, else 0.0.

    Entity names are normalized before comparison so casing / punctuation
    differences don't sink a real hit.
    """
    if not expected or k <= 0:
        return 0.0
    top_k = _normalized_set(retrieved[:k])
    want = _normalized_set(expected)
    return 1.0 if top_k & want else 0.0


def mrr(retrieved: Sequence[str], expected: Sequence[str]) -> float:
    """Mean reciprocal rank: 1/(rank of first expected hit), else 0.0.

    Single-query MRR == reciprocal rank; the suite-level mean is the
    average across fixtures.
    """
    if not expected:
        return 0.0
    want = _normalized_set(expected)
    for idx, item in enumerate(retrieved):
        if normalize_answer(item) in want:
            return 1.0 / (idx + 1)
    return 0.0


# ---------------------------------------------------------------------------
# Dimension 3 — LLM-as-judge (injected callable)
# ---------------------------------------------------------------------------

# judge_fn(question, expected_answer, actual_answer) -> score in [0, 1].
# The caller binds the model: grok-judge, deepseek-judge, or an ensemble
# that averages both. The harness never imports a backend.
JudgeFn = Callable[[str, str, str], float]

# evidence_fn(fixture) -> ranked list of retrieved entity names.
EvidenceFn = Callable[[GoptsFixture], Sequence[str]]

# answer_fn(fixture) -> the lane's natural-language answer string.
AnswerFn = Callable[[GoptsFixture], str]


# ---------------------------------------------------------------------------
# Per-fixture + aggregate reports
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureAnswerResult:
    fixture_id: str
    token_f1: float
    token_precision: float
    token_recall: float
    exact_match: bool
    hit_at_k: float
    mrr: float
    judge_score: Optional[float]  # None when judge_fn not supplied
    retrieved_entities: Tuple[str, ...]
    actual_answer: str


@dataclass(frozen=True)
class Layer3Report:
    fixture_results: Tuple[FixtureAnswerResult, ...]
    k: int
    avg_token_f1: float
    avg_token_precision: float
    avg_token_recall: float
    exact_match_rate: float
    avg_hit_at_k: float
    avg_mrr: float
    avg_judge_score: float          # 0.0 when no judge_fn
    judge_evaluated: int            # how many fixtures the judge scored

    @property
    def total_fixtures(self) -> int:
        return len(self.fixture_results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_fixtures": self.total_fixtures,
            "k": self.k,
            "avg_token_f1": self.avg_token_f1,
            "avg_token_precision": self.avg_token_precision,
            "avg_token_recall": self.avg_token_recall,
            "exact_match_rate": self.exact_match_rate,
            "avg_hit_at_k": self.avg_hit_at_k,
            "avg_mrr": self.avg_mrr,
            "avg_judge_score": self.avg_judge_score,
            "judge_evaluated": self.judge_evaluated,
            "fixtures": [
                {
                    "fixture_id": fr.fixture_id,
                    "token_f1": fr.token_f1,
                    "token_precision": fr.token_precision,
                    "token_recall": fr.token_recall,
                    "exact_match": fr.exact_match,
                    "hit_at_k": fr.hit_at_k,
                    "mrr": fr.mrr,
                    "judge_score": fr.judge_score,
                    "retrieved_entities": list(fr.retrieved_entities),
                }
                for fr in self.fixture_results
            ],
        }


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def run_layer3(
    fixtures: Sequence[GoptsFixture],
    *,
    evidence_fn: EvidenceFn,
    answer_fn: AnswerFn,
    judge_fn: Optional[JudgeFn] = None,
    k: int = 5,
) -> Layer3Report:
    """Evaluate answer quality across the fixture suite (3-way + retrieval).

    For each fixture with an ``expected_answer``:
      - token-F1, exact-match against expected_answer
      - Hit@k, MRR of evidence_fn(fixture) vs expected_evidence_entities
      - judge_score = judge_fn(question, expected, actual) when supplied

    Fixtures without an ``expected_answer`` are skipped (Layer-1-only
    fixtures), so total_fixtures reflects the answer-annotated subset.
    A judge_fn that raises on a fixture drops only that fixture's judge
    score (best-effort), not the whole run.
    """
    results: List[FixtureAnswerResult] = []
    for fixture in fixtures:
        if not fixture.expected_answer:
            continue
        actual = answer_fn(fixture)
        retrieved = tuple(str(e) for e in evidence_fn(fixture))

        tf1 = token_f1(fixture.expected_answer, actual)
        em = exact_match(fixture.expected_answer, actual)
        h = hit_at_k(retrieved, fixture.expected_evidence_entities, k)
        rr = mrr(retrieved, fixture.expected_evidence_entities)

        judge_score: Optional[float] = None
        if judge_fn is not None:
            try:
                raw = float(judge_fn(fixture.question, fixture.expected_answer, actual))
                judge_score = max(0.0, min(1.0, raw))  # clamp to [0,1]
            except Exception:
                judge_score = None

        results.append(
            FixtureAnswerResult(
                fixture_id=fixture.fixture_id,
                token_f1=tf1.f1,
                token_precision=tf1.precision,
                token_recall=tf1.recall,
                exact_match=em,
                hit_at_k=h,
                mrr=rr,
                judge_score=judge_score,
                retrieved_entities=retrieved,
                actual_answer=actual,
            )
        )

    if not results:
        return Layer3Report(
            fixture_results=(),
            k=k,
            avg_token_f1=0.0,
            avg_token_precision=0.0,
            avg_token_recall=0.0,
            exact_match_rate=0.0,
            avg_hit_at_k=0.0,
            avg_mrr=0.0,
            avg_judge_score=0.0,
            judge_evaluated=0,
        )

    n = len(results)
    judged = [r.judge_score for r in results if r.judge_score is not None]
    return Layer3Report(
        fixture_results=tuple(results),
        k=k,
        avg_token_f1=round(sum(r.token_f1 for r in results) / n, 4),
        avg_token_precision=round(sum(r.token_precision for r in results) / n, 4),
        avg_token_recall=round(sum(r.token_recall for r in results) / n, 4),
        exact_match_rate=round(sum(1 for r in results if r.exact_match) / n, 4),
        avg_hit_at_k=round(sum(r.hit_at_k for r in results) / n, 4),
        avg_mrr=round(sum(r.mrr for r in results) / n, 4),
        avg_judge_score=round(sum(judged) / len(judged), 4) if judged else 0.0,
        judge_evaluated=len(judged),
    )


# ---------------------------------------------------------------------------
# Judge adapter — build a JudgeFn from any seocho LLM backend
# ---------------------------------------------------------------------------


def make_llm_judge_fn(
    llm_backend: Any,
    *,
    temperature: float = 0.0,
) -> JudgeFn:
    """Wrap a seocho LLM backend (grok, deepseek, openai, vllm, …) as a
    JudgeFn that scores answer correctness in [0, 1].

    The backend is whatever ``create_llm_backend(provider=...)`` returns,
    so the caller decides which model judges — and can build an ensemble
    by averaging two ``make_llm_judge_fn`` calls. The judge is asked for a
    single float; parsing is defensive (any unparseable reply scores 0.0,
    which run_layer3 treats as a clamp-floor, not a drop).
    """
    import json
    import re

    _FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")

    def judge(question: str, expected: str, actual: str) -> float:
        system = (
            "You are a strict grader for question-answering. Given the "
            "question, the reference answer, and a candidate answer, output "
            "ONLY a JSON object {\"score\": <float 0..1>} where 1.0 means the "
            "candidate fully matches the reference's facts and 0.0 means it "
            "is wrong or empty. Judge factual correctness, not phrasing."
        )
        user = (
            f"Question: {question}\n"
            f"Reference answer: {expected}\n"
            f"Candidate answer: {actual}\n"
            'Respond with only: {"score": <float>}'
        )
        resp = llm_backend.complete(
            system=system,
            user=user,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        text = getattr(resp, "text", str(resp))
        try:
            return float(json.loads(text).get("score", 0.0))
        except Exception:
            match = _FLOAT_RE.search(text or "")
            return float(match.group(0)) if match else 0.0

    return judge
