"""Typed, cache-aware prompt IR for stage-aware prompt assembly.

The IR is the provider-neutral SEOCHO Prompt Package.  It keeps stable
instructions ahead of request data, records content-free assembly receipts,
and lets adapters express backend cache controls without coupling prompt
composition to a particular inference server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any, Dict, List, Optional

from .metrics import get_metrics


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


class PromptStability(str, Enum):
    """Expected lifetime of a section's exact rendered bytes."""

    IMMUTABLE = "immutable"
    WORKSPACE = "workspace"
    SESSION = "session"
    REQUEST = "request"


class PromptCacheScope(str, Enum):
    """Trust boundary within which a prefix may be reused."""

    NONE = "none"
    WORKSPACE = "workspace"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class PromptBackendCapabilities:
    """Endpoint capabilities; model aliases are resolved outside the IR."""

    protocol: str = "openai_chat"
    cache_mode: str = "automatic"  # automatic | explicit | none
    cache_key_field: str = ""
    cache_salt_field: str = ""
    supports_system_role: bool = True
    supports_structured_output: bool = False
    reports_cached_tokens: bool = False


BUILTIN_PROMPT_BACKENDS: Dict[str, PromptBackendCapabilities] = {
    "generic": PromptBackendCapabilities(cache_mode="none"),
    "openai": PromptBackendCapabilities(
        supports_structured_output=True, reports_cached_tokens=True
    ),
    "xai": PromptBackendCapabilities(
        cache_key_field="prompt_cache_key", reports_cached_tokens=True
    ),
    "kimi": PromptBackendCapabilities(),
    "qwen": PromptBackendCapabilities(reports_cached_tokens=True),
    "mara": PromptBackendCapabilities(),
    "vllm": PromptBackendCapabilities(
        cache_salt_field="cache_salt", reports_cached_tokens=True
    ),
    "sglang": PromptBackendCapabilities(reports_cached_tokens=True),
    "anthropic": PromptBackendCapabilities(
        protocol="anthropic_messages", cache_mode="explicit", reports_cached_tokens=True
    ),
    # Muse/Spark endpoints may be hosted through different gateways.  Keep the
    # conservative OpenAI-compatible baseline and allow deployment overrides.
    "meta_muse": PromptBackendCapabilities(),
}


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
    stability: PromptStability = PromptStability.WORKSPACE
    cache_scope: PromptCacheScope = PromptCacheScope.WORKSPACE
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
            "stability": self.stability.value,
            "cache_scope": self.cache_scope.value,
            "content_hash": _fingerprint(_render_section(self)),
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
    cache_scope: str = PromptCacheScope.NONE.value
    cache_salt_hash: str = ""
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
            "cache_scope": self.cache_scope,
            "cache_salt_hash": self.cache_salt_hash,
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
            "seocho.prompt.cache_scope": self.cache_scope,
            "seocho.prompt.cache_salt_hash": self.cache_salt_hash[:16],
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

    def validate_cache_layout(self) -> None:
        """Reject layouts that make prefix reuse unsafe or ineffective."""
        seen: set[str] = set()
        volatile_seen = False
        for section in [*self.system_sections, *self.user_sections]:
            if not section.section_id or section.section_id in seen:
                raise ValueError("Prompt section IDs must be non-empty and unique")
            seen.add(section.section_id)
            cacheable = (
                section.cacheable
                and section.kind is not PromptSectionKind.USER_INPUT
                and section.source is not PromptSource.USER_INPUT
                and section.cache_scope is not PromptCacheScope.NONE
            )
            if not cacheable or section.stability is PromptStability.REQUEST:
                volatile_seen = True
            elif volatile_seen:
                raise ValueError("Cacheable prompt sections must precede volatile sections")
            if section.sensitive and section.cache_scope is PromptCacheScope.WORKSPACE:
                raise ValueError("Sensitive prompt sections cannot use workspace cache scope")

    def effective_cache_scope(self) -> PromptCacheScope:
        scopes = {
            section.cache_scope
            for section in self.system_sections
            if section.cacheable and section.cache_scope is not PromptCacheScope.NONE
        }
        if PromptCacheScope.SESSION in scopes:
            return PromptCacheScope.SESSION
        if PromptCacheScope.WORKSPACE in scopes:
            return PromptCacheScope.WORKSPACE
        return PromptCacheScope.NONE

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

    def render_package(
        self,
        *,
        backend: str = "generic",
        cache_salt: str = "",
        cache_key: str = "",
        capabilities: Optional[PromptBackendCapabilities] = None,
    ) -> Dict[str, Any]:
        """Render a deterministic provider request plus private cache metadata.

        vLLM and SGLang reuse identical leading tokens automatically.  vLLM's
        optional ``cache_salt`` is emitted for tenant isolation.  Anthropic
        receives an explicit ephemeral breakpoint on the stable system block.
        Other OpenAI-compatible APIs receive ordinary ordered messages.
        """
        self.validate_cache_layout()
        stable = self.stable_prefix_text()
        volatile_system = [
            _render_section(section)
            for section in self.system_sections
            if (
                not section.cacheable
                or section.stability is PromptStability.REQUEST
                or section.cache_scope is PromptCacheScope.NONE
            )
            and _render_section(section)
        ]
        user = "\n\n".join(
            rendered
            for section in self.user_sections
            if (rendered := _render_section(section))
        )
        system = "\n\n".join(part for part in [stable, *volatile_system] if part)
        normalized = backend.strip().lower()
        profile = capabilities or BUILTIN_PROMPT_BACKENDS.get(
            normalized, BUILTIN_PROMPT_BACKENDS["generic"]
        )
        payload: Dict[str, Any]
        if profile.protocol == "anthropic_messages":
            system_blocks: List[Dict[str, Any]] = []
            if stable:
                system_blocks.append(
                    {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}
                )
            if volatile_system:
                system_blocks.append({"type": "text", "text": "\n\n".join(volatile_system)})
            payload = {"system": system_blocks, "messages": [{"role": "user", "content": user}]}
        else:
            payload = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if profile.cache_salt_field and cache_salt:
                payload[profile.cache_salt_field] = cache_salt
            if profile.cache_key_field and cache_key:
                payload[profile.cache_key_field] = cache_key
        receipt = {
            "schema_version": "seocho.prompt.v1",
            "stage": self.stage.value,
            "stable_prefix_hash": self.stable_prefix_hash(),
            "cache_scope": self.effective_cache_scope().value,
            "cache_salt_hash": _fingerprint(cache_salt) if cache_salt else "",
            "cache_key_hash": _fingerprint(cache_key) if cache_key else "",
            "backend": normalized,
            "cache_mode": profile.cache_mode,
            "section_hashes": {
                section.section_id: _fingerprint(_render_section(section))
                for section in [*self.system_sections, *self.user_sections]
            },
        }
        # Keep audit metadata outside the transport request. Passing unknown
        # SEOCHO fields through strict provider APIs would otherwise fail.
        return {"request": payload, "receipt": receipt}

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
        receipt = PromptAssemblyReceipt(
            stage=self.stage,
            task_hint=self.task_hint,
            provider=provider,
            query_mode=query_mode,
            reasoning_mode=self.reasoning_mode,
            selected_section_ids=selected,
            precedence_sources=self.semantic_precedence_sources(),
            response_format=dict(self.response_format or {}),
            stable_prefix_hash=self.stable_prefix_hash(),
            cache_scope=self.effective_cache_scope().value,
            adapter_hint_keys=sorted(self.adapter_hints.keys()),
            optimization=optimization,
        )
        metrics = get_metrics()
        strategy = self.stage.value
        metrics.record(
            "seocho.context.candidate_token_count",
            candidate_tokens,
            {"strategy": strategy},
        )
        metrics.record(
            "seocho.context.selected_token_count",
            selected_tokens,
            {"strategy": strategy},
        )
        metrics.record(
            "seocho.context.item_count",
            len(candidates),
            {"strategy": strategy, "state": "candidate"},
        )
        metrics.record(
            "seocho.context.item_count",
            len(selected),
            {"strategy": strategy, "state": "selected"},
        )
        if token_budget and selected_tokens > token_budget:
            metrics.add(
                "seocho.context.budget_exceeded.count",
                attributes={"strategy": strategy},
            )
        return receipt

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
    "PromptBackendCapabilities",
    "BUILTIN_PROMPT_BACKENDS",
    "PromptOptimizationReceipt",
    "PromptSection",
    "PromptSectionKind",
    "PromptStability",
    "PromptCacheScope",
    "PromptSource",
    "PromptStage",
    "SEMANTIC_PROMPT_PRECEDENCE",
    "StagePromptSpec",
]
