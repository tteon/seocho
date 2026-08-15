"""Attribute vLLM KV-cache blocks to graph-agentic-RAG stages, offline.

Reads a run directory produced by the serve-track harness:

    kv_windows.jsonl   per-step wall-clock windows (scripts/serve_track/kv_windows.py)
    kv_events.jsonl    KV-event frames stamped with `_recv_ts` (scripts/cache_probe)

and answers, per stage: how many blocks that stage caused to be stored, how many
of its prompt's tokens the engine served from cache, and whether its prefix is
stable enough across episodes to be reusable at all.

Why a time-window join. vLLM's KV events name no request — `BlockStored` is
`block_hashes + parent_block_hash + token_ids + medium`. The API response does
carry a per-request signal (`prompt_tokens_details.cached_tokens`), but it says
nothing about *which* blocks. Containment in a step's window is the only link
between the two, and it is sound exactly when calls are serialized, which
`WindowRecorder` enforces on the producing side.

What this cannot tell you. `_recv_ts` is subscriber receive time, not engine
emit time; a frame delayed past a window boundary is attributed to the next
stage or dropped. The `unattributed` count is reported rather than spread, so
that error stays visible instead of being smoothed into the per-stage numbers.

Usage:
    python scripts/serve_track/correlate_kv.py outputs/serve_track/<run>
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_STORE_TAGS = {"BlockStored"}
_REMOVE_TAGS = {"BlockRemoved"}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _event_kind(event: Dict[str, Any]) -> str:
    """Name an event across every encoding the probe tolerates.

    Measured on 0.27.1 the decoded frame carries a plain ``type`` field; the
    probe also keeps a `_tag`/`_fields` pair for msgspec array-encoded structs,
    whose layout has moved between versions. Both shapes reach here.
    """
    tag = event.get("type") or event.get("_tag")
    if tag in _STORE_TAGS:
        return "stored"
    if tag in _REMOVE_TAGS:
        return "removed"
    if "parent_block_hash" in event or "token_ids" in event:
        return "stored"
    if "block_hashes" in event:
        # BlockRemoved carries hashes but no parent chain or tokens.
        return "removed"
    return "other"


def _block_count(event: Dict[str, Any]) -> int:
    hashes = event.get("block_hashes")
    if isinstance(hashes, list):
        return len(hashes)
    fields = event.get("_fields")
    if isinstance(fields, list):
        for value in fields:
            if isinstance(value, list) and value and isinstance(value[0], int):
                return len(value)
    return 1


def _block_size(event: Dict[str, Any]) -> int:
    """Tokens per block, as the engine reports it (16 on 0.27.1).

    This is what makes a real hit rate computable on vLLM at all: the
    completions response leaves ``cached_tokens`` unpopulated, so tokens the
    engine did not re-prefill are only knowable as blocks it did not store,
    times this.
    """
    size = event.get("block_size")
    return size if isinstance(size, int) and size > 0 else 0


def _medium(event: Dict[str, Any]) -> str:
    """Storage tier of a block — the axis KV offloading moves along.

    With offloading enabled a reused block may be recalled from CPU or disk
    instead of recomputed, and that shows up here as a non-GPU medium.
    """
    medium = event.get("medium")
    return medium if isinstance(medium, str) and medium else "unknown"


def _cached_tokens(usage: Dict[str, Any]) -> Optional[int]:
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        value = details.get("cached_tokens")
        if isinstance(value, int):
            return value
    return None


def _stable_prefix_chars(section_maps: Iterable[Dict[str, int]]) -> int:
    """Chars of leading prompt sections that are identical across every call.

    A stage's KV prefix is reusable only up to the first section whose size
    varies between episodes. Comparing sizes (not contents) is deliberate: the
    sidecar records `section -> chars`, and a section that changes length has
    certainly changed tokens.
    """
    maps = [m for m in section_maps if m]
    if not maps:
        return 0
    first = maps[0]
    stable = 0
    for name, size in first.items():
        if all(other.get(name) == size for other in maps[1:]):
            stable += size
        else:
            break
    return stable


def correlate(run_dir: Path) -> Dict[str, Any]:
    windows = _read_jsonl(run_dir / "kv_windows.jsonl")
    events = _read_jsonl(run_dir / "kv_events.jsonl")
    if not windows:
        raise SystemExit(f"no windows in {run_dir}/kv_windows.jsonl")

    windows.sort(key=lambda w: w["t_start"])
    per_role: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "blocks_stored": 0,
            "blocks_removed": 0,
            "prompt_tokens": 0,
            "cached_tokens": 0,
            "cached_tokens_reported": 0,
            "latency_ms": 0.0,
            "_sections": [],
        }
    )
    # Per-window block totals, so reuse is readable call by call. On vLLM this
    # is the *primary* cache signal: measured on 0.27.1, the completions
    # response does not populate `prompt_tokens_details.cached_tokens` at all,
    # so a repeat whose prefix was reused shows up as blocks it did not have to
    # store, not as a token count. MARA reports the token field and no blocks;
    # the two rigs are legible through different columns.
    per_window_blocks: Dict[int, int] = defaultdict(int)
    per_window_tokens: Dict[int, int] = defaultdict(int)
    per_role_medium: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    attributed = 0
    for event in events:
        ts = event.get("_recv_ts")
        if not isinstance(ts, (int, float)):
            continue
        index = next(
            (i for i, w in enumerate(windows) if w["t_start"] <= ts <= w["t_end"]), None
        )
        if index is None:
            continue
        attributed += 1
        window = windows[index]
        kind = _event_kind(event)
        bucket = per_role[window["role"]]
        if kind == "stored":
            count = _block_count(event)
            bucket["blocks_stored"] += count
            per_window_blocks[index] += count
            per_window_tokens[index] += count * _block_size(event)
            per_role_medium[window["role"]][_medium(event)] += count
        elif kind == "removed":
            bucket["blocks_removed"] += _block_count(event)

    for index, window in enumerate(windows):
        bucket = per_role[window["role"]]
        bucket.setdefault("blocks_per_call", []).append(per_window_blocks[index])
        bucket["tokens_stored"] = bucket.get("tokens_stored", 0) + per_window_tokens[index]
        bucket["calls"] += 1
        bucket["latency_ms"] += (window["t_end"] - window["t_start"]) * 1000.0
        bucket["_sections"].append(window.get("prompt_sections") or {})
        usage = window.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        if isinstance(prompt_tokens, int):
            bucket["prompt_tokens"] += prompt_tokens
        cached = _cached_tokens(usage)
        if cached is not None:
            bucket["cached_tokens"] += cached
            bucket["cached_tokens_reported"] += 1

    has_block_evidence = any(per_window_tokens.values()) or any(per_window_blocks.values())

    stages = {}
    for role, bucket in per_role.items():
        sections = bucket.pop("_sections")
        bucket.setdefault("blocks_per_call", [])
        bucket.setdefault("tokens_stored", 0)
        calls = bucket["calls"] or 1
        prompt_tokens = bucket["prompt_tokens"]
        tokens_stored = bucket["tokens_stored"]
        stages[role] = {
            **bucket,
            "media": dict(sorted(per_role_medium[role].items())),
            "latency_ms_mean": round(bucket["latency_ms"] / calls, 2),
            "stable_prefix_chars": _stable_prefix_chars(sections),
            # Block-derived reuse: prompt tokens the engine did NOT have to
            # store, over prompt tokens. This is the usable hit rate on vLLM,
            # where `cached_tokens` is absent.
            #
            # The None guard is per *run*, not per stage: on a rig that emits
            # block events, a stage that stored zero blocks reused everything —
            # that is 100%, the headline result, not a missing measurement.
            # Only a run with no block evidence at all (an API-only rig) has
            # nothing to report.
            "prefix_reuse_rate": (
                round(max(0.0, 1.0 - tokens_stored / prompt_tokens), 4)
                if prompt_tokens and has_block_evidence
                else None
            ),
            # Share of prompt tokens the engine did not have to prefill.
            # None when the provider never reported the field — an unreported
            # signal is not a zero, and the MARA path is measured to report
            # nothing below a length threshold.
            "cache_hit_rate": (
                round(bucket["cached_tokens"] / prompt_tokens, 4)
                if prompt_tokens and bucket["cached_tokens_reported"]
                else None
            ),
        }

    return {
        "run": str(run_dir),
        "windows": len(windows),
        "events": len(events),
        "events_attributed": attributed,
        "events_unattributed": len(events) - attributed,
        "stages": dict(sorted(stages.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out", type=Path, default=None,
                        help="write the report as JSON (default: stdout only)")
    args = parser.parse_args()

    report = correlate(args.run_dir)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
