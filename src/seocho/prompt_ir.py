"""Preparatory typed prompt IR for stage-aware prompt assembly.

This module is an internal scaffold for future prompt-composer work. It
captures prompt stages, section sources, and structured assembly receipts
without changing current runtime behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any, Dict, List, Optional


class PromptStage(str, Enum):
    ONTOLOGY_CANDIDATE = "ontology_candidate"
    SHACL_CANDIDATE = "shacl_candidate"
    ENTITY_EXTRACTION = "entity_extraction"
    ENTITY_LINKING = "entity_linking"
    INTENT_CLASSIFICATION = "intent_classification"
    QUERY_PLANNING = "query_planning"
    QUERY_REPAIR = "query_repair"
    ANSWER_SYNTHESIS = "answer_synthesis"
    TOOL_AGENT = "tool_agent"
    GUARDRAIL_REVIEW = "guardrail_review"


class PromptSectionKind(str, Enum):
    CONTRACT = "contract"
    ONTOLOGY = "ontology"
    SHACL = "shacl"
    VOCABULARY = "vocabulary"
    METADATA = "metadata"
    DEVELOPER_INSTRUCTIONS = "developer_instructions"
    EVIDENCE = "evidence"
    DIAGNOSTICS = "diagnostics"
    USER_INPUT = "user_input"
    OUTPUT_SCHEMA = "output_schema"
    VERIFICATION = "verification"
    TOOL_POLICY = "tool_policy"
    EXAMPLES = "examples"


class PromptSource(str, Enum):
    SYSTEM_CONTRACT = "system_contract"
    GRAPH_TARGET_METADATA = "graph_target_metadata"
    APPROVED_ARTIFACTS = "approved_artifacts"
    REQUEST_PROMPT_CONTEXT = "request_prompt_context"
    RUNTIME_CANDIDATE = "runtime_candidate"
    DEVELOPER_INPUT = "developer_input"
    RETRIEVAL_EVIDENCE = "retrieval_evidence"
    EXECUTION_DIAGNOSTICS = "execution_diagnostics"
    TOOL_REGISTRY = "tool_registry"
    EXAMPLE_BANK = "example_bank"
    USER_INPUT = "user_input"
    OUTPUT_CONTRACT = "output_contract"


SEMANTIC_PROMPT_PRECEDENCE: tuple[PromptSource, ...] = (
    PromptSource.GRAPH_TARGET_METADATA,
    PromptSource.APPROVED_ARTIFACTS,
    PromptSource.REQUEST_PROMPT_CONTEXT,
    PromptSource.RUNTIME_CANDIDATE,
)


def _fingerprint(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _render_section(section: "PromptSection") -> str:
    if not section.content:
        return ""
    header = section.title.strip()
    if header:
        return f"{header}\n{section.content.strip()}"
    return section.content.strip()


@dataclass(slots=True)
class PromptSection:
    section_id: str
    kind: PromptSectionKind
    source: PromptSource
    title: str
    content: str
    cacheable: bool = True
    sensitive: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "kind": self.kind.value,
            "source": self.source.value,
            "title": self.title,
            "content": self.content,
            "cacheable": self.cacheable,
            "sensitive": self.sensitive,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class PromptAssemblyReceipt:
    stage: PromptStage
    task_hint: str = ""
    provider: str = ""
    query_mode: str = ""
    reasoning_mode: Optional[bool] = None
    selected_section_ids: List[str] = field(default_factory=list)
    precedence_sources: List[str] = field(default_factory=list)
    response_format: Dict[str, Any] = field(default_factory=dict)
    stable_prefix_hash: str = ""
    adapter_hint_keys: List[str] = field(default_factory=list)
    optimization: "PromptOptimizationReceipt" = field(
        default_factory=lambda: PromptOptimizationReceipt()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.value,
            "task_hint": self.task_hint,
            "provider": self.provider,
            "query_mode": self.query_mode,
            "reasoning_mode": self.reasoning_mode,
            "selected_section_ids": list(self.selected_section_ids),
            "precedence_sources": list(self.precedence_sources),
            "response_format": dict(self.response_format),
            "stable_prefix_hash": self.stable_prefix_hash,
            "adapter_hint_keys": list(self.adapter_hint_keys),
            "optimization": self.optimization.to_dict(),
        }

    def to_trace_attributes(self) -> Dict[str, Any]:
        """Return bounded, content-free attributes for an OTel span.

        Section bodies, user input, identifiers, and exclusion details stay out
        of telemetry.  The full receipt can still be returned by an authorized
        debug/API surface because it contains section IDs and reasons, not the
        prompt text itself.
        """
        optimization = self.optimization
        return {
            "seocho.prompt.stage": self.stage.value,
            "seocho.prompt.provider": self.provider,
            "seocho.prompt.query_mode": self.query_mode,
            "seocho.prompt.stable_prefix_hash": self.stable_prefix_hash[:16],
            "seocho.prompt.candidate_sections": optimization.candidate_section_count,
            "seocho.prompt.selected_sections": optimization.selected_section_count,
            "seocho.prompt.omitted_sections": optimization.omitted_section_count,
            "seocho.prompt.candidate_tokens_estimate": optimization.estimated_candidate_tokens,
            "seocho.prompt.selected_tokens_estimate": optimization.estimated_selected_tokens,
            "seocho.prompt.token_budget": optimization.token_budget,
            "seocho.prompt.compression_ratio": optimization.compression_ratio,
            "seocho.prompt.cacheable_prefix_tokens_estimate": optimization.cacheable_prefix_tokens,
            "seocho.prompt.evidence_count": optimization.evidence_count,
            "seocho.prompt.provenance_count": optimization.provenance_count,
        }


@dataclass(slots=True)
class PromptOptimizationReceipt:
    """Privacy-safe explanation of how a prompt was reduced and assembled."""

    strategy: str = "stage_aware_selection"
    candidate_section_count: int = 0
    selected_section_count: int = 0
    omitted_section_count: int = 0
    estimated_candidate_tokens: int = 0
    estimated_selected_tokens: int = 0
    token_budget: int = 0
    compression_ratio: float = 1.0
    cacheable_prefix_tokens: int = 0
    excluded_section_reasons: Dict[str, str] = field(default_factory=dict)
    evidence_count: int = 0
    provenance_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "candidate_section_count": self.candidate_section_count,
            "selected_section_count": self.selected_section_count,
            "omitted_section_count": self.omitted_section_count,
            "estimated_candidate_tokens": self.estimated_candidate_tokens,
            "estimated_selected_tokens": self.estimated_selected_tokens,
            "token_budget": self.token_budget,
            "compression_ratio": self.compression_ratio,
            "cacheable_prefix_tokens": self.cacheable_prefix_tokens,
            "excluded_section_reasons": dict(self.excluded_section_reasons),
            "evidence_count": self.evidence_count,
            "provenance_count": self.provenance_count,
        }


@dataclass(slots=True)
class StagePromptSpec:
    stage: PromptStage
    task_hint: str = ""
    reasoning_mode: Optional[bool] = None
    system_sections: List[PromptSection] = field(default_factory=list)
    user_sections: List[PromptSection] = field(default_factory=list)
    output_schema: str = ""
    verification_rules: List[str] = field(default_factory=list)
    response_format: Optional[Dict[str, Any]] = None
    adapter_hints: Dict[str, Any] = field(default_factory=dict)

    def selected_section_ids(self) -> List[str]:
        return [section.section_id for section in [*self.system_sections, *self.user_sections]]

    def semantic_precedence_sources(self) -> List[str]:
        present_sources = {section.source for section in [*self.system_sections, *self.user_sections]}
        return [source.value for source in SEMANTIC_PROMPT_PRECEDENCE if source in present_sources]

    def stable_prefix_text(self) -> str:
        blocks: List[str] = []
        for section in self.system_sections:
            if section.cacheable:
                rendered = _render_section(section)
                if rendered:
                    blocks.append(rendered)
        if self.output_schema.strip():
            blocks.append(f"Output Schema\n{self.output_schema.strip()}")
        if self.verification_rules:
            checks = "\n".join(f"- {rule.strip()}" for rule in self.verification_rules if rule.strip())
            if checks:
                blocks.append(f"Verification\n{checks}")
        return "\n\n".join(blocks).strip()

    def stable_prefix_hash(self) -> str:
        return _fingerprint(self.stable_prefix_text())

    def build_receipt(
        self,
        *,
        provider: str = "",
        query_mode: str = "",
        candidate_section_ids: Optional[List[str]] = None,
        excluded_section_reasons: Optional[Dict[str, str]] = None,
        token_budget: int = 0,
        estimated_candidate_tokens: Optional[int] = None,
        evidence_count: int = 0,
        provenance_count: int = 0,
    ) -> PromptAssemblyReceipt:
        selected = self.selected_section_ids()
        candidates = list(dict.fromkeys(candidate_section_ids or selected))
        selected_text = "\n\n".join(
            rendered
            for section in [*self.system_sections, *self.user_sections]
            if (rendered := _render_section(section))
        )
        selected_tokens = max((len(selected_text) + 3) // 4, 0)
        candidate_tokens = max(
            estimated_candidate_tokens
            if estimated_candidate_tokens is not None
            else selected_tokens,
            selected_tokens,
        )
        # The composer may know only IDs for omitted sections.  In that case
        # report a conservative selected-token estimate instead of inventing
        # the size of content that was deliberately not retained.
        omitted = max(len(candidates) - len(selected), 0)
        compression_ratio = 1.0 if candidate_tokens == 0 else round(
            selected_tokens / candidate_tokens, 4
        )
        optimization = PromptOptimizationReceipt(
            candidate_section_count=len(candidates),
            selected_section_count=len(selected),
            omitted_section_count=omitted,
            estimated_candidate_tokens=candidate_tokens,
            estimated_selected_tokens=selected_tokens,
            token_budget=max(token_budget, 0),
            compression_ratio=compression_ratio,
            cacheable_prefix_tokens=(len(self.stable_prefix_text()) + 3) // 4,
            excluded_section_reasons=dict(excluded_section_reasons or {}),
            evidence_count=max(evidence_count, 0),
            provenance_count=max(provenance_count, 0),
        )
        return PromptAssemblyReceipt(
            stage=self.stage,
            task_hint=self.task_hint,
            provider=provider,
            query_mode=query_mode,
            reasoning_mode=self.reasoning_mode,
            selected_section_ids=selected,
            precedence_sources=self.semantic_precedence_sources(),
            response_format=dict(self.response_format or {}),
            stable_prefix_hash=self.stable_prefix_hash(),
            adapter_hint_keys=sorted(self.adapter_hints.keys()),
            optimization=optimization,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.value,
            "task_hint": self.task_hint,
            "reasoning_mode": self.reasoning_mode,
            "system_sections": [section.to_dict() for section in self.system_sections],
            "user_sections": [section.to_dict() for section in self.user_sections],
            "output_schema": self.output_schema,
            "verification_rules": list(self.verification_rules),
            "response_format": dict(self.response_format or {}),
            "adapter_hints": dict(self.adapter_hints),
        }


__all__ = [
    "PromptAssemblyReceipt",
    "PromptOptimizationReceipt",
    "PromptSection",
    "PromptSectionKind",
    "PromptSource",
    "PromptStage",
    "SEMANTIC_PROMPT_PRECEDENCE",
    "StagePromptSpec",
]
