"""Pure, typed execution outcomes. An answered question is not a quality grade."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, TypedDict
from collections.abc import Mapping

Stage = Literal["preflight", "build", "index", "query", "cleanup", "evidence"]
Status = Literal["running", "completed", "partial", "failed", "interrupted"]


class Outcome(TypedDict):
    status: Status
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class RunDiagnostic:
    stage: Stage
    code: str
    message: str
    action: str
    item_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def query_state(record: Mapping[str, Any]) -> str:
    if record.get("skipped"):
        return "skipped"
    if record.get("error"):
        return "error"
    if record.get("empty") or not str(record.get("answer") or "").strip():
        return "empty"
    return "answered"


def summarize_outcome(payload: Mapping[str, Any]) -> Outcome:
    reasons: list[str] = []
    if payload.get("fatal_error"):
        return {"status": "failed", "reasons": [str(payload["fatal_error"]["code"])]}
    index = payload.get("indexing")
    usable = 0
    if index is not None:
        found = int(index.get("files_found", 0) or 0)
        usable = int(index.get("files_indexed", 0) or 0) + int(
            index.get("files_unchanged", 0) or 0
        )
        if found == 0 or usable == 0:
            reasons.append("no_usable_documents")
        if int(index.get("files_failed", 0) or 0):
            reasons.append("indexing_failed")
        if int(index.get("files_skipped", 0) or 0):
            reasons.append("documents_skipped")
    records = payload.get("queries", [])
    for state in ("error", "empty", "skipped"):
        if any(query_state(record) == state for record in records):
            reasons.append(f"query_{state}")
    if payload.get("cleanup_errors"):
        reasons.append("cleanup_failed")
    if not reasons:
        return {"status": "completed", "reasons": []}
    useful = usable > 0 or any(query_state(record) == "answered" for record in records)
    return {"status": "partial" if useful else "failed", "reasons": reasons}
