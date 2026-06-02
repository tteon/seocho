"""
Indexing pipeline — ontology-driven document chunking, extraction,
validation, deduplication, and graph writing.

This module handles the **construction** side of the knowledge graph.
It takes raw text, splits it into manageable chunks, extracts entities
and relationships using ontology-aware prompts, validates against SHACL,
deduplicates, and writes to the graph store.

Usage::

    from seocho import Seocho, Ontology, NodeDef, RelDef, P
    from seocho.graph_store import Neo4jGraphStore
    from seocho.llm_backend import OpenAIBackend

    s = Seocho(ontology=onto, graph_store=store, llm=llm)

    # Single document
    s.add("Some text about entities...")

    # Batch with progress
    results = s.add_batch([
        "First document...",
        "Second document...",
    ], database="mydb")

    # With strict SHACL validation (reject invalid extractions)
    s.add("Some text", strict_validation=True)
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)
_SLUG_RE = re.compile(r"[^a-z0-9]+")

from .chunk import Chunk, build_chunk_id, chunk as _canonical_chunk
from .extraction_engine import CanonicalExtractionEngine
from .property_shaper import PropertyShaper
from ..store.llm import complete_with_task_hints


def _graph_cot_properties_enabled() -> bool:
    """ADR-0092 feature flag — opt-in until the integration milestone."""
    return os.environ.get("SEOCHO_GRAPH_COT_PROPERTIES", "").strip() in {"1", "true", "TRUE"}


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(
    text: str,
    max_chars: int = 6000,
    overlap_chars: int = 200,
    separator: str = "\n\n",
) -> List[str]:
    """Back-compat shim — split text into overlapping chunk strings.

    New code should use :func:`seocho.index.chunk.chunk` which returns
    :class:`~seocho.index.chunk.Chunk` instances with deterministic
    ``chunk_id`` and char offsets. This wrapper exists for SDK consumers
    that still expect ``list[str]``.
    """
    return [
        c.text
        for c in _canonical_chunk(
            text,
            source_id="_text",
            max_chars=max_chars,
            overlap_chars=overlap_chars,
            separator=separator,
        )
    ]


# ---------------------------------------------------------------------------
# Content hashing for dedup
# ---------------------------------------------------------------------------

def content_hash(text: str) -> str:
    """Produce a stable hash of text content for deduplication."""
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Indexing result
# ---------------------------------------------------------------------------

@dataclass
class IndexingResult:
    """Result of indexing one or more documents."""

    source_id: str = ""
    chunks_processed: int = 0
    total_nodes: int = 0
    total_relationships: int = 0
    validation_errors: List[str] = field(default_factory=list)
    write_errors: List[str] = field(default_factory=list)
    skipped_chunks: int = 0
    deduplicated: bool = False
    rule_profile: Optional[Dict[str, Any]] = None
    rule_validation_summary: Optional[Dict[str, Any]] = None
    relatedness_summary: Optional[Dict[str, Any]] = None
    semantic_artifacts: Optional[Dict[str, Any]] = None
    ontology_context: Optional[Dict[str, Any]] = None
    layered_graph_summary: Optional[Dict[str, Any]] = None
    fallback_used: bool = False
    fallback_reason: str = ""

    # Materialised extracted graph payload — the post-write graph view that the
    # caller (e.g. ``_LocalEngine.add``) can surface to users via
    # :class:`seocho.models.Memory`. Empty when no nodes survived validation.
    observed_nodes: List[Dict[str, Any]] = field(default_factory=list)
    observed_relationships: List[Dict[str, Any]] = field(default_factory=list)
    chunk_records: List[Dict[str, Any]] = field(default_factory=list)
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.write_errors) == 0 and self.chunks_processed > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "chunks_processed": self.chunks_processed,
            "total_nodes": self.total_nodes,
            "total_relationships": self.total_relationships,
            "validation_errors": self.validation_errors,
            "write_errors": self.write_errors,
            "skipped_chunks": self.skipped_chunks,
            "deduplicated": self.deduplicated,
            "ok": self.ok,
            "rule_profile": self.rule_profile,
            "rule_validation_summary": self.rule_validation_summary,
            "relatedness_summary": self.relatedness_summary,
            "semantic_artifacts": self.semantic_artifacts,
            "ontology_context": self.ontology_context,
            "layered_graph_summary": self.layered_graph_summary,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
        }


@dataclass
class BatchIndexingResult:
    """Result of batch indexing multiple documents."""

    results: List[IndexingResult] = field(default_factory=list)
    total_documents: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_documents": self.total_documents,
            "successful": self.successful,
            "failed": self.failed,
            "skipped": self.skipped,
            "ok": self.ok,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Indexing pipeline
# ---------------------------------------------------------------------------

class IndexingPipeline:
    """Ontology-driven indexing pipeline.

    Orchestrates: chunk → extract → validate → dedup → write.

    **Callbacks** (Open-Closed Principle): Inject your logic at any
    pipeline stage without modifying source code::

        def my_filter(nodes, relationships):
            # Remove nodes without a name
            nodes = [n for n in nodes if n["properties"].get("name")]
            return nodes, relationships

        pipeline = IndexingPipeline(
            ontology=onto, graph_store=store, llm=llm,
            on_after_extract=my_filter,
        )

    Available callbacks:

    - ``on_after_extract(nodes, rels) → (nodes, rels)``
    - ``on_after_validate(nodes, rels, errors) → (nodes, rels, errors)``
    - ``on_before_write(nodes, rels) → (nodes, rels)``
    - ``on_after_write(nodes, rels, summary) → None``
    """

    def __init__(
        self,
        *,
        ontology: Any,
        graph_store: Any,
        llm: Any,
        workspace_id: str = "default",
        extraction_prompt: Optional[Any] = None,
        strict_validation: bool = False,
        max_chunk_chars: int = 6000,
        enable_dedup: bool = True,
        enable_rule_constraints: bool = False,
        embedding_backend: Any = None,
        vector_store: Any = None,
        ontology_profile: str = "default",
        ontology_context_cache: Any = None,
        on_after_extract: Optional[Callable] = None,
        on_after_validate: Optional[Callable] = None,
        on_before_write: Optional[Callable] = None,
        on_after_write: Optional[Callable] = None,
    ) -> None:
        from seocho.ontology import Ontology
        from seocho.query.strategy import ExtractionStrategy, LinkingStrategy

        self.ontology: Ontology = ontology
        self.graph_store = graph_store
        self.llm = llm
        self.workspace_id = workspace_id
        self.strict_validation = strict_validation
        self.max_chunk_chars = max_chunk_chars
        self.enable_dedup = enable_dedup
        self.enable_rule_constraints = enable_rule_constraints
        self.vector_store = vector_store
        self._seen_hashes: set = set()
        self.extraction_prompt = extraction_prompt
        self.ontology_profile = str(ontology_profile or "default")

        from seocho.ontology_context import OntologyContextCache

        self._ontology_context_cache = ontology_context_cache or OntologyContextCache()

        # Embedding linker (optional — enables server-parity linking)
        self._embedding_linker = None
        if embedding_backend is not None:
            from seocho.index.linker import EmbeddingLinker
            self._embedding_linker = EmbeddingLinker(embedding_backend)

        # Callbacks
        self.on_after_extract = on_after_extract
        self.on_after_validate = on_after_validate
        self.on_before_write = on_before_write
        self.on_after_write = on_after_write

        self._extraction = ExtractionStrategy(ontology, extraction_prompt=extraction_prompt)
        self._linking = LinkingStrategy(ontology)
        self._graph_extraction = CanonicalExtractionEngine(
            ontology=ontology,
            llm=llm,
            extraction_prompt=extraction_prompt,
        )

    @staticmethod
    def _fallback_extract(text: str, *, source_id: str = "fallback") -> Dict[str, Any]:
        """Heuristic extraction when the LLM is unavailable.

        Captures capitalized multi-character tokens as ``Entity`` nodes and
        attaches them to a single ``Document`` via ``MENTIONS`` relationships.
        Used as a last-resort fallback to preserve some graph structure when
        LLM extraction fails.
        """
        import re as _re

        tokens = _re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text)
        seen: set = set()
        unique: List[str] = []
        for token in tokens:
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(token)
            if len(unique) >= 12:
                break

        doc_id = f"{source_id}_doc"
        nodes: List[Dict[str, Any]] = [
            {
                "id": doc_id,
                "label": "Document",
                "properties": {
                    "name": text[:80],
                    "source_id": source_id,
                },
            }
        ]
        relationships: List[Dict[str, Any]] = []
        for idx, name in enumerate(unique):
            ent_id = f"{source_id}_ent_{idx}"
            nodes.append(
                {"id": ent_id, "label": "Entity", "properties": {"name": name}}
            )
            relationships.append(
                {"source": doc_id, "target": ent_id, "type": "MENTIONS", "properties": {}}
            )

        return {"nodes": nodes, "relationships": relationships}

    def _normalize_extraction_payload(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize LLM extraction output into the graph write contract."""
        return self._graph_extraction.normalize_payload(extracted)

    def _normalize_node(self, raw_node: Any, index: int) -> Optional[Dict[str, Any]]:
        return self._graph_extraction._normalize_node(raw_node, index)

    def _normalize_relationship(
        self,
        raw_rel: Any,
        node_lookup: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        return self._graph_extraction._normalize_relationship(raw_rel, node_lookup)

    def _infer_node_label(self, props: Dict[str, Any]) -> str:
        return self._graph_extraction._infer_node_label(props)

    def _normalize_node_id(self, raw_id: str, label: str) -> str:
        return self._graph_extraction._normalize_node_id(raw_id, label)

    def _normalize_relationship_type(self, raw_type: str) -> str:
        return self._graph_extraction._normalize_relationship_type(raw_type)

    @staticmethod
    def _graph_payload_hash(graph_data: Dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "nodes": graph_data.get("nodes", []) or [],
                "relationships": graph_data.get("relationships", []) or [],
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _system_layer_label(label: Any) -> bool:
        return str(label or "").strip() in {"Document", "DocumentVersion", "Section", "Chunk"}

    def _match_entity_ids_to_text(
        self,
        text: str,
        nodes: Sequence[Dict[str, Any]],
    ) -> List[str]:
        content = str(text or "").strip().lower()
        if not content:
            return []
        entity_ids: List[str] = []
        seen: set[str] = set()
        for node in nodes:
            label = str(node.get("label", "")).strip()
            if self._system_layer_label(label):
                continue
            node_id = str(node.get("id", "")).strip()
            name = str(node.get("properties", {}).get("name", "")).strip().lower()
            if not node_id or not name:
                continue
            if name in content and node_id not in seen:
                seen.add(node_id)
                entity_ids.append(node_id)
        return entity_ids

    def _coerce_chunk_records(
        self,
        *,
        source_id: str,
        document_id: str,
        version_id: str,
        content: str,
        chunk_records: Optional[Sequence[Dict[str, Any]]],
        nodes: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        provided_records = [dict(item) for item in (chunk_records or []) if isinstance(item, dict)]
        if not provided_records and not content:
            return []

        if provided_records:
            normalized_records: List[Dict[str, Any]] = []
            for index, record in enumerate(
                sorted(
                    provided_records,
                    key=lambda item: int(item.get("ordinal", 0) or 0),
                )
            ):
                ordinal = int(record.get("ordinal", index) or index)
                chunk_text = str(record.get("text") or "")
                entity_ids = [
                    str(entity_id).strip()
                    for entity_id in (record.get("entity_ids") or [])
                    if str(entity_id).strip()
                ]
                if not entity_ids and chunk_text:
                    entity_ids = self._match_entity_ids_to_text(chunk_text, nodes)
                normalized_records.append(
                    {
                        "chunk_id": str(record.get("chunk_id") or build_chunk_id(source_id, ordinal)),
                        "document_id": str(record.get("document_id") or document_id),
                        "version_id": str(record.get("version_id") or version_id),
                        "ordinal": ordinal,
                        "text": chunk_text,
                        "char_start": record.get("char_start"),
                        "char_end": record.get("char_end"),
                        "token_count": int(record.get("token_count") or len(chunk_text.split())),
                        "embedding_vector_id": str(record.get("embedding_vector_id") or build_chunk_id(source_id, ordinal)),
                        "embedding_model": str(record.get("embedding_model") or ""),
                        "embeddingText": str(record.get("embeddingText") or chunk_text),
                        "entity_ids": entity_ids,
                        "section_path": str(record.get("section_path") or ""),
                        "section_title": str(record.get("section_title") or ""),
                        "section_level": record.get("section_level"),
                    }
                )
            return normalized_records

        normalized_records = []
        for chunk_obj in _canonical_chunk(
            content,
            source_id=source_id,
            max_chars=self.max_chunk_chars,
        ):
            normalized_records.append(
                {
                    "chunk_id": chunk_obj.chunk_id,
                    "document_id": document_id,
                    "version_id": version_id,
                    "ordinal": chunk_obj.ordinal,
                    "text": chunk_obj.text,
                    "char_start": chunk_obj.char_start,
                    "char_end": chunk_obj.char_end,
                    "token_count": chunk_obj.token_count
                    if chunk_obj.token_count is not None
                    else len(chunk_obj.text.split()),
                    "embedding_vector_id": chunk_obj.chunk_id,
                    "embedding_model": "",
                    "embeddingText": chunk_obj.text,
                    "entity_ids": self._match_entity_ids_to_text(chunk_obj.text, nodes),
                    "section_path": chunk_obj.section_path,
                    "section_title": chunk_obj.section_title,
                    "section_level": chunk_obj.section_level,
                }
            )
        return normalized_records

    def _build_record_metadata(
        self,
        *,
        source_id: str,
        document_id: str,
        version_id: str,
        category: str,
        source_type: str,
        metadata: Optional[Dict[str, Any]],
        checksum: str,
    ) -> Dict[str, Any]:
        from seocho.index.runtime_memory import build_record_metadata

        return build_record_metadata(
            source_id=source_id,
            category=category,
            source_type=source_type,
            content_encoding="utf-8",
            parser_metadata=None,
            user_metadata={
                **(metadata if isinstance(metadata, dict) else {}),
                "document_id": document_id,
                "version_id": version_id,
                "checksum": checksum,
            },
        )

    def _maybe_build_semantic_artifacts(self, result: IndexingResult) -> None:
        try:
            draft = self.ontology.to_semantic_artifact_draft()
            draft_dict = draft.to_dict() if hasattr(draft, "to_dict") else dict(draft)
            result.semantic_artifacts = {
                "ontology_candidate": draft_dict.get("ontology_candidate"),
                "shacl_candidate": draft_dict.get("shacl_candidate"),
                "vocabulary_candidate": draft_dict.get("vocabulary_candidate"),
                "artifact_decision": {
                    "policy": "auto",
                    "applied": "draft",
                    "status": "auto_applied",
                },
                "relatedness_summary": result.relatedness_summary,
            }
        except Exception as exc:
            logger.warning("Semantic artifact draft skipped: %s", exc)

    def _shape_and_write_graph(
        self,
        *,
        result: IndexingResult,
        all_nodes: List[Dict[str, Any]],
        all_rels: List[Dict[str, Any]],
        chunk_records: List[Dict[str, Any]],
        ontology_context: Any,
        source_id: str,
        document_id: str,
        version_id: str,
        content: str,
        database: str,
        category: str,
        metadata: Optional[Dict[str, Any]],
        checksum: str,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        from seocho.index.runtime_memory import ensure_memory_graph
        from seocho.ontology_context import apply_ontology_context_to_graph_payload

        source_type = "text"
        if isinstance(metadata, dict):
            source_type = str(metadata.get("source_type") or "text")

        if all_nodes or all_rels:
            try:
                record_metadata = self._build_record_metadata(
                    source_id=source_id,
                    document_id=document_id,
                    version_id=version_id,
                    category=category,
                    source_type=source_type,
                    metadata=metadata,
                    checksum=checksum,
                )
                shaped = ensure_memory_graph(
                    graph_data={"nodes": all_nodes, "relationships": all_rels},
                    source_id=source_id,
                    workspace_id=self.workspace_id,
                    text=content,
                    category=category,
                    source_type=source_type,
                    record_metadata=record_metadata,
                    chunk_records=chunk_records,
                )
                all_nodes = shaped.get("nodes", all_nodes)
                all_rels = shaped.get("relationships", all_rels)
                result.layered_graph_summary = shaped.get("layered_graph_summary")
            except Exception as exc:
                logger.warning("Memory graph shaping skipped: %s", exc)

        if not (all_nodes or all_rels):
            return all_nodes, all_rels

        all_nodes, all_rels = apply_ontology_context_to_graph_payload(
            all_nodes,
            all_rels,
            ontology_context,
        )

        if _graph_cot_properties_enabled():
            shaper = PropertyShaper()
            for node in all_nodes:
                props = node.get("properties") or {}
                props.setdefault("id", node.get("id"))
                props.setdefault("name", props.get("name") or node.get("id"))
                node["properties"] = shaper.shape_node(props)
            for rel in all_rels:
                edge_type = str(rel.get("type") or "MENTIONS")
                rel["properties"] = shaper.shape_edge(
                    rel.get("properties") or {},
                    edge_type=edge_type,
                )

        summary = self.graph_store.write(
            all_nodes,
            all_rels,
            database=database,
            workspace_id=self.workspace_id,
            source_id=source_id,
        )
        result.total_nodes = summary.get("nodes_created", 0)
        result.total_relationships = summary.get("relationships_created", 0)
        result.write_errors = summary.get("errors", [])

        if not result.write_errors and self.vector_store is not None and chunk_records:
            try:
                vector_rows = [
                    {
                        "id": str(record["chunk_id"]),
                        "text": str(record.get("embeddingText") or record.get("text") or ""),
                        "metadata": {
                            "workspace_id": self.workspace_id,
                            "memory_id": source_id,
                            "source_id": source_id,
                            "document_id": document_id,
                            "version_id": version_id,
                            "chunk_id": str(record["chunk_id"]),
                            "ordinal": int(record.get("ordinal", 0) or 0),
                            "source_type": source_type,
                            "category": category,
                            "section_path": str(record.get("section_path") or ""),
                            "entity_ids": list(record.get("entity_ids", []) or []),
                        },
                    }
                    for record in chunk_records
                ]
                indexed = self.vector_store.add_batch(vector_rows)
                layered_summary = dict(result.layered_graph_summary or {})
                layered_summary["vector_indexed_chunks"] = int(indexed)
                result.layered_graph_summary = layered_summary
            except Exception as exc:
                result.write_errors.append(f"Vector index: {exc}")

        result.nodes = list(all_nodes)
        result.relationships = list(all_rels)

        if self.on_after_write:
            self.on_after_write(all_nodes, all_rels, summary)

        return all_nodes, all_rels

    def index(
        self,
        content: str,
        *,
        database: str = "neo4j",
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
        on_chunk: Optional[Callable[[int, int], None]] = None,
        source_id: Optional[str] = None,
    ) -> IndexingResult:
        """Index a single document (with automatic chunking).

        Parameters
        ----------
        content:
            The document text to index.
        database:
            Target database name.
        category:
            Document category (used in extraction prompts).
        metadata:
            Additional metadata to attach to nodes.
        on_chunk:
            Optional callback ``(chunk_index, total_chunks)`` for
            progress tracking.

        Returns
        -------
        IndexingResult with detailed metrics.
        """
        source_id = str(source_id or uuid.uuid4())
        result = IndexingResult(source_id=source_id)
        ontology_context = self._ontology_context_cache.get(
            self.ontology,
            workspace_id=self.workspace_id,
            profile=self.ontology_profile,
        )
        result.ontology_context = ontology_context.metadata(usage="indexing")

        # Dedup check
        if self.enable_dedup:
            h = content_hash(content)
            if h in self._seen_hashes:
                result.deduplicated = True
                result.skipped_chunks = 1
                logger.info("Skipping duplicate content (hash=%s)", h)
                return result
            self._seen_hashes.add(h)

        # Chunk
        import time as _time
        _pipeline_start = _time.time()

        chunks: List[Chunk] = _canonical_chunk(
            content,
            source_id=source_id,
            max_chars=self.max_chunk_chars,
        )
        checksum = content_hash(content)
        version_id = f"{source_id}_ver_{checksum}"
        document_id = f"{source_id}_doc"
        all_nodes: List[Dict[str, Any]] = []
        all_rels: List[Dict[str, Any]] = []
        chunk_records: List[Dict[str, Any]] = []
        _total_usage: Dict[str, int] = {}

        for i, chunk_obj in enumerate(chunks):
            chunk = chunk_obj.text
            if on_chunk:
                on_chunk(i, len(chunks))

            # Extract
            try:
                response = self._graph_extraction.extract(
                    chunk,
                    category=category,
                    metadata=metadata,
                )
                extracted = response
            except Exception as exc:
                logger.warning(
                    "LLM extraction failed for chunk %d, using heuristic fallback: %s",
                    i, exc,
                )
                # Heuristic fallback — capture capitalized tokens as Entity
                # nodes so the chunk produces *some* graph structure even
                # without LLM access.  Marks the result as fallback_used for
                # parity with server-side fallback_records tracking.
                extracted = self._fallback_extract(chunk, source_id=source_id)
                result.fallback_used = True
                result.fallback_reason = f"{type(exc).__name__}: {str(exc)[:200]}"

            nodes = extracted.get("nodes", [])
            rels = extracted.get("relationships", [])

            if not nodes and not rels:
                logger.warning(
                    "LLM extraction returned an empty graph for chunk %d, using heuristic fallback.",
                    i,
                )
                extracted = self._fallback_extract(chunk, source_id=source_id)
                nodes = extracted.get("nodes", [])
                rels = extracted.get("relationships", [])
                result.fallback_used = True
                result.fallback_reason = (
                    result.fallback_reason
                    or "EmptyExtraction: entity extraction returned no nodes or relationships"
                )
                if not nodes and not rels:
                    result.skipped_chunks += 1
                    continue

            # --- Callback: on_after_extract ---
            if self.on_after_extract:
                nodes, rels = self.on_after_extract(nodes, rels)

            # --- Indexing Reasoning: re-extract if quality is low ---
            score_data = self.ontology.score_extraction(extracted)
            extraction_score = score_data.get("overall", 0.0)
            quality_threshold = getattr(self, "_quality_threshold", 0.0)
            max_retries = getattr(self, "_max_retries", 0)

            if quality_threshold > 0 and extraction_score < quality_threshold and max_retries > 0:
                # Build guidance from low-scoring details
                low_nodes = [n for n in score_data.get("nodes", []) if n.get("score", 0) < 0.5]
                missing_info = []
                for n in low_nodes:
                    details = n.get("details", {})
                    if details.get("label_match", 1) == 0:
                        missing_info.append(f"Node '{n.get('id')}' has unknown label '{n.get('label')}'")
                    if details.get("property_completeness", 1) < 0.5:
                        missing_info.append(f"Node '{n.get('id')}' ({n.get('label')}) is missing required properties")

                for retry in range(max_retries):
                    guidance = (
                        f"Previous extraction scored {extraction_score:.0%}. Issues:\n"
                        + "\n".join(f"- {m}" for m in missing_info[:5])
                        + f"\n\nPlease re-extract with these corrections. "
                        f"Available types: {', '.join(self.ontology.nodes.keys())}. "
                        f"Available relationships: {', '.join(self.ontology.relationships.keys())}."
                    )
                    try:
                        retry_system, retry_user = self._extraction.render(chunk, metadata=metadata)
                        retry_system += f"\n\n{guidance}"
                        retry_response = complete_with_task_hints(
                            self.llm,
                            system=retry_system,
                            user=retry_user,
                            temperature=0.1 * (retry + 1),
                            response_format={"type": "json_object"},
                            reasoning_mode=False,
                            task_hint="json_extraction_retry",
                        )
                        retry_extracted = self._normalize_extraction_payload(retry_response.json())
                        retry_score = self.ontology.score_extraction(retry_extracted).get("overall", 0)

                        if retry_score > extraction_score:
                            nodes = retry_extracted.get("nodes", [])
                            rels = retry_extracted.get("relationships", [])
                            extracted = retry_extracted
                            extraction_score = retry_score
                            logger.info("Indexing reasoning: retry %d improved score %.0f%% → %.0f%%",
                                       retry + 1, extraction_score * 100, retry_score * 100)
                            if retry_score >= quality_threshold:
                                break
                    except Exception:
                        break

            # Validate with SHACL
            errors = self.ontology.validate_with_shacl(extracted)

            # --- Callback: on_after_validate ---
            if self.on_after_validate:
                nodes, rels, errors = self.on_after_validate(nodes, rels, errors)

            if errors:
                result.validation_errors.extend(errors)
                if self.strict_validation:
                    logger.warning("Chunk %d rejected by SHACL: %s", i, errors)
                    result.skipped_chunks += 1
                    continue

            # Link (deduplicate entities within chunk)
            if nodes:
                try:
                    linked = self._graph_extraction.link(
                        {"nodes": nodes, "relationships": rels},
                        category=category,
                    )
                    linked_nodes = linked.get("nodes", [])
                    linked_rels = linked.get("relationships", [])
                    if linked_nodes:
                        nodes = linked_nodes
                    if linked_rels:
                        rels = linked_rels
                except Exception as exc:
                    logger.warning("Linking failed for chunk %d, using raw extraction: %s", i, exc)

            chunk_records.append(
                {
                    "chunk_id": chunk_obj.chunk_id,
                    "document_id": document_id,
                    "version_id": version_id,
                    "ordinal": chunk_obj.ordinal,
                    "text": chunk_obj.text,
                    "char_start": chunk_obj.char_start,
                    "char_end": chunk_obj.char_end,
                    "token_count": chunk_obj.token_count
                    if chunk_obj.token_count is not None
                    else len(chunk_obj.text.split()),
                    "embedding_vector_id": chunk_obj.chunk_id,
                    "embeddingText": chunk_obj.text,
                    "section_path": chunk_obj.section_path,
                    "section_title": chunk_obj.section_title,
                    "section_level": chunk_obj.section_level,
                    "entity_ids": [
                        str(node.get("id", "")).strip()
                        for node in nodes
                        if str(node.get("label", "")).strip() != "Document"
                        and str(node.get("id", "")).strip()
                    ],
                }
            )

            all_nodes.extend(nodes)
            all_rels.extend(rels)
            result.chunks_processed += 1

        # Cross-chunk dedup: merge nodes with same label+name
        result.observed_nodes = copy.deepcopy(all_nodes)
        result.observed_relationships = copy.deepcopy(all_rels)
        all_nodes, canonical_id_by_original = self._cross_chunk_dedup(all_nodes)
        all_rels = self._rewrite_relationship_ids(all_rels, canonical_id_by_original)
        for record in chunk_records:
            canonical_entity_ids: List[str] = []
            seen_entity_ids: set[str] = set()
            for entity_id in record.get("entity_ids", []) or []:
                canonical_id = canonical_id_by_original.get(str(entity_id), str(entity_id))
                if canonical_id in seen_entity_ids:
                    continue
                seen_entity_ids.add(canonical_id)
                canonical_entity_ids.append(canonical_id)
            record["entity_ids"] = canonical_entity_ids
        result.chunk_records = copy.deepcopy(chunk_records)

        # --- Embedding relatedness (parity with server path) ---
        if self._embedding_linker is not None and all_nodes:
            try:
                candidate_names = {
                    str(n.get("properties", {}).get("name", "")).strip().lower()
                    for n in all_nodes
                    if n.get("label") != "Document" and n.get("properties", {}).get("name")
                }
                # For local single-document indexing, known_entities starts empty
                # (first record is "bootstrap").  The relatedness is recorded for
                # contract parity with the server, not for linking decisions.
                relatedness = self._embedding_linker.compute_relatedness(candidate_names, set())
                result.relatedness_summary = self._embedding_linker.summarize([relatedness])
            except Exception as exc:
                logger.warning("Embedding relatedness skipped: %s", exc)

        # --- Callback: on_before_write ---
        if self.on_before_write:
            all_nodes, all_rels = self.on_before_write(all_nodes, all_rels)

        # --- Rule inference & validation ---
        if self.enable_rule_constraints and all_nodes:
            try:
                from seocho.rules import infer_rules_from_graph, apply_rules_to_graph

                graph_for_rules = {"nodes": all_nodes, "relationships": all_rels}
                ruleset = infer_rules_from_graph(graph_for_rules)
                annotated = apply_rules_to_graph(graph_for_rules, ruleset)
                all_nodes = annotated.get("nodes", all_nodes)
                result.rule_profile = annotated.get("rule_profile")
                result.rule_validation_summary = annotated.get("rule_validation_summary")
            except Exception as exc:
                logger.warning("Rule inference skipped: %s", exc)

        # --- Semantic artifacts (parity with server path) ---
        # Build ontology/SHACL/vocabulary candidate payload from the active
        # ontology so local mode reports the same artifact contract as the
        # server's RuntimeRawIngestor.semantic_artifacts.
        if all_nodes:
            self._maybe_build_semantic_artifacts(result)

        # Write to graph
        if all_nodes or all_rels:
            try:
                all_nodes, all_rels = self._shape_and_write_graph(
                    result=result,
                    all_nodes=all_nodes,
                    all_rels=all_rels,
                    chunk_records=chunk_records,
                    ontology_context=ontology_context,
                    source_id=source_id,
                    document_id=document_id,
                    version_id=version_id,
                    content=content,
                    database=database,
                    category=category,
                    metadata=metadata,
                    checksum=checksum,
                )
            except Exception as exc:
                result.write_errors.append(str(exc))

        # --- Compute extraction score ---
        _pipeline_elapsed = _time.time() - _pipeline_start
        _score = 0.0
        if all_nodes or all_rels:
            try:
                _scores = self.ontology.score_extraction({"nodes": all_nodes, "relationships": all_rels})
                _score = _scores.get("overall", 0.0)
            except Exception:
                pass

        # --- Tracing ---
        try:
            from seocho.tracing import log_extraction, is_tracing_enabled
            if is_tracing_enabled():
                log_extraction(
                    text_preview=content[:200] if content else "",
                    ontology_name=self.ontology.name,
                    model=getattr(self.llm, "model", "unknown"),
                    nodes_count=result.total_nodes,
                    relationships_count=result.total_relationships,
                    score=_score,
                    validation_errors=len(result.validation_errors),
                    elapsed_seconds=_pipeline_elapsed,
                    metadata={"usage": _total_usage} if _total_usage else None,
                )
        except Exception:
            pass

        return result

    def index_graph(
        self,
        graph_data: Dict[str, Any],
        *,
        content: str = "",
        database: str = "neo4j",
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
        source_id: Optional[str] = None,
        chunk_records: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> IndexingResult:
        """Index a pre-structured graph payload under the active ontology.

        This path is for callers who already designed their graph schema and
        want SEOCHO to enforce ontology validation, provenance shaping, and
        vector/document join metadata without running text extraction.
        """

        source_id = str(source_id or uuid.uuid4())
        result = IndexingResult(source_id=source_id)
        ontology_context = self._ontology_context_cache.get(
            self.ontology,
            workspace_id=self.workspace_id,
            profile=self.ontology_profile,
        )
        result.ontology_context = ontology_context.metadata(usage="indexing")

        normalized = self._normalize_extraction_payload(graph_data or {})
        all_nodes = list(normalized.get("nodes", []) or [])
        all_rels = list(normalized.get("relationships", []) or [])
        if self.on_after_extract:
            all_nodes, all_rels = self.on_after_extract(all_nodes, all_rels)

        validation_payload = {"nodes": all_nodes, "relationships": all_rels}
        result.observed_nodes = copy.deepcopy(all_nodes)
        result.observed_relationships = copy.deepcopy(all_rels)
        errors = self.ontology.validate_with_shacl(validation_payload)
        if self.on_after_validate:
            all_nodes, all_rels, errors = self.on_after_validate(all_nodes, all_rels, errors)
        if errors:
            result.validation_errors.extend(errors)
            if self.strict_validation:
                result.skipped_chunks = 1
                return result

        if not all_nodes and not all_rels:
            result.write_errors.append("Structured graph payload produced no nodes or relationships.")
            return result

        all_nodes, canonical_id_by_original = self._cross_chunk_dedup(all_nodes)
        all_rels = self._rewrite_relationship_ids(all_rels, canonical_id_by_original)

        canonical_content = str(content or "").strip()
        chunk_body = self._coerce_chunk_records(
            source_id=source_id,
            document_id=f"{source_id}_doc",
            version_id="",
            content=canonical_content,
            chunk_records=chunk_records,
            nodes=all_nodes,
        )
        if not canonical_content and chunk_body:
            canonical_content = "\n\n".join(
                str(record.get("text") or "").strip()
                for record in chunk_body
                if str(record.get("text") or "").strip()
            ).strip()
        if not canonical_content:
            canonical_content = "\n".join(
                str(node.get("properties", {}).get("name") or node.get("id") or "").strip()
                for node in all_nodes
                if str(node.get("properties", {}).get("name") or node.get("id") or "").strip()
            )

        checksum = (
            content_hash(canonical_content)
            if canonical_content.strip()
            else self._graph_payload_hash(validation_payload)
        )
        document_id = f"{source_id}_doc"
        version_id = f"{source_id}_ver_{checksum}"
        resolved_chunk_records = self._coerce_chunk_records(
            source_id=source_id,
            document_id=document_id,
            version_id=version_id,
            content=canonical_content,
            chunk_records=chunk_records,
            nodes=all_nodes,
        )
        for record in resolved_chunk_records:
            canonical_entity_ids: List[str] = []
            seen_entity_ids: set[str] = set()
            for entity_id in record.get("entity_ids", []) or []:
                canonical_id = canonical_id_by_original.get(str(entity_id), str(entity_id))
                if not canonical_id or canonical_id in seen_entity_ids:
                    continue
                seen_entity_ids.add(canonical_id)
                canonical_entity_ids.append(canonical_id)
            record["entity_ids"] = canonical_entity_ids
        result.chunk_records = copy.deepcopy(resolved_chunk_records)

        result.chunks_processed = len(resolved_chunk_records) if resolved_chunk_records else 1

        if self.on_before_write:
            all_nodes, all_rels = self.on_before_write(all_nodes, all_rels)

        if self.enable_rule_constraints and all_nodes:
            try:
                from seocho.rules import infer_rules_from_graph, apply_rules_to_graph

                graph_for_rules = {"nodes": all_nodes, "relationships": all_rels}
                ruleset = infer_rules_from_graph(graph_for_rules)
                annotated = apply_rules_to_graph(graph_for_rules, ruleset)
                all_nodes = annotated.get("nodes", all_nodes)
                result.rule_profile = annotated.get("rule_profile")
                result.rule_validation_summary = annotated.get("rule_validation_summary")
            except Exception as exc:
                logger.warning("Rule inference skipped: %s", exc)

        if all_nodes:
            self._maybe_build_semantic_artifacts(result)

        try:
            all_nodes, all_rels = self._shape_and_write_graph(
                result=result,
                all_nodes=all_nodes,
                all_rels=all_rels,
                chunk_records=resolved_chunk_records,
                ontology_context=ontology_context,
                source_id=source_id,
                document_id=document_id,
                version_id=version_id,
                content=canonical_content,
                database=database,
                category=category,
                metadata=metadata,
                checksum=checksum,
            )
        except Exception as exc:
            result.write_errors.append(str(exc))

        return result

    def index_batch(
        self,
        documents: Sequence[str],
        *,
        database: str = "neo4j",
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
        on_document: Optional[Callable[[int, int], None]] = None,
    ) -> BatchIndexingResult:
        """Index multiple documents.

        Parameters
        ----------
        documents:
            List of document texts.
        database:
            Target database name.
        category:
            Document category.
        metadata:
            Metadata applied to all documents.
        on_document:
            Optional callback ``(doc_index, total_docs)`` for progress.

        Returns
        -------
        BatchIndexingResult with per-document results.
        """
        batch = BatchIndexingResult(total_documents=len(documents))

        for i, doc in enumerate(documents):
            if on_document:
                on_document(i, len(documents))

            result = self.index(
                doc, database=database,
                category=category, metadata=metadata,
            )

            batch.results.append(result)
            if result.deduplicated:
                batch.skipped += 1
            elif result.ok:
                batch.successful += 1
            else:
                batch.failed += 1

        return batch

    @staticmethod
    def _cross_chunk_dedup(
        nodes: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        """Merge nodes across chunks that have the same label + name.

        Returns the deduplicated nodes and a map from original node id to the
        canonical surviving node id.
        """
        seen: Dict[str, Dict[str, Any]] = {}  # (label, name) -> merged node
        deduped: List[Dict[str, Any]] = []
        canonical_id_by_original: Dict[str, str] = {}

        for node in nodes:
            label = node.get("label", "")
            props = node.get("properties", {})
            name = props.get("name", "")
            key = f"{label}::{name}" if name else ""
            node_id = str(node.get("id", "")).strip()

            if key and key in seen:
                # Merge properties (later values override)
                existing = seen[key]
                existing_props = existing.get("properties", {})
                existing_props.update(props)
                existing["properties"] = existing_props
                if node_id:
                    canonical_id_by_original[node_id] = str(existing.get("id", node_id))
            elif key:
                seen[key] = dict(node)
                deduped.append(seen[key])
                if node_id:
                    canonical_id_by_original[node_id] = node_id
            else:
                deduped.append(node)
                if node_id:
                    canonical_id_by_original[node_id] = node_id

        return deduped, canonical_id_by_original

    @staticmethod
    def _rewrite_relationship_ids(
        relationships: List[Dict[str, Any]],
        canonical_id_by_original: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        rewritten: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for rel in relationships:
            source = canonical_id_by_original.get(str(rel.get("source", "")), str(rel.get("source", "")))
            target = canonical_id_by_original.get(str(rel.get("target", "")), str(rel.get("target", "")))
            rel_type = str(rel.get("type", "RELATED_TO")).strip() or "RELATED_TO"
            if not source or not target:
                continue
            key = (source, target, rel_type)
            if key in seen:
                continue
            seen.add(key)
            rewritten.append(
                {
                    **rel,
                    "source": source,
                    "target": target,
                    "type": rel_type,
                }
            )
        return rewritten

    # ------------------------------------------------------------------
    # Incremental indexing
    # ------------------------------------------------------------------

    def reindex(
        self,
        source_id: str,
        content: str,
        *,
        database: str = "neo4j",
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IndexingResult:
        """Re-index a document: delete old data, then index fresh.

        Parameters
        ----------
        source_id:
            The source_id of the previously indexed document.
        content:
            The (possibly updated) document text.
        database:
            Target database.
        category:
            Document category.
        metadata:
            Additional metadata.

        Returns
        -------
        IndexingResult for the new indexing pass.
        """
        # 1. Remove old data
        delete_summary = self.delete_source(source_id, database=database)
        logger.info(
            "Reindex: deleted %d nodes, %d rels for source_id=%s",
            delete_summary.get("nodes_deleted", 0),
            delete_summary.get("relationships_deleted", 0),
            source_id,
        )

        # 2. Remove from dedup cache (allow re-indexing same content)
        h = content_hash(content)
        self._seen_hashes.discard(h)

        # 3. Index fresh
        result = self.index(
            content,
            database=database,
            category=category,
            metadata=metadata,
            source_id=source_id,
        )
        return result

    def delete_source(
        self,
        source_id: str,
        *,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        """Delete all graph data associated with a source_id.

        Parameters
        ----------
        source_id:
            The provenance identifier to remove.
        database:
            Target database.

        Returns
        -------
        Summary with ``nodes_deleted``, ``relationships_deleted``.
        """
        return self.graph_store.delete_by_source(source_id, database=database)
