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
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


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

    Parameters
    ----------
    ontology:
        The ontology driving extraction and validation.
    graph_store:
        Target graph database.
    llm:
        LLM backend for extraction and linking.
    workspace_id:
        Tenant scope.
    strict_validation:
        If True, reject chunks that fail SHACL validation instead of
        writing them with warnings.
    max_chunk_chars:
        Maximum characters per chunk for long documents.
    enable_dedup:
        Check content hash before indexing to skip duplicates.
    """

    def __init__(
        self,
        *,
        ontology: Any,
        graph_store: Any,
        llm: Any,
        workspace_id: str = "default",
        strict_validation: bool = False,
        max_chunk_chars: int = 6000,
        enable_dedup: bool = True,
    ) -> None:
        from .ontology import Ontology
        from .prompt_strategy import ExtractionStrategy, LinkingStrategy

        self.ontology: Ontology = ontology
        self.graph_store = graph_store
        self.llm = llm
        self.workspace_id = workspace_id
        self.strict_validation = strict_validation
        self.max_chunk_chars = max_chunk_chars
        self.enable_dedup = enable_dedup
        self._seen_hashes: set = set()

        self._extraction = ExtractionStrategy(ontology)
        self._linking = LinkingStrategy(ontology)

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
        chunks = chunk_text(content, max_chars=self.max_chunk_chars)
        all_nodes: List[Dict[str, Any]] = []
        all_rels: List[Dict[str, Any]] = []

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
                extracted = response.json()
            except Exception as exc:
                logger.error("Extraction failed for chunk %d: %s", i, exc)
                result.skipped_chunks += 1
                continue

            nodes = extracted.get("nodes", [])
            rels = extracted.get("relationships", [])

            if not nodes and not rels:
                result.skipped_chunks += 1
                continue

            # Validate with SHACL
            errors = self.ontology.validate_with_shacl(extracted)
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
                    linked = link_response.json()
                    nodes = linked.get("nodes", nodes)
                    rels = linked.get("relationships", rels)
                except Exception as exc:
                    logger.warning("Linking failed for chunk %d, using raw extraction: %s", i, exc)

            all_nodes.extend(nodes)
            all_rels.extend(rels)
            result.chunks_processed += 1

        # Cross-chunk dedup: merge nodes with same label+name
        all_nodes = self._cross_chunk_dedup(all_nodes)

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
