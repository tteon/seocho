from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent_config import AgentConfig
from .models import JsonSerializable
from .ontology import Ontology
from .ontology_context import compile_ontology_context
from .query.strategy import PromptTemplate


class OntologyHashStabilityError(RuntimeError):
    """Raised when a runtime bundle ontology's context hash drifts across the to_dict/from_dict round trip."""

_PORTABLE_AGENT_CONFIG_KEYS = {
    "extraction_strategy",
    "extraction_quality_threshold",
    "extraction_retry_on_low_quality",
    "extraction_max_retries",
    "linking_strategy",
    "validation_on_fail",
    "query_strategy",
    "answer_style",
    "reasoning_mode",
    "repair_budget",
    "routing",
}


@dataclass(slots=True)
class PortablePromptTemplate(JsonSerializable):
    system: str
    user: str = "{{text}}"

    @classmethod
    def from_prompt_template(cls, prompt_template: Any) -> "PortablePromptTemplate":
        system = str(getattr(prompt_template, "system", "")).strip()
        user = str(getattr(prompt_template, "user", "{{text}}")).strip() or "{{text}}"
        if not system:
            raise ValueError("Portable runtime bundles require a prompt template with a non-empty system prompt.")
        return cls(system=system, user=user)

    def to_prompt_template(self) -> PromptTemplate:
        return PromptTemplate(system=self.system, user=self.user)


@dataclass(slots=True)
class RuntimeLLMConfig(JsonSerializable):
    kind: str = "openai_compatible"
    provider: str = "openai"
    model: str = "gpt-4o"
    base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RuntimeLLMConfig":
        raw_kind = str(payload.get("kind", "openai_compatible")).strip() or "openai_compatible"
        raw_provider = str(payload.get("provider", "")).strip() or "openai"
        if raw_kind == "openai":
            raw_kind = "openai_compatible"
            raw_provider = "openai"
        return cls(
            kind=raw_kind,
            provider=raw_provider,
            model=str(payload.get("model", "gpt-4o")).strip() or "gpt-4o",
            base_url=str(payload.get("base_url", "")).strip(),
            api_key_env=str(payload.get("api_key_env", "OPENAI_API_KEY")).strip() or "OPENAI_API_KEY",
        )


@dataclass(slots=True)
class RuntimeGraphStoreConfig(JsonSerializable):
    kind: str = "neo4j"
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password_env: str = "NEO4J_PASSWORD"
    default_database: str = "neo4j"

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RuntimeGraphStoreConfig":
        return cls(
            kind=str(payload.get("kind", "neo4j")).strip() or "neo4j",
            uri=str(payload.get("uri", "bolt://localhost:7687")).strip() or "bolt://localhost:7687",
            user=str(payload.get("user", "neo4j")).strip() or "neo4j",
            password_env=str(payload.get("password_env", "NEO4J_PASSWORD")).strip() or "NEO4J_PASSWORD",
            default_database=str(payload.get("default_database", "neo4j")).strip() or "neo4j",
        )


@dataclass(slots=True)
class RuntimeGraphBinding(JsonSerializable):
    graph_id: str
    database: str
    ontology_id: str
    graph_model: str = "lpg"
    uri: str = ""
    description: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RuntimeGraphBinding":
        return cls(
            graph_id=str(payload.get("graph_id", "")).strip(),
            database=str(payload.get("database", "")).strip(),
            ontology_id=str(payload.get("ontology_id", "")).strip(),
            graph_model=str(payload.get("graph_model", "lpg")).strip() or "lpg",
            uri=str(payload.get("uri", "")).strip(),
            description=str(payload.get("description", "")).strip(),
        )

    def to_public_dict(self, *, workspace_id: str) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "database": self.database,
            "uri": self.uri,
            "ontology_id": self.ontology_id,
            "vocabulary_profile": "portable.bundle.v1",
            "description": self.description or f"Portable runtime graph for {self.database}.",
            "workspace_scope": workspace_id,
        }


