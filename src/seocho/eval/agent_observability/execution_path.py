"""Typed public task plans, distinct from private reasoning and actual receipts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evaluator import Dimension


class PathStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    required_slots: list[str]
    depends_on: list[str]
    completion_criteria: str = Field(min_length=1)
    requested_capabilities: list[str]


class PublicPathPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decompose: bool
    decision_summary: str
    steps: list[PathStep] = Field(max_length=3)

    @model_validator(mode="after")
    def ordered_dag(self) -> PublicPathPlan:
        seen: set[str] = set()
        if self.decompose != bool(self.steps):
            raise ValueError(
                "Decomposed plan requires steps; direct plan must have none"
            )
        for step in self.steps:
            if step.step_id in seen or not set(step.depends_on) <= seen:
                raise ValueError("Unique steps and earlier-only dependencies required")
            seen.add(step.step_id)
        return self


class PathResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    evidence_refs: list[str]
    missing_slots: list[str]
    unsupported_assumptions: list[str]
    completion_claim: str


class PathEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decomposition_coverage: Dimension
    dependency_validity: Dimension
    unnecessary_work: Dimension
    missing_evidence_handling: Dimension
    synthesis_faithfulness: Dimension
    rationale: str
