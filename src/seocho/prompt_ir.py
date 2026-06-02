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
    ) -> PromptAssemblyReceipt:
        return PromptAssemblyReceipt(
            stage=self.stage,
            task_hint=self.task_hint,
            provider=provider,
            query_mode=query_mode,
            reasoning_mode=self.reasoning_mode,
            selected_section_ids=self.selected_section_ids(),
            precedence_sources=self.semantic_precedence_sources(),
            response_format=dict(self.response_format or {}),
            stable_prefix_hash=self.stable_prefix_hash(),
            adapter_hint_keys=sorted(self.adapter_hints.keys()),
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
    "PromptSection",
    "PromptSectionKind",
    "PromptSource",
    "PromptStage",
    "SEMANTIC_PROMPT_PRECEDENCE",
    "StagePromptSpec",
]
