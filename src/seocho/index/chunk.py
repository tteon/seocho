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
import re
from typing import List, Optional


CHUNK_ID_FORMAT = "{source_id}_chunk_{ordinal:04d}"
_SECTION_HEADING_RE = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)\s*$")


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
    section_path:
        Hierarchical markdown heading path active at this chunk's starting
        offset, for example ``Overview / Risks``.
    section_title:
        Leaf section title for ``section_path``.
    section_level:
        Markdown heading depth of ``section_title`` (``1``-``6``), or ``None``
        when the document has no heading structure.
    """

    chunk_id: str
    text: str
    ordinal: int
    char_start: int
    char_end: int
    token_count: Optional[int] = None
    section_path: str = ""
    section_title: str = ""
    section_level: Optional[int] = None


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
    records = _split_with_offsets(
        text,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        separator=separator,
    )
    section_annotations = _locate_sections(text)
    return [
        _build_chunk(
            body=body,
            source_id=source_id,
            ordinal=i,
            start=start,
            end=end,
            section_annotations=section_annotations,
        )
        for i, (body, start, end) in enumerate(records)
    ]


def _build_chunk(
    *,
    body: str,
    source_id: str,
    ordinal: int,
    start: int,
    end: int,
    section_annotations: List[dict[str, object]],
) -> Chunk:
    section_path, section_title, section_level = _section_for_range(
        start,
        end,
        section_annotations,
    )
    return Chunk(
        chunk_id=build_chunk_id(source_id, ordinal),
        text=body,
        ordinal=ordinal,
        char_start=start,
        char_end=end,
        token_count=_rough_token_count(body),
        section_path=section_path,
        section_title=section_title,
        section_level=section_level,
    )


def _paragraph_spans(
    text: str,
    separator: str,
) -> List[tuple[str, int, int]]:
    """Yield ``(stripped_paragraph, char_start, char_end)`` for each non-empty
    paragraph, with offsets into the *original* ``text``.

    Splitting on a fixed separator discards positions, but they are
    recoverable: segment ``i`` begins at the running cursor, and stripping a
    paragraph only shifts its start past the leading whitespace and pulls its
    end in past the trailing whitespace. Tracking offsets here (rather than
    searching for the post-processed body afterwards) is what lets overlapped
    chunks keep correct provenance — see issue #124.
    """
    spans: List[tuple[str, int, int]] = []
    cursor = 0
    for raw in text.split(separator):
        stripped = raw.strip()
        if stripped:
            lead = len(raw) - len(raw.lstrip())
            start = cursor + lead
            spans.append((stripped, start, start + len(stripped)))
        cursor += len(raw) + len(separator)
    return spans


def _split_with_offsets(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
    separator: str,
) -> List[tuple[str, int, int]]:
    """Chunker that emits ``(body, char_start, char_end)`` per chunk.

    The body strings are byte-for-byte identical to the legacy ``chunk_text``
    output (the back-compat shim depends on this). Offsets are tracked during
    splitting from real paragraph positions instead of being recovered with a
    post-hoc ``str.find``, so they stay correct even when a chunk carries an
    overlap prefix or spans multiple paragraphs.

    ``[char_start, char_end)`` covers the chunk's *own* (non-overlap) content:
    ``char_start`` is the source start of the first paragraph this chunk
    introduces, ``char_end`` the end of its last paragraph. The leading overlap
    prefix is borrowed context whose provenance already belongs to the previous
    chunk, so the spans tile the document without gaps. Empty input maps to
    ``(-1, -1)`` to preserve the documented "offset unknown" sentinel.
    """
    if len(text) <= max_chars:
        return [(text, 0, len(text))] if text else [("", -1, -1)]

    records: List[tuple[str, int, int]] = []
    current: List[str] = []
    current_len = 0
    cur_start: Optional[int] = None  # source start of this chunk's first own paragraph
    cur_end: Optional[int] = None  # source end of the last paragraph added

    for para, p_start, p_end in _paragraph_spans(text, separator):
        if current_len + len(para) + len(separator) > max_chars and current:
            body = separator.join(current)
            records.append((body, cur_start, cur_end))

            overlap_text = body[-overlap_chars:] if overlap_chars > 0 else ""
            current = [overlap_text] if overlap_text else []
            current_len = len(overlap_text)
            cur_start = None
            cur_end = None

        if cur_start is None:
            cur_start = p_start
        cur_end = p_end
        current.append(para)
        current_len += len(para) + len(separator)

    if current and cur_start is not None:
        records.append((separator.join(current), cur_start, cur_end))

    return records if records else [(text, 0, len(text))]


def _locate_sections(text: str) -> List[dict[str, object]]:
    """Return ordered section annotations extracted from markdown headings."""
    annotations: List[dict[str, object]] = []
    stack: List[str] = []
    for match in _SECTION_HEADING_RE.finditer(text):
        level = len(match.group(1))
        title = re.sub(r"\s+", " ", match.group(2).strip())
        if not title:
            continue
        if len(stack) >= level:
            stack = stack[: level - 1]
        while len(stack) < level - 1:
            stack.append("")
        stack.append(title)
        path = " / ".join(part for part in stack if part)
        annotations.append(
            {
                "start": match.start(),
                "path": path,
                "title": title,
                "level": level,
            }
        )
    return annotations


def _section_for_range(
    start_offset: int,
    end_offset: int,
    section_annotations: List[dict[str, object]],
) -> tuple[str, str, Optional[int]]:
    if end_offset < 0 or not section_annotations:
        return "", "", None
    for annotation in reversed(section_annotations):
        annotation_start = int(annotation.get("start", -1))
        if start_offset <= annotation_start < end_offset:
            return (
                str(annotation.get("path", "")),
                str(annotation.get("title", "")),
                int(annotation["level"]) if annotation.get("level") is not None else None,
            )
    if start_offset < 0:
        return "", "", None
    for annotation in reversed(section_annotations):
        annotation_start = int(annotation.get("start", -1))
        if annotation_start <= start_offset:
            return (
                str(annotation.get("path", "")),
                str(annotation.get("title", "")),
                int(annotation["level"]) if annotation.get("level") is not None else None,
            )
    return "", "", None


def _rough_token_count(text: str) -> Optional[int]:
    if not text:
        return 0
    return len(text.split())


__all__ = ["Chunk", "chunk", "build_chunk_id", "CHUNK_ID_FORMAT"]
