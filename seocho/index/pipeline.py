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
        self._seen_hashes: set = set()
        self.extraction_prompt = extraction_prompt

        # Callbacks
        self.on_after_extract = on_after_extract
        self.on_after_validate = on_after_validate
        self.on_before_write = on_before_write
        self.on_after_write = on_after_write

        self._extraction = ExtractionStrategy(ontology, prompt_template=extraction_prompt)
        self._linking = LinkingStrategy(ontology)

    def _normalize_extraction_payload(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize LLM extraction output into the graph write contract."""
        raw_nodes = list(extracted.get("nodes", []) or [])
        raw_relationships = list(extracted.get("relationships", []) or [])
        raw_triples = list(extracted.get("triples", []) or [])

        nodes: List[Dict[str, Any]] = []
        node_lookup: Dict[str, str] = {}
        for index, raw_node in enumerate(raw_nodes):
            normalized = self._normalize_node(raw_node, index)
            if not normalized:
                continue
            nodes.append(normalized)
            props = normalized.get("properties", {})
            for key in (
                str(normalized.get("id", "")),
                str(props.get("name", "")),
                str(props.get("uri", "")),
            ):
                if key:
                    node_lookup[key] = str(normalized["id"])

        relationships: List[Dict[str, Any]] = []
        for raw_rel in (raw_relationships or raw_triples):
            normalized = self._normalize_relationship(raw_rel, node_lookup)
            if normalized:
                relationships.append(normalized)

        return {"nodes": nodes, "relationships": relationships}

    def _normalize_node(self, raw_node: Any, index: int) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_node, dict):
            return None

        if isinstance(raw_node.get("properties"), dict):
            props = dict(raw_node.get("properties", {}))
        else:
            props = {
                key: value
                for key, value in raw_node.items()
                if key not in {"id", "label", "properties", "from", "to", "source", "target", "type", "predicate"}
            }

        label = str(raw_node.get("label") or "").strip() or self._infer_node_label(props)
        if not label:
            return None

        node_id = raw_node.get("id") or props.get("id") or props.get("uri") or props.get("name") or f"{label}_{index+1}"
        normalized_id = self._normalize_node_id(str(node_id), label)
        clean_props = {key: value for key, value in props.items() if value not in (None, "") and key != "id"}
        if "name" not in clean_props and raw_node.get("name"):
            clean_props["name"] = raw_node["name"]

        return {"id": normalized_id, "label": label, "properties": clean_props}

    def _normalize_relationship(
        self,
        raw_rel: Any,
        node_lookup: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_rel, dict):
            return None

        raw_source = str(raw_rel.get("source") or raw_rel.get("from") or raw_rel.get("subject") or "").strip()
        raw_target = str(raw_rel.get("target") or raw_rel.get("to") or raw_rel.get("object") or "").strip()
        raw_type = str(raw_rel.get("type") or raw_rel.get("predicate") or raw_rel.get("relationship") or "").strip()
        if not raw_source or not raw_target or not raw_type:
            return None

        rel_type = self._normalize_relationship_type(raw_type)
        if not rel_type:
            return None

        source_id = node_lookup.get(raw_source, raw_source)
        target_id = node_lookup.get(raw_target, raw_target)
        if not source_id or not target_id:
            return None

        properties = {
            key: value
            for key, value in raw_rel.items()
            if key not in {"source", "target", "from", "to", "subject", "object", "type", "predicate", "relationship"}
            and value not in (None, "")
        }
        return {"source": source_id, "target": target_id, "type": rel_type, "properties": properties}

    def _infer_node_label(self, props: Dict[str, Any]) -> str:
        prop_keys = {key for key, value in props.items() if value not in (None, "")}
        best_label = ""
        best_score = -1
        for label, node_def in self.ontology.nodes.items():
            schema_keys = set(node_def.properties.keys())
            score = len(prop_keys & schema_keys)
            if "name" in prop_keys and "name" in schema_keys:
                score += 1
            if score > best_score:
                best_label = label
                best_score = score
        return best_label

    def _normalize_node_id(self, raw_id: str, label: str) -> str:
        if "://" in raw_id or raw_id.startswith("urn:"):
            return raw_id
        slug = _SLUG_RE.sub("_", raw_id.lower()).strip("_")
        return slug or f"{label.lower()}_{uuid.uuid4().hex[:8]}"

    def _normalize_relationship_type(self, raw_type: str) -> str:
        if raw_type == "rdf:type":
            return ""

        direct = str(raw_type).strip()
        if direct in self.ontology.relationships:
            return direct

        direct_lower = direct.lower()
        tail = direct_lower.split(":")[-1].split("/")[-1].split("__")[-1]
        for rel_name, rel_def in self.ontology.relationships.items():
            candidates = {rel_name.lower(), *(alias.lower() for alias in rel_def.aliases)}
            if rel_def.same_as:
                same_as = rel_def.same_as.lower()
                candidates.add(same_as)
                candidates.add(same_as.split(":")[-1].split("/")[-1])
            if direct_lower in candidates or tail in candidates:
                return rel_name
        return direct

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
        source_id = str(uuid.uuid4())
        result = IndexingResult(source_id=source_id)

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

        chunks = chunk_text(content, max_chars=self.max_chunk_chars)
        all_nodes: List[Dict[str, Any]] = []
        all_rels: List[Dict[str, Any]] = []
        _total_usage: Dict[str, int] = {}

        for i, chunk in enumerate(chunks):
            if on_chunk:
                on_chunk(i, len(chunks))

            # Extract
            self._extraction.category = category
            system, user = self._extraction.render(chunk, metadata=metadata)
            try:
                response = self.llm.complete(
                    system=system, user=user,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )
                extracted = self._normalize_extraction_payload(response.json())
                # Collect token usage
                if hasattr(response, 'usage') and response.usage:
                    for k, v in response.usage.items():
                        _total_usage[k] = _total_usage.get(k, 0) + v
            except Exception as exc:
                logger.error("Extraction failed for chunk %d: %s", i, exc)
                result.skipped_chunks += 1
                continue

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
                        retry_system, retry_user = self._extraction.render(
                            chunk, metadata=metadata,
                        )
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
                    entities_json = json.dumps({"nodes": nodes, "relationships": rels}, default=str)
                    self._linking.category = category
                    sys_l, usr_l = self._linking.render(entities_json)
                    link_response = self.llm.complete(
                        system=sys_l, user=usr_l,
                        temperature=0.0,
                        response_format={"type": "json_object"},
                    )
                    linked = self._normalize_extraction_payload(link_response.json())
                    nodes = linked.get("nodes", nodes)
                    rels = linked.get("relationships", rels)
                except Exception as exc:
                    logger.warning("Linking failed for chunk %d, using raw extraction: %s", i, exc)

            all_nodes.extend(nodes)
            all_rels.extend(rels)
            result.chunks_processed += 1

        # Cross-chunk dedup: merge nodes with same label+name
        all_nodes = self._cross_chunk_dedup(all_nodes)

        # --- Callback: on_before_write ---
        if self.on_before_write:
            all_nodes, all_rels = self.on_before_write(all_nodes, all_rels)

        # Write to graph
        if all_nodes or all_rels:
            try:
                summary = self.graph_store.write(
                    all_nodes, all_rels,
                    database=database,
                    workspace_id=self.workspace_id,
                    source_id=source_id,
                )
                result.total_nodes = summary.get("nodes_created", 0)
                result.total_relationships = summary.get("relationships_created", 0)
                result.write_errors = summary.get("errors", [])

                # --- Callback: on_after_write ---
                if self.on_after_write:
                    self.on_after_write(all_nodes, all_rels, summary)

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