@dataclass(slots=True)
class RuntimeBundle(JsonSerializable):
    schema_version: str = "sdk_runtime_bundle.v1"
    app_name: str = "seocho-app"
    workspace_id: str = "default"
    ontology: Dict[str, Any] = field(default_factory=dict)
    ontology_registry: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    llm: RuntimeLLMConfig = field(default_factory=RuntimeLLMConfig)
    graph_store: RuntimeGraphStoreConfig = field(default_factory=RuntimeGraphStoreConfig)
    agent_config: Dict[str, Any] = field(default_factory=dict)
    extraction_prompt: Optional[PortablePromptTemplate] = None
    graphs: List[RuntimeGraphBinding] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RuntimeBundle":
        extraction_prompt = payload.get("extraction_prompt")
        return cls(
            schema_version=str(payload.get("schema_version", "sdk_runtime_bundle.v1")).strip() or "sdk_runtime_bundle.v1",
            app_name=str(payload.get("app_name", "seocho-app")).strip() or "seocho-app",
            workspace_id=str(payload.get("workspace_id", "default")).strip() or "default",
            ontology=dict(payload.get("ontology", {})),
            ontology_registry={
                str(key): dict(value)
                for key, value in payload.get("ontology_registry", {}).items()
                if str(key).strip() and isinstance(value, dict)
            },
            llm=RuntimeLLMConfig.from_dict(payload.get("llm", {})),
            graph_store=RuntimeGraphStoreConfig.from_dict(payload.get("graph_store", {})),
            agent_config=dict(payload.get("agent_config", {})),
            extraction_prompt=(
                PortablePromptTemplate(**extraction_prompt)
                if isinstance(extraction_prompt, dict)
                else None
            ),
            graphs=[
                RuntimeGraphBinding.from_dict(item)
                for item in payload.get("graphs", [])
                if isinstance(item, dict)
            ],
        )

    @property
    def default_database(self) -> str:
        return self.graph_store.default_database

    def save(self, path: str | Path) -> Path:
        output_path = Path(path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path

    @classmethod
    def load(cls, path: str | Path) -> "RuntimeBundle":
        bundle_path = Path(path).expanduser().resolve()
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Runtime bundle must be a JSON object: {bundle_path}")
        return cls.from_dict(payload)


def build_runtime_bundle(
    client: Any,
    *,
    app_name: Optional[str] = None,
    default_database: str = "neo4j",
) -> RuntimeBundle:
    if not getattr(client, "_local_mode", False):
        raise RuntimeError("Runtime bundles can only be exported from local engine mode.")

    agent_config = getattr(client, "agent_config", None)
    if isinstance(agent_config, AgentConfig):
        if agent_config.custom_indexing_strategy is not None or agent_config.custom_query_strategy is not None:
            raise ValueError(
                "Portable runtime bundles cannot include custom Python indexing/query strategies. "
                "Use declarative AgentConfig options only."
            )
        agent_config_payload = {
            key: value
            for key, value in agent_config.to_dict().items()
            if key in _PORTABLE_AGENT_CONFIG_KEYS
        }
    elif agent_config is None:
        agent_config_payload = AgentConfig().to_dict()
    else:
        raise ValueError("Portable runtime bundles require an AgentConfig-compatible object.")

    graph_store = getattr(client, "graph_store", None)
    graph_store_kind = graph_store.__class__.__name__
    if graph_store_kind != "Neo4jGraphStore":
        raise ValueError(
            f"Portable runtime bundles currently support Neo4jGraphStore only, not {graph_store_kind}."
        )

    llm = getattr(client, "llm", None)
    llm_kind = llm.__class__.__name__
    portable_llm_kinds = {
        "OpenAICompatibleBackend",
        "OpenAIBackend",
        "DeepSeekBackend",
        "KimiBackend",
        "GrokBackend",
        "QwenBackend",
    }
    if llm_kind not in portable_llm_kinds:
        raise ValueError(
            "Portable runtime bundles currently support OpenAI-compatible SDK backends "
            f"only, not {llm_kind}."
        )

    ontology = getattr(client, "ontology", None)
    if not isinstance(ontology, Ontology):
        raise ValueError("Portable runtime bundles require a concrete Ontology instance.")

    ontology_registry = {
        database: item.to_dict()
        for database, item in getattr(client, "_ontology_registry", {}).items()
        if isinstance(database, str) and database.strip() and isinstance(item, Ontology)
    }

    extraction_prompt = getattr(client, "extraction_prompt", None)
    portable_prompt = None
    if extraction_prompt is not None:
        portable_prompt = PortablePromptTemplate.from_prompt_template(extraction_prompt)

    graphs = [
        RuntimeGraphBinding(
            graph_id=default_database,
            database=default_database,
            ontology_id=ontology.name,
            graph_model=ontology.graph_model,
            uri=str(getattr(graph_store, "_uri", "")).strip(),
            description=f"Portable runtime graph for {default_database}.",
        )
    ]
    for database, ontology_payload in sorted(ontology_registry.items()):
        ontology_name = str(ontology_payload.get("graph_type", "")).strip() or database
        graph_model = str(ontology_payload.get("graph_model", "lpg")).strip() or "lpg"
        graphs.append(
            RuntimeGraphBinding(
                graph_id=database,
                database=database,
                ontology_id=ontology_name,
                graph_model=graph_model,
                uri=str(getattr(graph_store, "_uri", "")).strip(),
                description=f"Portable runtime graph for {database}.",
            )
        )

    return RuntimeBundle(
        app_name=(app_name or getattr(ontology, "name", "") or "seocho-app").strip() or "seocho-app",
        workspace_id=str(getattr(client, "workspace_id", "default")).strip() or "default",
        ontology=ontology.to_dict(),
        ontology_registry=ontology_registry,
        llm=RuntimeLLMConfig(
            kind="openai_compatible",
            provider=str(getattr(llm, "provider", "openai")).strip() or "openai",
            model=str(getattr(llm, "model", "gpt-4o")).strip() or "gpt-4o",
            base_url=str(getattr(llm, "_base_url", "") or "").strip(),
            api_key_env=str(getattr(llm, "_api_key_env", "OPENAI_API_KEY")).strip() or "OPENAI_API_KEY",
        ),
        graph_store=RuntimeGraphStoreConfig(
            kind="neo4j",
            uri=str(getattr(graph_store, "_uri", "bolt://localhost:7687")).strip() or "bolt://localhost:7687",
            user=str(getattr(graph_store, "_user", "neo4j")).strip() or "neo4j",
            password_env="NEO4J_PASSWORD",
            default_database=default_database,
        ),
        agent_config=agent_config_payload,
        extraction_prompt=portable_prompt,
        graphs=graphs,
    )


def assert_bundle_hash_stable(bundle: RuntimeBundle) -> None:
    """Verify that every ontology in *bundle* survives a to_dict/from_dict round trip with a stable context hash.

    The runtime trusts the ``OntologyContextDescriptor.context_hash`` to prove
    that indexing, querying, and agent invocation share one ontology contract.
    Any silent drift in the serialization round trip would let the runtime
    register a hash that the SDK and graph never stamped — Phase 1 makes that
    drift fail loudly at boot rather than degrade answers later.
    """

    def _stable(payload: Dict[str, Any], *, label: str) -> None:
        if not payload:
            return
        ontology = Ontology.from_dict(payload)
        first = compile_ontology_context(
            ontology, workspace_id=bundle.workspace_id
        ).descriptor.context_hash
        ontology_round_trip = Ontology.from_dict(ontology.to_dict())
        second = compile_ontology_context(
            ontology_round_trip, workspace_id=bundle.workspace_id
        ).descriptor.context_hash
        if first != second:
            raise OntologyHashStabilityError(
                f"Ontology context hash drift detected for {label}: "
                f"first={first} second={second}"
            )

    _stable(bundle.ontology, label="bundle.ontology")
    for database, payload in bundle.ontology_registry.items():
        _stable(payload, label=f"bundle.ontology_registry[{database!r}]")


def create_client_from_runtime_bundle(bundle_source: RuntimeBundle | str | Path, *, workspace_id: Optional[str] = None) -> Any:
    from .client import Seocho
    from .store.graph import Neo4jGraphStore
    from .store.llm import OpenAIBackend, create_llm_backend

    bundle = bundle_source if isinstance(bundle_source, RuntimeBundle) else RuntimeBundle.load(bundle_source)
    if bundle.graph_store.kind != "neo4j":
        raise ValueError(f"Unsupported portable graph store kind: {bundle.graph_store.kind}")
    if bundle.llm.kind != "openai_compatible":
        raise ValueError(f"Unsupported portable LLM kind: {bundle.llm.kind}")

    assert_bundle_hash_stable(bundle)

    ontology = Ontology.from_dict(bundle.ontology)
    graph_store = Neo4jGraphStore(
        bundle.graph_store.uri,
        bundle.graph_store.user,
        os.environ.get(bundle.graph_store.password_env, "password"),
    )
    if bundle.llm.provider == "openai":
        llm = OpenAIBackend(
            model=bundle.llm.model,
            api_key=os.environ.get(bundle.llm.api_key_env),
            base_url=bundle.llm.base_url or None,
        )
    else:
        llm = create_llm_backend(
            provider=bundle.llm.provider,
            model=bundle.llm.model,
            api_key=os.environ.get(bundle.llm.api_key_env),
            base_url=bundle.llm.base_url or None,
        )
    agent_config = AgentConfig(
        **{
            key: value
            for key, value in bundle.agent_config.items()
            if key in _PORTABLE_AGENT_CONFIG_KEYS
        }
    )
    extraction_prompt = bundle.extraction_prompt.to_prompt_template() if bundle.extraction_prompt else None

    client = Seocho(
        ontology=ontology,
        graph_store=graph_store,
        llm=llm,
        workspace_id=workspace_id or bundle.workspace_id,
        extraction_prompt=extraction_prompt,
        agent_config=agent_config,
    )
    for database, ontology_payload in bundle.ontology_registry.items():
        client.register_ontology(database, Ontology.from_dict(ontology_payload))
    return client
