"""Versioned production-agent harness manifests and rubric promotion gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class HarnessManifest:
    version: str
    runtime: str
    model_route: str
    prompt_version: str
    ontology_version: str
    policy_version: str
    retrieval_profile: str

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "runtime": self.runtime,
            "model_route": self.model_route,
            "prompt_version": self.prompt_version,
            "ontology_version": self.ontology_version,
            "policy_version": self.policy_version,
            "retrieval_profile": self.retrieval_profile,
        }


@dataclass(frozen=True, slots=True)
class RubricScore:
    rubric_id: str
    score: float
    threshold: float
    critical: bool = False
    evidence_ref: str = ""

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    allowed: bool
    status: str
    failed_rubrics: tuple[str, ...]
    candidate_fingerprint: str
    baseline_fingerprint: str
    metrics: Mapping[str, float]


class HarnessPromotionGate:
    """Candidate-only gate; never auto-promotes an unevaluated harness."""

    def evaluate(
        self,
        *,
        baseline: HarnessManifest,
        candidate: HarnessManifest,
        scores: Sequence[RubricScore],
        minimum_pass_ratio: float = 1.0,
    ) -> PromotionDecision:
        if not 0 < minimum_pass_ratio <= 1:
            raise ValueError("minimum_pass_ratio must be in (0, 1]")
        if not scores:
            return PromotionDecision(
                False,
                "insufficient_evidence",
                (),
                candidate.fingerprint,
                baseline.fingerprint,
                {"pass_ratio": 0.0},
            )
        failed = tuple(score.rubric_id for score in scores if not score.passed)
        critical_failed = any(score.critical and not score.passed for score in scores)
        pass_ratio = sum(score.passed for score in scores) / len(scores)
        allowed = not critical_failed and pass_ratio >= minimum_pass_ratio
        return PromotionDecision(
            allowed=allowed,
            status="promote_candidate" if allowed else "hold_candidate",
            failed_rubrics=failed,
            candidate_fingerprint=candidate.fingerprint,
            baseline_fingerprint=baseline.fingerprint,
            metrics={"pass_ratio": pass_ratio, "rubric_count": float(len(scores))},
        )
