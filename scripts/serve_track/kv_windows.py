"""Absolute per-step time windows, so KV-cache events can be attributed to a stage.

Trace schema v1 (`scripts/pattern_traces/schema.py`) records `latency_ms` per
step but no absolute start time, and it was deliberately frozen in #486. vLLM's
KV events carry no request id either — a `BlockStored` payload is
`block_hashes + parent_block_hash + token_ids + medium`, nothing that names the
caller. So neither side alone can say *which RAG stage* caused a block store.

Rather than reopen the frozen schema, this sidecar records the one fact both
sides are missing: when each step actually ran, on the same clock the KV
subscriber stamps its frames with (`time.time`). Joining is then a containment
test, done offline in `correlate_kv.py`.

The sidecar is written next to the episode file and keyed by the same
`trace_id`, so a run is one pair:

    outputs/serve_track/<run>/episodes.jsonl    # trace schema v1, untouched
    outputs/serve_track/<run>/kv_windows.jsonl  # this file
    outputs/serve_track/<run>/kv_events.jsonl   # from the vLLM subscriber

Windows are only meaningful when requests do not overlap in time. Concurrency
would let two stages' blocks interleave inside one window, so `record_step`
refuses to open a second window while one is open; the caller must serialize.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

WINDOW_SCHEMA_VERSION = 1


@dataclass(slots=True)
class StepWindow:
    """One LLM call's wall-clock extent, on `time.time`'s clock."""

    trace_id: str
    step_index: int
    role: str
    model: str
    provider: str
    t_start: float
    t_end: float = 0.0
    window_schema: int = field(default=WINDOW_SCHEMA_VERSION)
    # Provider-verbatim usage, so the API-side cache signal
    # (`prompt_tokens_details.cached_tokens`) survives next to the block-side
    # evidence without a second lookup.
    usage: Dict[str, Any] = field(default_factory=dict)
    prompt_chars: int = 0
    # Section -> chars, mirroring LLMStep.prompt_sections. This is what makes
    # the prefix story legible: a stage whose leading sections are stable
    # across calls is a stage whose KV prefix can be reused.
    prompt_sections: Dict[str, int] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.t_end - self.t_start) * 1000.0


class WindowRecorder:
    """Append-only writer for step windows, one file per run."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._open: Optional[StepWindow] = None
        self._counter: Dict[str, int] = {}

    @contextmanager
    def record_step(
        self,
        *,
        trace_id: str,
        role: str,
        model: str,
        provider: str,
        prompt_chars: int = 0,
        prompt_sections: Optional[Dict[str, int]] = None,
    ) -> Iterator[StepWindow]:
        """Open a window around one LLM call and append it on exit.

        The window closes even when the call raises, because a failed call
        still consumed cache; dropping it would silently bias attribution.
        """
        if self._open is not None:
            raise RuntimeError(
                f"window for {self._open.role!r} is still open; KV attribution "
                "requires serialized calls (see module docstring)"
            )
        index = self._counter.get(trace_id, 0)
        self._counter[trace_id] = index + 1
        window = StepWindow(
            trace_id=trace_id,
            step_index=index,
            role=role,
            model=model,
            provider=provider,
            t_start=time.time(),
            prompt_chars=prompt_chars,
            prompt_sections=dict(prompt_sections or {}),
        )
        self._open = window
        try:
            yield window
        finally:
            window.t_end = time.time()
            self._open = None
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(window), ensure_ascii=False) + "\n")


def read_windows(path: str | os.PathLike[str]) -> list[Dict[str, Any]]:
    """Read a window sidecar, refusing to mix schema generations."""
    records: list[Dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("window_schema") != WINDOW_SCHEMA_VERSION:
                raise ValueError(
                    f"window_schema {record.get('window_schema')} != "
                    f"{WINDOW_SCHEMA_VERSION}; refusing to silently mix generations"
                )
            records.append(record)
    return records
