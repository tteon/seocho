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

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)
_SLUG_RE = re.compile(r"[^a-z0-9]+")

from .extraction_engine import CanonicalExtractionEngine


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(
    text: str,
    max_chars: int = 6000,
    overlap_chars: int = 200,
    separator: str = "\n\n",
) -> List[str]:
    """Split text into overlapping chunks for extraction.

    Strategy:
    1. Split on ``separator`` (paragraph breaks by default)
    2. Merge paragraphs until ``max_chars`` is reached
    3. Add ``overlap_chars`` from previous chunk to preserve context

    Parameters
    ----------
    text:
        The full document text.
    max_chars:
        Maximum characters per chunk (~1500 tokens at 4 chars/token).
    overlap_chars:
        Characters to repeat from previous chunk for context continuity.
    separator:
        Primary split point (paragraph breaks).

    Returns
    -------
    List of text chunks. Single-chunk list if text is short enough.
    """
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split(separator)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if current_len + len(para) + len(separator) > max_chars and current:
            chunk_text_str = separator.join(current)
            chunks.append(chunk_text_str)

            # Overlap: keep last portion of current chunk
            overlap_text = chunk_text_str[-overlap_chars:] if overlap_chars > 0 else ""
            current = [overlap_text] if overlap_text else []
            current_len = len(overlap_text)

        current.append(para)
        current_len += len(para) + len(separator)

    if current:
        chunks.append(separator.join(current))

    return chunks if chunks else [text]


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
    semantic_package: Optional[Dict[str, Any]] = None
    stage_metrics: Dict[str, Any] = field(default_factory=dict)
    policy_metrics: Dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str = ""

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
            "semantic_package": self.semantic_package,
            "stage_metrics": self.stage_metrics,
            "policy_metrics": self.policy_metrics,
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

    def index(
        self,
        content: str,
        *,
        database: str = "neo4j",
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
        on_chunk: Optional[Callable[[int, int], None]] = None,
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
        import time as _time

        source_id = str(uuid.uuid4())
        result = IndexingResult(source_id=source_id)
        stage_metrics: Dict[str, float] = {}
        stage_started = _time.perf_counter()
        ontology_context = self._ontology_context_cache.get(
            self.ontology,
            workspace_id=self.workspace_id,
            profile=self.ontology_profile,
        )
        result.ontology_context = ontology_context.metadata(usage="indexing")
        stage_metrics["ontology_context_ms"] = round((_time.perf_counter() - stage_started) * 1000.0, 2)
        try:
            from seocho.semantic_package import compile_semantic_package

            stage_started = _time.perf_counter()
            result.semantic_package = compile_semantic_package(
                ontology_context,
                graph_id=database,
                database=database,
                source="ontology_context",
            ).to_dict()
            stage_metrics["semantic_package_ms"] = round((_time.perf_counter() - stage_started) * 1000.0, 2)
        except Exception:
            logger.debug("Semantic package compilation skipped for indexing trace.", exc_info=True)

        # Dedup check
        if self.enable_dedup:
            h = content_hash(content)
            if h in self._seen_hashes:
                result.deduplicated = True
                result.skipped_chunks = 1
                logger.info("Skipping duplicate content (hash=%s)", h)
                return result
            self._seen_hashes.add(h)

        _pipeline_start = _time.time()

        stage_started = _time.perf_counter()
        chunks = chunk_text(content, max_chars=self.max_chunk_chars)
        stage_metrics["chunking_ms"] = round((_time.perf_counter() - stage_started) * 1000.0, 2)
        all_nodes: List[Dict[str, Any]] = []
        all_rels: List[Dict[str, Any]] = []
        _total_usage: Dict[str, int] = {}
        extraction_ms = 0.0
        validation_ms = 0.0
        linking_ms = 0.0

        for i, chunk in enumerate(chunks):
            if on_chunk:
                on_chunk(i, len(chunks))

            # Extract
            try:
                extraction_started = _time.perf_counter()
                response = self._graph_extraction.extract(
                    chunk,
                    category=category,
                    metadata=metadata,
                )
                extracted = response
                extraction_ms += (_time.perf_counter() - extraction_started) * 1000.0
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
                        retry_response = self.llm.complete(
                            system=retry_system, user=retry_user,
                            temperature=0.1 * (retry + 1),
                            response_format={"type": "json_object"},
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
            validation_started = _time.perf_counter()
            errors = self.ontology.validate_with_shacl(extracted)
            validation_ms += (_time.perf_counter() - validation_started) * 1000.0

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
                    linking_started = _time.perf_counter()
                    linked = self._graph_extraction.link(
                        {"nodes": nodes, "relationships": rels},
                        category=category,
                    )
                    linking_ms += (_time.perf_counter() - linking_started) * 1000.0
                    linked_nodes = linked.get("nodes", [])
                    linked_rels = linked.get("relationships", [])
                    if linked_nodes:
                        nodes = linked_nodes
                    if linked_rels:
                        rels = linked_rels
                except Exception as exc:
                    logger.warning("Linking failed for chunk %d, using raw extraction: %s", i, exc)

            all_nodes.extend(nodes)
            all_rels.extend(rels)
            result.chunks_processed += 1

        # Cross-chunk dedup: merge nodes with same label+name
        stage_started = _time.perf_counter()
        all_nodes = self._cross_chunk_dedup(all_nodes)
        stage_metrics["cross_chunk_dedup_ms"] = round((_time.perf_counter() - stage_started) * 1000.0, 2)
        stage_metrics["extraction_ms"] = round(extraction_ms, 2)
        stage_metrics["validation_ms"] = round(validation_ms, 2)
        stage_metrics["linking_ms"] = round(linking_ms, 2)

        # --- Embedding relatedness (parity with server path) ---
        if self._embedding_linker is not None and all_nodes:
            try:
                stage_started = _time.perf_counter()
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
                stage_metrics["relatedness_ms"] = round((_time.perf_counter() - stage_started) * 1000.0, 2)
            except Exception as exc:
                logger.warning("Embedding relatedness skipped: %s", exc)

        # --- Callback: on_before_write ---
        if self.on_before_write:
            all_nodes, all_rels = self.on_before_write(all_nodes, all_rels)

        # --- Rule inference & validation ---
        if self.enable_rule_constraints and all_nodes:
            try:
                from seocho.rules import infer_rules_from_graph, apply_rules_to_graph

                stage_started = _time.perf_counter()
                graph_for_rules = {"nodes": all_nodes, "relationships": all_rels}
                ruleset = infer_rules_from_graph(graph_for_rules)
                annotated = apply_rules_to_graph(graph_for_rules, ruleset)
                all_nodes = annotated.get("nodes", all_nodes)
                result.rule_profile = annotated.get("rule_profile")
                result.rule_validation_summary = annotated.get("rule_validation_summary")
                stage_metrics["rule_inference_ms"] = round((_time.perf_counter() - stage_started) * 1000.0, 2)
            except Exception as exc:
                logger.warning("Rule inference skipped: %s", exc)

        # --- Semantic artifacts (parity with server path) ---
        # Build ontology/SHACL/vocabulary candidate payload from the active
        # ontology so local mode reports the same artifact contract as the
        # server's RuntimeRawIngestor.semantic_artifacts.
        if all_nodes:
            try:
                stage_started = _time.perf_counter()
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
                stage_metrics["semantic_artifact_ms"] = round((_time.perf_counter() - stage_started) * 1000.0, 2)
            except Exception as exc:
                logger.warning("Semantic artifact draft skipped: %s", exc)

        # --- Memory graph shaping ---
        if all_nodes or all_rels:
            try:
                from seocho.index.runtime_memory import build_record_metadata, ensure_memory_graph

                stage_started = _time.perf_counter()
                source_type = "text"
                if isinstance(metadata, dict):
                    source_type = str(metadata.get("source_type") or "text")
                record_metadata = build_record_metadata(
                    source_id=source_id,
                    category=category,
                    source_type=source_type,
                    content_encoding="utf-8",
                    parser_metadata=None,
                    user_metadata=metadata if isinstance(metadata, dict) else None,
                )
                shaped = ensure_memory_graph(
                    graph_data={"nodes": all_nodes, "relationships": all_rels},
                    source_id=source_id,
                    workspace_id=self.workspace_id,
                    text=content,
                    category=category,
                    source_type=source_type,
                    record_metadata=record_metadata,
                )
                all_nodes = shaped.get("nodes", all_nodes)
                all_rels = shaped.get("relationships", all_rels)
                stage_metrics["memory_graph_ms"] = round((_time.perf_counter() - stage_started) * 1000.0, 2)
            except Exception as exc:
                logger.warning("Memory graph shaping skipped: %s", exc)

        # Write to graph
        if all_nodes or all_rels:
            try:
                from seocho.ontology_context import apply_ontology_context_to_graph_payload

                stage_started = _time.perf_counter()
                all_nodes, all_rels = apply_ontology_context_to_graph_payload(
                    all_nodes,
                    all_rels,
                    ontology_context,
                )
                summary = self.graph_store.write(
                    all_nodes, all_rels,
                    database=database,
                    workspace_id=self.workspace_id,
                    source_id=source_id,
                )
                result.total_nodes = summary.get("nodes_created", 0)
                result.total_relationships = summary.get("relationships_created", 0)
                result.write_errors = summary.get("errors", [])
                stage_metrics["graph_write_ms"] = round((_time.perf_counter() - stage_started) * 1000.0, 2)

                # --- Callback: on_after_write ---
                if self.on_after_write:
                    self.on_after_write(all_nodes, all_rels, summary)

            except Exception as exc:
                result.write_errors.append(str(exc))

        # --- Compute extraction score ---
        _pipeline_elapsed = _time.time() - _pipeline_start
        stage_metrics["total_ms"] = round(_pipeline_elapsed * 1000.0, 2)
        result.stage_metrics = stage_metrics
        result.policy_metrics = {
            "mode": "indexing",
            "strict_validation": bool(self.strict_validation),
            "chunks_total": len(chunks),
            "chunks_processed": int(result.chunks_processed),
            "skipped_chunks": int(result.skipped_chunks),
            "validation_error_count": len(result.validation_errors),
            "write_error_count": len(result.write_errors),
            "fallback_used": bool(result.fallback_used),
            "deduplicated": bool(result.deduplicated),
            "nodes_created": int(result.total_nodes),
            "relationships_created": int(result.total_relationships),
        }
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
                trace_metadata: Dict[str, Any] = {}
                if _total_usage:
                    trace_metadata["usage"] = _total_usage
                if result.ontology_context is not None:
                    trace_metadata["ontology_context"] = result.ontology_context
                if result.semantic_package is not None:
                    trace_metadata["semantic_package"] = result.semantic_package
                log_extraction(
                    text_preview=content[:200] if content else "",
                    ontology_name=self.ontology.name,
                    model=getattr(self.llm, "model", "unknown"),
                    nodes_count=result.total_nodes,
                    relationships_count=result.total_relationships,
                    score=_score,
                    validation_errors=len(result.validation_errors),
                    elapsed_seconds=_pipeline_elapsed,
                    metadata=trace_metadata or None,
                )
        except Exception:
            pass

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
    def _cross_chunk_dedup(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge nodes across chunks that have the same label + name."""
        seen: Dict[str, Dict[str, Any]] = {}  # (label, name) -> merged node
        deduped: List[Dict[str, Any]] = []

        for node in nodes:
            label = node.get("label", "")
            props = node.get("properties", {})
            name = props.get("name", "")
            key = f"{label}::{name}" if name else ""

            if key and key in seen:
                # Merge properties (later values override)
                existing = seen[key]
                existing_props = existing.get("properties", {})
                existing_props.update(props)
                existing["properties"] = existing_props
            elif key:
                seen[key] = dict(node)
                deduped.append(seen[key])
            else:
                deduped.append(node)

        return deduped

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
        )
        result.source_id = source_id  # preserve original source_id
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
