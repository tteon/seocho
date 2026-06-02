"""Standardized indexing-metadata schema for nodes & edges.

Closes seocho-hpml (MVP).

This module formalises the 7-category property model documented in
``examples/teaching/chapter-01-property-design.md``:

    Identity · Provenance · Trust · Lineage · Temporal · Tenancy · Performance

It does *not* yet replace the IndexingPipeline write path; it provides:

1. **Constants** — the canonical property names so all writers agree.
2. :func:`provenance_stamp` — produce a dict suitable for the MENTIONS edge
   (``extraction_run_id``, ``prompt_version``, ``ontology_slice_hash``,
   ``extracted_by``, ``extracted_at``).
3. :func:`required_provenance_fields` — schema-doc accessor.
4. :func:`check_record_completeness` — given a MENTIONS edge ``properties``
   dict, return missing required fields (used by callbacks / tests).

Downstream code (callbacks passed to :class:`seocho.index.pipeline.IndexingPipeline`,
or post-write audits) can now reference one definition instead of re-inventing
field names. Full pipeline integration is tracked under seocho-hpml's follow-up.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional


# ---------------------------------------------------------------------------
# Property name constants  (single source of truth)
# ---------------------------------------------------------------------------


class SourceField:
    """Property names on ``(:Source)`` nodes."""

    ID = "source_id"
    URI = "uri"
    MIME_TYPE = "mime_type"
    TITLE = "title"
    AUTHOR = "author"
    PUBLISHED_AT = "published_at"
    LANGUAGE = "language"
    CATEGORY = "category"
    WORKSPACE_ID = "workspace_id"
    INGESTED_AT = "ingested_at"
    INGESTED_BY = "ingested_by"
    CHECKSUM = "checksum"
    VERSION = "version"
    PARENT_SOURCE_ID = "parent_source_id"
    TAGS = "tags"


class ChunkField:
    """Property names on ``(:Chunk)`` nodes."""

    ID = "chunk_id"
    ORDINAL = "ordinal"
    TEXT = "text"
    CHAR_START = "char_start"
    CHAR_END = "char_end"
    TOKEN_COUNT = "token_count"
    CHUNKER = "chunker"
    CHUNKER_VERSION = "chunker_version"
    EMBEDDING_MODEL = "embedding_model"
    EMBEDDING_VECTOR_ID = "embedding_vector_id"
    QUALITY_SCORE = "quality_score"
    LANGUAGE = "language"


class EntityField:
    """Property names on ontology-class entity nodes."""

    ID = "entity_id"
    NAME = "name"
    ALIASES = "aliases"
    CLASS = "class"
    DESCRIPTION = "description"
    FIRST_SEEN_AT = "first_seen_at"
    LAST_SEEN_AT = "last_seen_at"
    MENTION_COUNT = "mention_count"
    COMMUNITY_ID = "community_id"
    DEGREE = "degree"
    EXTERNAL_IDS = "external_ids"
    VALIDATED = "validated"
    VALIDATED_BY = "validated_by"
    VALIDATED_AT = "validated_at"


class MentionsField:
    """Property names on the ``[:MENTIONS]`` edge."""

    EVIDENCE_SPAN = "evidence_span"
    CHAR_START = "char_start"
    CHAR_END = "char_end"
    CONFIDENCE = "confidence"
    EXTRACTED_BY = "extracted_by"
    EXTRACTED_AT = "extracted_at"
    EXTRACTION_RUN_ID = "extraction_run_id"
    PROMPT_VERSION = "prompt_version"
    ONTOLOGY_SLICE_HASH = "ontology_slice_hash"
    AGREED_BY = "agreed_by"
    ROLE = "role"


class RelatedToField:
    """Property names on entity-entity ontology relations."""

    CONFIDENCE = "confidence"
    EVIDENCE_CHUNKS = "evidence_chunks"
    EXTRACTED_BY = "extracted_by"
    EXTRACTED_AT = "extracted_at"
    VALIDATED_BY = "validated_by"
    TEMPORAL_RANGE = "temporal_range"
    WEIGHT = "weight"


REQUIRED_MENTIONS_FIELDS: tuple[str, ...] = (
    MentionsField.EXTRACTED_BY,
    MentionsField.EXTRACTED_AT,
    MentionsField.EXTRACTION_RUN_ID,
    MentionsField.PROMPT_VERSION,
)


def required_provenance_fields() -> tuple[str, ...]:
    """Stable list of fields every :class:`MentionsField` write SHOULD include."""
    return REQUIRED_MENTIONS_FIELDS


# ---------------------------------------------------------------------------
# Stamping helpers
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    """Captures the *who/when/what version* for one extraction run."""

    extraction_run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    extracted_by: str = "unknown"
    prompt_version: str = "v0"
    ontology_slice_hash: str = ""
    workspace_id: Optional[str] = None

    @classmethod
    def make(
        cls,
        *,
        model: str,
        prompt: str | bytes,
        ontology_repr: str | bytes,
        workspace_id: Optional[str] = None,
        prompt_version: Optional[str] = None,
    ) -> "RunContext":
        """Convenience builder that hashes the prompt + ontology slice."""
        return cls(
            extracted_by=model,
            prompt_version=prompt_version or _short_hash(prompt, prefix="p"),
            ontology_slice_hash=_short_hash(ontology_repr, prefix="o"),
            workspace_id=workspace_id,
        )


def provenance_stamp(
    ctx: RunContext,
    *,
    confidence: Optional[float] = None,
    evidence_span: Optional[str] = None,
    char_start: Optional[int] = None,
    char_end: Optional[int] = None,
    role: Optional[str] = None,
    agreed_by: Optional[Iterable[str]] = None,
) -> dict:
    """Return a dict ready to be written on a ``[:MENTIONS]`` edge.

    Always includes the required provenance fields; optional fields are
    only set when caller supplies them.
    """
    out: dict = {
        MentionsField.EXTRACTED_BY: ctx.extracted_by,
        MentionsField.EXTRACTED_AT: datetime.now(timezone.utc).isoformat(),
        MentionsField.EXTRACTION_RUN_ID: ctx.extraction_run_id,
        MentionsField.PROMPT_VERSION: ctx.prompt_version,
    }
    if ctx.ontology_slice_hash:
        out[MentionsField.ONTOLOGY_SLICE_HASH] = ctx.ontology_slice_hash
    if confidence is not None:
        out[MentionsField.CONFIDENCE] = float(confidence)
    if evidence_span is not None:
        out[MentionsField.EVIDENCE_SPAN] = evidence_span
    if char_start is not None:
        out[MentionsField.CHAR_START] = int(char_start)
    if char_end is not None:
        out[MentionsField.CHAR_END] = int(char_end)
    if role is not None:
        out[MentionsField.ROLE] = role
    if agreed_by:
        out[MentionsField.AGREED_BY] = sorted(set(agreed_by))
    return out


def check_record_completeness(properties: Mapping[str, Any]) -> list[str]:
    """Return the list of required provenance fields *missing* from ``properties``."""
    return [f for f in REQUIRED_MENTIONS_FIELDS if not properties.get(f)]


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _short_hash(payload: str | bytes, *, prefix: str = "h", length: int = 12) -> str:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return f"{prefix}-{hashlib.sha256(data).hexdigest()[:length]}"


__all__ = [
    "SourceField",
    "ChunkField",
    "EntityField",
    "MentionsField",
    "RelatedToField",
    "REQUIRED_MENTIONS_FIELDS",
    "required_provenance_fields",
    "RunContext",
    "provenance_stamp",
    "check_record_completeness",
]
