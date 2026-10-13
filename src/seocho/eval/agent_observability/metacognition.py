"""Public epistemic claims and paired outcomes; never private reasoning traces."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequiredSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: Literal["supported", "missing", "ambiguous"]
    evidence_refs: list[str]


class EpistemicAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    evidence_refs: list[str]
    answerable: bool
    confidence: float = Field(ge=0, le=1)
    required_slots: list[RequiredSlot]
    unsupported_assumptions: list[str]
    requested_capabilities: list[
        Literal["search", "ontology", "knowledge_graph", "calculator", "clarification"]
    ]

    @model_validator(mode="after")
    def unique_slots(self) -> EpistemicAssessment:
        if len({s.slot_id for s in self.required_slots}) != len(self.required_slots):
            raise ValueError("Required slot IDs must be unique")
        return self

    def validate_evidence(self, allowed: set[str]) -> None:
        cited = set(self.evidence_refs)
        for slot in self.required_slots:
            cited.update(slot.evidence_refs)
        if not cited <= allowed:
            raise ValueError("Assessment invented evidence reference")


def paired_outcome(initial: str, final: str, assessment: EpistemicAssessment) -> dict:
    """One paired observation; aggregate precision/recall needs adjudicated labels."""
    known = {"correct", "incorrect"}
    before = initial == "correct" if initial in known else None
    after = final == "correct" if final in known else None
    return {
        "label_basis": "model_judgment_requires_human_calibration",
        "initial_correct": before,
        "final_correct": after,
        "recovery": (not before and after)
        if before is not None and after is not None
        else None,
        "regression": (before and not after)
        if before is not None and after is not None
        else None,
        "predicted_failure": assessment.confidence < 0.5,
        "failure_prediction_threshold": 0.5,
        "self_reported_answerability": assessment.answerable,
        "failure_detection_match": (assessment.confidence < 0.5) == (not before)
        if before is not None
        else None,
        "confidence_brier": (assessment.confidence - float(before)) ** 2
        if before is not None
        else None,
        "semantic_tool_necessity": "unknown_without_counterfactual_or_adjudication",
    }
