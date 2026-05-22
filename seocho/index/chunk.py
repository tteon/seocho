"""Chunk dataclass — preserves source attribution through the indexing pipeline.

Replaces the previous bare-``list[str]`` chunk representation. Each chunk now
carries a deterministic ``chunk_id`` plus character offsets into the source
document, so downstream stages (extraction, dedup, derivable-property
injection) can attribute every node/edge back to the chunks it came from.

The legacy :func:`seocho.index.pipeline.chunk_text` is preserved as a thin
back-compat shim that returns only the text strings.

chunk_id format matches the existing on-disk convention used by
:class:`seocho.index.IndexingPipeline` chunk records, so this refactor does
not migrate existing graph data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


CHUNK_ID_FORMAT = "{source_id}_chunk_{ordinal:04d}"


@dataclass(frozen=True)
class Chunk:
    """One unit of chunked text with source attribution.

    Attributes
    ----------
    chunk_id:
        Deterministic id of the form ``{source_id}_chunk_{ordinal:04d}``.
    text:
        The chunk content as it will be sent to the extractor.
    ordinal:
        Zero-based chunk index within the source document.
    char_start, char_end:
        Half-open ``[char_start, char_end)`` offsets into the original
        document. ``-1`` for either field means the offset could not be
        determined (chunk text not locatable in the source — should not
        happen for the standard paragraph chunker but may for fallback
        chunking paths).
    token_count:
        Whitespace-token count of ``text``, populated by the chunker.
    """

    chunk_id: str
    text: str
    ordinal: int
    char_start: int
    char_end: int
    token_count: Optional[int] = None


def build_chunk_id(source_id: str, ordinal: int) -> str:
    """Return the canonical deterministic chunk_id for ``(source_id, ordinal)``."""
    return CHUNK_ID_FORMAT.format(source_id=source_id, ordinal=ordinal)


def chunk(
    text: str,
    *,
    source_id: str,
    max_chars: int = 6000,
    overlap_chars: int = 200,
    separator: str = "\n\n",
) -> List[Chunk]:
    """Split ``text`` into :class:`Chunk` instances with char offsets.

    Behavior parity with the legacy ``chunk_text`` function: same paragraph
    split, same overlap policy, same ``max_chars`` envelope. The only
    semantic addition is that char offsets are attached to each chunk
    during construction rather than being recovered post-hoc.

    Returns
    -------
    list of :class:`Chunk`. A single-element list is returned for short
    inputs (including the empty string).
    """
    bodies = _split_bodies(
        text,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        separator=separator,
    )
    offsets = _locate_bodies(text, bodies)
    return [
        Chunk(
            chunk_id=build_chunk_id(source_id, i),
            text=body,
            ordinal=i,
            char_start=start,
            char_end=end,
            token_count=_rough_token_count(body),
        )
        for i, (body, (start, end)) in enumerate(zip(bodies, offsets))
    ]


def _split_bodies(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
    separator: str,
) -> List[str]:
    """Pure chunker — identical algorithm to the legacy ``chunk_text``."""
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
            chunk_body = separator.join(current)
            chunks.append(chunk_body)

            overlap_text = chunk_body[-overlap_chars:] if overlap_chars > 0 else ""
            current = [overlap_text] if overlap_text else []
            current_len = len(overlap_text)

        current.append(para)
        current_len += len(para) + len(separator)

    if current:
        chunks.append(separator.join(current))

    return chunks if chunks else [text]


def _locate_bodies(content: str, bodies: List[str]) -> List[tuple[int, int]]:
    """Find each body's ``(char_start, char_end)`` in ``content``.

    Uses a forward-search cursor with a 512-char lookback (same heuristic as
    the previous ``_estimate_chunk_offsets`` helper) so overlapping chunks
    can still be located. Empty bodies and unlocatable chunks get
    ``(-1, -1)``.
    """
    offsets: List[tuple[int, int]] = []
    search_start = 0
    for body in bodies:
        if not body:
            offsets.append((-1, -1))
            continue
        start = content.find(body, max(search_start - 512, 0))
        if start < 0:
            start = content.find(body)
        end = start + len(body) if start >= 0 else -1
        offsets.append((start, end))
        if end >= 0:
            search_start = end
    return offsets


def _rough_token_count(text: str) -> Optional[int]:
    if not text:
        return 0
    return len(text.split())


__all__ = ["Chunk", "chunk", "build_chunk_id", "CHUNK_ID_FORMAT"]
