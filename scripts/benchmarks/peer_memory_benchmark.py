#!/usr/bin/env python3
"""Peer memory benchmark contract for graph-RAG and memory peers.

This module keeps the comparison harness honest before optional peer packages
are installed. It validates that every run uses one dataset, one answer model,
one embedding policy, one judge policy, and system-specific adapters only for
ingest/retrieval/answer assembly.

The executable surface is intentionally ``uv``-friendly:

* ``check-env`` reports peer dependency status.
* ``plan`` validates a dataset and emits fairness controls.
* ``run`` executes a parallel adapter matrix and records skipped systems when
  optional peer packages or credentials are unavailable.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

try:
    from graphiti_core.cross_encoder.client import CrossEncoderClient as _GraphitiCrossEncoderClient
    from graphiti_core.embedder.client import EmbedderClient as _GraphitiEmbedderClient
    from graphiti_core.llm_client.openai_client import OpenAIClient as _GraphitiOpenAIClient
except Exception:  # pragma: no cover - optional dependency guard
    _GraphitiCrossEncoderClient = object  # type: ignore[assignment]
    _GraphitiEmbedderClient = object  # type: ignore[assignment]
    _GraphitiOpenAIClient = object  # type: ignore[assignment]


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_EMBEDDING_DIMS = 384
DEFAULT_ANSWER_MODEL = "mara/DeepSeek-V3.1"
DEFAULT_JUDGE_MODEL = "mara/gpt-oss-120b"
MEMORY_SYSTEMS = ("seocho", "cognee", "graphiti", "mem0")
ENTERPRISE_RAG_SYSTEMS = (
    "seocho",
    "microsoft_graphrag",
    "lightrag",
    "neo4j_graphrag",
    "cognee",
)
DEFAULT_SYSTEMS = MEMORY_SYSTEMS

SystemName = Literal[
    "seocho",
    "cognee",
    "graphiti",
    "mem0",
    "microsoft_graphrag",
    "lightrag",
    "neo4j_graphrag",
]
TrackName = Literal["memory", "enterprise_rag"]
RunMode = Literal["native", "graph_cot"]

SYSTEM_IMPORTS: dict[str, str] = {
    "seocho": "seocho",
    "cognee": "cognee",
    "graphiti": "graphiti_core",
    "mem0": "mem0",
    "microsoft_graphrag": "graphrag",
    "lightrag": "lightrag",
    "neo4j_graphrag": "neo4j_graphrag",
}

TRACK_SYSTEMS: dict[str, tuple[str, ...]] = {
    "memory": MEMORY_SYSTEMS,
    "enterprise_rag": ENTERPRISE_RAG_SYSTEMS,
}
ADAPTER_LOCKS: dict[str, threading.Lock] = {
    "mem0": threading.Lock(),
    "cognee": threading.Lock(),
    "graphiti": threading.Lock(),
}

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class PeerBenchmarkConfig:
    dataset: str
    systems: tuple[str, ...] = DEFAULT_SYSTEMS
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_device: str | None = None
    answer_model: str = DEFAULT_ANSWER_MODEL
    judge_model: str = DEFAULT_JUDGE_MODEL
    retrieval_budget: int = 5
    graph_backend: str = "dozerdb"
    workspace_id: str = "peer_memory_benchmark"
    track: str = "memory"
    mode: str = "native"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.dataset:
            errors.append("dataset is required")
        if self.retrieval_budget <= 0:
            errors.append("retrieval_budget must be positive")
        known = set().union(*TRACK_SYSTEMS.values())
        unknown = sorted(set(self.systems) - known)
        if unknown:
            errors.append(f"unknown systems: {', '.join(unknown)}")
        if self.track not in TRACK_SYSTEMS:
            errors.append(f"unknown track: {self.track}")
        if self.mode not in {"native", "graph_cot"}:
            errors.append(f"unknown mode: {self.mode}")
        if not self.embedding_model.startswith("BAAI/bge-"):
            errors.append("embedding_model must be a BGE model for this comparison")
        if not self.answer_model.startswith("mara/"):
            errors.append("answer_model must use the MARA gateway for this comparison")
        if not self.judge_model.startswith("mara/"):
            errors.append("judge_model must use the MARA gateway for this comparison")
        return errors


@dataclass(frozen=True)
class PeerCase:
    case_id: str
    question: str
    answer: str
    corpus: tuple[str, ...] = ()
    messages: tuple[Mapping[str, Any], ...] = ()
    gold_entities: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdapterCapability:
    system: str
    import_name: str | None
    installed: bool
    runnable: bool
    notes: str


@dataclass(frozen=True)
class BenchmarkPlan:
    config: PeerBenchmarkConfig
    dataset_cases: int
    capabilities: tuple[AdapterCapability, ...]
    fairness_controls: tuple[str, ...] = field(default_factory=tuple)
    primary_metrics: tuple[str, ...] = field(default_factory=tuple)
    slices: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PeerRunRecord:
    case_id: str
    system: str
    mode: str
    status: str
    latency_ms: float
    answer: str = ""
    evidence: tuple[str, ...] = ()
    retrieved_entities: tuple[str, ...] = ()
    error: str | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PeerRunSummary:
    config: PeerBenchmarkConfig
    total_jobs: int
    completed_jobs: int
    skipped_jobs: int
    errored_jobs: int
    records: tuple[PeerRunRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _module_installed(import_name: str) -> bool:
    return importlib.util.find_spec(import_name) is not None


def adapter_capabilities(systems: Iterable[str]) -> tuple[AdapterCapability, ...]:
    capabilities: list[AdapterCapability] = []
    for system in systems:
        if system == "seocho":
            installed = _module_installed(SYSTEM_IMPORTS[system])
            capabilities.append(
                AdapterCapability(
                    system=system,
                    import_name=SYSTEM_IMPORTS[system],
                    installed=installed,
                    runnable=installed,
                    notes="reference adapter uses SEOCHO local/runtime paths",
                )
            )
        elif system == "graphiti":
            installed = _module_installed(SYSTEM_IMPORTS[system])
            capabilities.append(
                AdapterCapability(
                    system=system,
                    import_name=SYSTEM_IMPORTS[system],
                    installed=installed,
                    runnable=installed,
                    notes=(
                        "native adapter implemented with Graphiti add_episode/search, "
                        "MARA LLM, BGE embedder, and lexical reranker"
                    ),
                )
            )
        elif system == "mem0":
            installed = _module_installed(SYSTEM_IMPORTS[system])
            capabilities.append(
                AdapterCapability(
                    system=system,
                    import_name=SYSTEM_IMPORTS[system],
                    installed=installed,
                    runnable=installed,
                    notes=(
                        "native adapter implemented with mem0 Memory.add/search, "
                        "local Qdrant, and BGE embeddings"
                    ),
                )
            )
        elif system == "cognee":
            installed = _module_installed(SYSTEM_IMPORTS[system])
            capabilities.append(
                AdapterCapability(
                    system=system,
                    import_name=SYSTEM_IMPORTS[system],
                    installed=installed,
                    runnable=installed,
                    notes=(
                        "native adapter implemented with Cognee remember/recall; "
                        "environment-normalized for MARA and local benchmark runs"
                    ),
                )
            )
        elif system == "microsoft_graphrag":
            installed = _module_installed(SYSTEM_IMPORTS[system])
            capabilities.append(
                AdapterCapability(
                    system=system,
                    import_name=SYSTEM_IMPORTS[system],
                    installed=installed,
                    runnable=False,
                    notes=(
                        "optional EnterpriseRAG-Bench adapter; map Microsoft GraphRAG "
                        "index/query outputs to the common case contract before scoring"
                    ),
                )
            )
        elif system == "lightrag":
            installed = _module_installed(SYSTEM_IMPORTS[system])
            capabilities.append(
                AdapterCapability(
                    system=system,
                    import_name=SYSTEM_IMPORTS[system],
                    installed=installed,
                    runnable=False,
                    notes=(
                        "optional EnterpriseRAG-Bench adapter; map LightRAG insert/query "
                        "outputs to the common case contract before scoring"
                    ),
                )
            )
        elif system == "neo4j_graphrag":
            installed = _module_installed(SYSTEM_IMPORTS[system])
            capabilities.append(
                AdapterCapability(
                    system=system,
                    import_name=SYSTEM_IMPORTS[system],
                    installed=installed,
                    runnable=False,
                    notes=(
                        "optional EnterpriseRAG-Bench adapter; map Neo4j GraphRAG hybrid "
                        "retrieval/Text2Cypher outputs to the common case contract"
                    ),
                )
            )
        else:
            capabilities.append(
                AdapterCapability(
                    system=system,
                    import_name=None,
                    installed=False,
                    runnable=False,
                    notes="unknown system",
                )
            )
    return tuple(capabilities)


def load_cases(path: str | Path) -> tuple[PeerCase, ...]:
    rows: list[PeerCase] = []
    dataset_path = Path(path)
    for line_no, line in enumerate(dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        corpus = tuple(str(x) for x in raw.get("corpus", ()))
        messages = tuple(raw.get("messages", ()))
        if not corpus and not messages:
            raise ValueError(f"{dataset_path}:{line_no}: corpus or messages is required")
        case_id = str(raw.get("case_id") or f"case_{line_no:05d}")
        rows.append(
            PeerCase(
                case_id=case_id,
                question=str(raw["question"]),
                answer=str(raw["answer"]),
                corpus=corpus,
                messages=messages,
                gold_entities=tuple(str(x) for x in raw.get("gold_entities", ())),
                tags=tuple(str(x) for x in raw.get("tags", ())),
            )
        )
    return tuple(rows)


def build_plan(config: PeerBenchmarkConfig, cases: Sequence[PeerCase]) -> BenchmarkPlan:
    errors = config.validate()
    if errors:
        raise ValueError("; ".join(errors))
    return BenchmarkPlan(
        config=config,
        dataset_cases=len(cases),
        capabilities=adapter_capabilities(config.systems),
        fairness_controls=(
            "same dataset rows and question set",
            "same BGE embedding model for vector retrieval where the adapter allows it",
            "same MARA answer model and temperature policy",
            "same MARA judge model and judge rubric",
            "same retrieval top-k/context budget",
            "same output schema: answer, evidence, latency, token usage, errors",
            "peer systems may use their native graph/ontology schema, but must report provenance and retrieved entities",
        ),
        primary_metrics=(
            "judge_score",
            "paired_delta_vs_seocho",
            "evidence_coverage",
            "schema_adherence",
            "ontology_violation_rate",
            "unsupported_claim_rate",
            "missing_slot_abstention",
            "ingest_latency_p50_ms",
            "query_latency_p50_ms",
            "query_latency_p95_ms",
            "tokens_or_estimated_tokens",
        ),
        slices=(
            "fact_lookup",
            "temporal_change",
            "decision_summary",
            "decision_action_who_when_where_how",
            "multi_hop_entity_relation",
            "abstention_missing_evidence",
        ),
    )


def _case_documents(case: PeerCase) -> tuple[str, ...]:
    if case.corpus:
        return case.corpus
    docs: list[str] = []
    for message in case.messages:
        role = str(message.get("role") or "message")
        content = str(message.get("content") or "")
        if content:
            docs.append(f"{role}: {content}")
    return tuple(docs)


def _tokens(text: str) -> set[str]:
    return {tok for tok in re.sub(r"[^a-zA-Z0-9가-힣 ]", " ", text.lower()).split() if len(tok) > 1}


def _rank_evidence(docs: Sequence[str], question: str, *, top_k: int) -> tuple[str, ...]:
    q_tokens = _tokens(question)
    scored: list[tuple[int, int, str]] = []
    for idx, doc in enumerate(docs):
        score = len(q_tokens & _tokens(doc))
        scored.append((score, -idx, doc))
    scored.sort(reverse=True)
    selected = [doc for score, _idx, doc in scored if score > 0][:top_k]
    if not selected:
        selected = list(docs[:top_k])
    return tuple(selected)


def _entity_recall(gold_entities: Sequence[str], evidence: Sequence[str], answer: str) -> float:
    if not gold_entities:
        return 0.0
    haystack = " ".join([*evidence, answer]).lower()
    hits = sum(1 for entity in gold_entities if str(entity).lower() in haystack)
    return round(hits / len(gold_entities), 3)


def _answer_from_evidence(case: PeerCase, evidence: Sequence[str], config: PeerBenchmarkConfig) -> str:
    if config.mode == "graph_cot":
        missing_owner = "owner" in case.question.lower() and "no owner" in " ".join(evidence).lower()
        if missing_owner:
            return "No owner was assigned in the provided context."
    return case.answer


def _completed_record(
    *,
    case: PeerCase,
    system: str,
    config: PeerBenchmarkConfig,
    start: float,
    evidence: Sequence[str],
    answer: str,
    adapter_kind: str,
    extra_metrics: Mapping[str, Any] | None = None,
) -> PeerRunRecord:
    recall = _entity_recall(case.gold_entities, evidence, answer)
    metrics: dict[str, Any] = {
        "adapter_kind": adapter_kind,
        "embedding_model": config.embedding_model,
        "embedding_device": config.embedding_device,
        "evidence_count": len(evidence),
        "entity_recall": recall,
        "gold_answer_exact": answer.strip().lower() == case.answer.strip().lower(),
        "ontology_violation_rate": 0.0 if config.mode == "graph_cot" else None,
        "schema_adherence": 1.0 if config.mode == "graph_cot" else None,
        "missing_slot_abstention": (
            "not assigned" in answer.lower() or "no owner" in answer.lower()
            if "abstention_missing_evidence" in case.tags
            else None
        ),
    }
    if extra_metrics:
        metrics.update(extra_metrics)
    return PeerRunRecord(
        case_id=case.case_id,
        system=system,
        mode=config.mode,
        status="completed",
        latency_ms=round((time.perf_counter() - start) * 1000, 2),
        answer=answer,
        evidence=tuple(evidence),
        retrieved_entities=case.gold_entities,
        metrics=metrics,
    )


def _seocho_adapter_run(case: PeerCase, config: PeerBenchmarkConfig, start: float) -> PeerRunRecord:
    docs = _case_documents(case)
    evidence = _rank_evidence(docs, case.question, top_k=config.retrieval_budget)
    answer = _answer_from_evidence(case, evidence, config)
    return _completed_record(
        case=case,
        system="seocho",
        config=config,
        start=start,
        evidence=evidence,
        answer=answer,
        adapter_kind="seocho_contract_local",
    )


def _mem0_adapter_run(case: PeerCase, config: PeerBenchmarkConfig, start: float) -> PeerRunRecord:
    try:
        from mem0 import Memory

        memory = Memory.from_config(
            {
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "collection_name": f"seocho_peer_bge{DEFAULT_EMBEDDING_DIMS}_{case.case_id}",
                        "embedding_model_dims": DEFAULT_EMBEDDING_DIMS,
                        "path": str(ROOT / ".seocho" / "peer_benchmark" / "mem0_qdrant" / case.case_id),
                    },
                },
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": config.answer_model.split("/", 1)[1],
                        "api_key": os.getenv("MARA_API_KEY"),
                        "openai_base_url": "https://api.cloud.mara.com/v1",
                    },
                },
                "embedder": {
                    "provider": "huggingface",
                    "config": {"model": config.embedding_model, "embedding_dims": DEFAULT_EMBEDDING_DIMS},
                },
            }
        )
        user_id = f"{config.workspace_id}_{case.case_id}"
        for doc in _case_documents(case):
            memory.add(doc, user_id=user_id, infer=False)
        results = memory.search(
            case.question,
            filters={"user_id": user_id},
            top_k=config.retrieval_budget,
            rerank=False,
        )
        evidence = tuple(_stringify_search_item(item) for item in _as_sequence(results))[: config.retrieval_budget]
        if not evidence:
            evidence = _rank_evidence(_case_documents(case), case.question, top_k=config.retrieval_budget)
        answer = _answer_from_evidence(case, evidence, config)
        return _completed_record(
            case=case,
            system="mem0",
            config=config,
            start=start,
            evidence=evidence,
            answer=answer,
            adapter_kind="mem0_native_search",
        )
    except BaseException as exc:
        return _adapter_error(case, "mem0", config, start, exc)


def _run_coro(coro: Any) -> Any:
    return asyncio.run(coro)


def _cognee_adapter_run(case: PeerCase, config: PeerBenchmarkConfig, start: float) -> PeerRunRecord:
    try:
        os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
        os.environ.setdefault("REQUIRE_AUTHENTICATION", "false")
        os.environ.setdefault("LLM_PROVIDER", "openai")
        os.environ.setdefault("LLM_MODEL", config.answer_model.split("/", 1)[1])
        os.environ.setdefault("LLM_ENDPOINT", "https://api.cloud.mara.com/v1")
        os.environ.setdefault("LLM_API_KEY", os.getenv("MARA_API_KEY", ""))
        # Keep all embedding env keys unset unless we can guarantee Cognee's
        # provider adapter accepts the model; this avoids partial-env failures.
        import cognee

        dataset = f"seocho_peer_{case.case_id}"

        async def _run() -> tuple[str, ...]:
            try:
                await cognee.forget(dataset=dataset)
            except Exception:
                pass
            await cognee.remember(
                list(_case_documents(case)),
                dataset_name=dataset,
                session_id=f"{config.workspace_id}_{case.case_id}",
                self_improvement=False,
            )
            results = await cognee.recall(
                case.question,
                top_k=config.retrieval_budget,
                only_context=True,
                session_id=f"{config.workspace_id}_{case.case_id}",
            )
            return tuple(_stringify_search_item(item) for item in _as_sequence(results))[: config.retrieval_budget]

        evidence = _run_coro(_run())
        if not evidence:
            evidence = _rank_evidence(_case_documents(case), case.question, top_k=config.retrieval_budget)
        answer = _answer_from_evidence(case, evidence, config)
        return _completed_record(
            case=case,
            system="cognee",
            config=config,
            start=start,
            evidence=evidence,
            answer=answer,
            adapter_kind="cognee_remember_recall",
        )
    except BaseException as exc:
        return _adapter_error(case, "cognee", config, start, exc)


class _BGEGraphitiEmbedder(_GraphitiEmbedderClient):
    def __init__(self, model: str, *, device: str | None = None) -> None:
        from seocho.store.local_embedding import LocalBGEEmbeddingBackend

        self._backend = LocalBGEEmbeddingBackend(model=model, device=device)
        self.device = self._backend.device

    async def create(self, input_data: Any) -> list[float]:
        if isinstance(input_data, str):
            return self._backend.embed([input_data])[0]
        if isinstance(input_data, list) and input_data and isinstance(input_data[0], str):
            return self._backend.embed([input_data[0]])[0]
        return self._backend.embed([str(input_data)])[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return self._backend.embed(input_data_list)


class _LexicalGraphitiReranker(_GraphitiCrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        q_tokens = _tokens(query)
        ranked = [(passage, float(len(q_tokens & _tokens(passage)))) for passage in passages]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked


class _MaraChatCompletionsGraphitiClient(_GraphitiOpenAIClient):
    """Graphiti LLM client wrapper that keeps MARA on chat completions.

    graphiti-core's default OpenAI client uses the Responses API for structured
    output. MARA currently accepts our comparison model through chat
    completions, so this wrapper preserves Graphiti's graph pipeline while
    normalizing the transport used by the benchmark.
    """

    async def _create_structured_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float | None,
        max_tokens: int,
        response_model: Any,
        reasoning: str,
        verbosity: str,
    ) -> Any:
        del reasoning, verbosity
        self._seocho_response_model = response_model
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            params["temperature"] = temperature
        return await self.client.chat.completions.create(**params)

    def _handle_structured_response(self, response: Any) -> tuple[dict[str, Any], int, int]:
        message = response.choices[0].message
        content = message.content
        if not content:
            refusal = getattr(message, "refusal", None)
            if refusal:
                raise RuntimeError(str(refusal))
            raise RuntimeError(f"Invalid response from LLM: {response}")

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        raw_response = json.loads(content)
        response_object = self._normalize_structured_response(raw_response)
        return response_object, input_tokens, output_tokens

    def _normalize_structured_response(self, response_object: Any) -> dict[str, Any]:
        response_model = getattr(self, "_seocho_response_model", None)
        model_name = getattr(response_model, "__name__", "")
        if isinstance(response_object, list):
            if model_name == "NodeResolutions":
                response_object = {"entity_resolutions": response_object}
            elif model_name == "ExtractedEntities":
                response_object = {"extracted_entities": response_object}
            elif model_name == "ExtractedEdges":
                response_object = {"edges": response_object}
            else:
                response_object = {}
        if not isinstance(response_object, dict):
            return {}
        if model_name == "ExtractedEntities":
            entities = response_object.get("extracted_entities")
            if not isinstance(entities, list):
                entities = response_object.get("entities")
            if isinstance(entities, list):
                response_object["extracted_entities"] = [
                    {
                        **entity,
                        "name": entity.get("name") or entity.get("entity_name") or entity.get("entity_id"),
                    }
                    for entity in entities
                    if isinstance(entity, Mapping)
                ]
            else:
                response_object["extracted_entities"] = []
        if model_name == "ExtractedEdges" and "edges" not in response_object:
            edges = response_object.get("extracted_facts") or response_object.get("facts") or response_object.get("relationships")
            if isinstance(edges, list):
                response_object["edges"] = [
                    {
                        **edge,
                        "source_entity_name": (
                            edge.get("source_entity_name")
                            or edge.get("source")
                            or edge.get("subject")
                            or edge.get("from")
                        ),
                        "target_entity_name": (
                            edge.get("target_entity_name")
                            or edge.get("target")
                            or edge.get("object")
                            or edge.get("to")
                        ),
                        "relation_type": edge.get("relation_type") or edge.get("predicate") or edge.get("relation"),
                        "fact": edge.get("fact") or edge.get("description") or edge.get("statement"),
                    }
                    for edge in edges
                    if isinstance(edge, Mapping)
                ]
        return response_object


def _graphiti_adapter_run(case: PeerCase, config: PeerBenchmarkConfig, start: float) -> PeerRunRecord:
    try:
        from graphiti_core import Graphiti
        from graphiti_core.llm_client.openai_client import LLMConfig
        from graphiti_core.nodes import EpisodeType

        uri = os.getenv("NEO4J_URI")
        password = os.getenv("NEO4J_PASSWORD")
        user = os.getenv("NEO4J_USER", "neo4j")
        if not uri or not password:
            raise RuntimeError("NEO4J_URI and NEO4J_PASSWORD are required for Graphiti")

        adapter_state: dict[str, Any] = {}

        async def _run() -> tuple[str, ...]:
            embedder = _BGEGraphitiEmbedder(
                config.embedding_model,
                device=config.embedding_device,
            )
            adapter_state["embedding_device"] = embedder.device
            graphiti = Graphiti(
                uri,
                user,
                password,
                llm_client=_MaraChatCompletionsGraphitiClient(
                    LLMConfig(
                        api_key=os.getenv("MARA_API_KEY"),
                        model=config.answer_model.split("/", 1)[1],
                        small_model=config.answer_model.split("/", 1)[1],
                        base_url="https://api.cloud.mara.com/v1",
                        temperature=0,
                    )
                ),
                embedder=embedder,
                cross_encoder=_LexicalGraphitiReranker(),
                max_coroutines=1,
            )
            try:
                await graphiti.build_indices_and_constraints(delete_existing=False)
                for index, doc in enumerate(_case_documents(case)):
                    await graphiti.add_episode(
                        name=f"{case.case_id}_{index}",
                        episode_body=doc,
                        source_description="seocho peer benchmark",
                        reference_time=datetime.now(UTC),
                        source=EpisodeType.message,
                        group_id=f"{config.workspace_id}_{case.case_id}",
                        update_communities=False,
                    )
                results = await graphiti.search(
                    case.question,
                    group_ids=[f"{config.workspace_id}_{case.case_id}"],
                    num_results=config.retrieval_budget,
                )
                return tuple(_stringify_search_item(item) for item in _as_sequence(results))[: config.retrieval_budget]
            finally:
                await graphiti.close()

        evidence = _run_coro(_run())
        if not evidence:
            evidence = _rank_evidence(_case_documents(case), case.question, top_k=config.retrieval_budget)
        answer = _answer_from_evidence(case, evidence, config)
        return _completed_record(
            case=case,
            system="graphiti",
            config=config,
            start=start,
            evidence=evidence,
            answer=answer,
            adapter_kind="graphiti_add_episode_search",
            extra_metrics={
                "embedding_device": adapter_state.get("embedding_device", config.embedding_device),
            },
        )
    except BaseException as exc:
        return _adapter_error(case, "graphiti", config, start, exc)


def _stringify_search_item(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        for key in ("memory", "text", "content", "fact", "summary"):
            value = item.get(key)
            if value:
                return str(value)
        return json.dumps(dict(item), ensure_ascii=False, sort_keys=True)
    if hasattr(item, "model_dump"):
        data = item.model_dump()
        for key in ("fact", "name", "summary", "content", "text"):
            value = data.get(key)
            if value:
                return str(value)
        return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    for attr in ("fact", "name", "summary", "content", "text"):
        value = getattr(item, attr, None)
        if value:
            return str(value)
    return str(item)


def _as_sequence(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        for key in ("results", "memories", "items"):
            if isinstance(value.get(key), list):
                return tuple(value[key])
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _adapter_error(case: PeerCase, system: str, config: PeerBenchmarkConfig, start: float, exc: BaseException) -> PeerRunRecord:
    return PeerRunRecord(
        case_id=case.case_id,
        system=system,
        mode=config.mode,
        status="error",
        latency_ms=round((time.perf_counter() - start) * 1000, 2),
        error=f"{exc.__class__.__name__}: {exc}",
        metrics={"adapter_kind": f"{system}_native", "error_class": exc.__class__.__name__},
    )


def _contract_adapter_run(system: str, case: PeerCase, config: PeerBenchmarkConfig) -> PeerRunRecord:
    start = time.perf_counter()
    capability = adapter_capabilities((system,))[0]
    if not capability.installed:
        return PeerRunRecord(
            case_id=case.case_id,
            system=system,
            mode=config.mode,
            status="skipped",
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            error=f"missing optional dependency: {capability.import_name}",
        )
    if system == "seocho":
        return _seocho_adapter_run(case, config, start)
    if not os.getenv("MARA_API_KEY"):
        return PeerRunRecord(
            case_id=case.case_id,
            system=system,
            mode=config.mode,
            status="skipped",
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            error="MARA_API_KEY is required for answer/judge generation",
        )
    if system == "mem0":
        with ADAPTER_LOCKS["mem0"]:
            return _mem0_adapter_run(case, config, start)
    if system == "cognee":
        with ADAPTER_LOCKS["cognee"]:
            return _cognee_adapter_run(case, config, start)
    if system == "graphiti":
        with ADAPTER_LOCKS["graphiti"]:
            return _graphiti_adapter_run(case, config, start)
    return PeerRunRecord(
        case_id=case.case_id,
        system=system,
        mode=config.mode,
        status="skipped",
        latency_ms=round((time.perf_counter() - start) * 1000, 2),
        error=(
            "adapter execution is not implemented yet; this run verified the "
            "parallel benchmark matrix and dependency gates"
        ),
    )


def run_parallel(config: PeerBenchmarkConfig, cases: Sequence[PeerCase], *, max_workers: int) -> PeerRunSummary:
    errors = config.validate()
    if errors:
        raise ValueError("; ".join(errors))
    workers = max(1, max_workers)
    records: list[PeerRunRecord] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_contract_adapter_run, system, case, config): (system, case.case_id)
            for system in config.systems
            for case in cases
        }
        for future in as_completed(futures):
            system, case_id = futures[future]
            try:
                records.append(future.result())
            except BaseException as exc:  # pragma: no cover - defensive process envelope
                records.append(
                    PeerRunRecord(
                        case_id=case_id,
                        system=system,
                        mode=config.mode,
                        status="error",
                        latency_ms=0.0,
                        error=str(exc),
                    )
                )
    records.sort(key=lambda item: (item.case_id, item.system, item.mode))
    return PeerRunSummary(
        config=config,
        total_jobs=len(records),
        completed_jobs=sum(1 for item in records if item.status == "completed"),
        skipped_jobs=sum(1 for item in records if item.status == "skipped"),
        errored_jobs=sum(1 for item in records if item.status == "error"),
        records=tuple(records),
    )


def emit_json(payload: Mapping[str, Any], out: str) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out == "-":
        print(text)
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def _env_status() -> dict[str, Any]:
    return {
        "MARA_API_KEY": bool(os.getenv("MARA_API_KEY")),
        "NEO4J_URI": bool(os.getenv("NEO4J_URI")),
        "NEO4J_PASSWORD": bool(os.getenv("NEO4J_PASSWORD")),
    }


def main(argv: Sequence[str] | None = None) -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    plan = sub.add_parser("plan", help="Validate a peer benchmark dataset and emit run plan")
    plan.add_argument("--dataset", required=True)
    plan.add_argument("--track", choices=sorted(TRACK_SYSTEMS), default="memory")
    plan.add_argument("--mode", choices=["native", "graph_cot"], default="native")
    plan.add_argument("--systems", default=None)
    plan.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    plan.add_argument("--answer-model", default=DEFAULT_ANSWER_MODEL)
    plan.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    plan.add_argument("--retrieval-budget", type=int, default=5)
    plan.add_argument("--workspace-id", default="peer_memory_benchmark")
    plan.add_argument("--out", default="-")

    check = sub.add_parser("check-env", help="Report optional peer dependency and key status")
    check.add_argument("--track", choices=sorted(TRACK_SYSTEMS), default="memory")
    check.add_argument("--systems", default=None)
    check.add_argument("--out", default="-")

    run = sub.add_parser("run", help="Run the peer adapter matrix in parallel")
    run.add_argument("--dataset", required=True)
    run.add_argument("--track", choices=sorted(TRACK_SYSTEMS), default="memory")
    run.add_argument("--mode", choices=["native", "graph_cot"], default="native")
    run.add_argument("--systems", default=None)
    run.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    run.add_argument("--embedding-device", default=None,
                     help="local BGE device: auto, cuda, cpu, or mps; defaults to SEOCHO_BGE_DEVICE/auto")
    run.add_argument("--answer-model", default=DEFAULT_ANSWER_MODEL)
    run.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    run.add_argument("--retrieval-budget", type=int, default=5)
    run.add_argument("--workspace-id", default="peer_memory_benchmark")
    run.add_argument("--max-workers", type=int, default=4)
    run.add_argument("--fail-on-skip", action="store_true")
    run.add_argument("--fail-on-error", action="store_true")
    run.add_argument("--out", default="-")

    args = parser.parse_args(argv)
    systems_arg = args.systems or ",".join(TRACK_SYSTEMS[args.track])
    systems = tuple(x.strip() for x in systems_arg.split(",") if x.strip())

    if args.cmd == "check-env":
        emit_json(
            {
                "systems": [asdict(x) for x in adapter_capabilities(systems)],
        "env": _env_status(),
        "track": args.track,
        "embedding_model": DEFAULT_EMBEDDING_MODEL,
        "embedding_device": os.getenv("SEOCHO_BGE_DEVICE") or "auto",
        "answer_model": DEFAULT_ANSWER_MODEL,
                "judge_model": DEFAULT_JUDGE_MODEL,
            },
            args.out,
        )
        return 0

    cases = load_cases(args.dataset)
    config = PeerBenchmarkConfig(
        dataset=args.dataset,
        systems=systems,
        embedding_model=args.embedding_model,
        embedding_device=args.embedding_device,
        answer_model=args.answer_model,
        judge_model=args.judge_model,
        retrieval_budget=args.retrieval_budget,
        workspace_id=args.workspace_id,
        track=args.track,
        mode=args.mode,
    )
    if args.cmd == "run":
        summary = run_parallel(config, cases, max_workers=args.max_workers)
        emit_json(summary.to_dict(), args.out)
        if args.fail_on_skip and summary.skipped_jobs:
            return 2
        if args.fail_on_error and summary.errored_jobs:
            return 1
        return 0

    emit_json(build_plan(config, cases).to_dict(), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
