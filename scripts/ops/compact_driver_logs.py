#!/usr/bin/env python3
"""Shrink existing driver logs to the records anybody would actually read.

The handler now keeps only statements and their server summaries, but the runs
already on disk hold the whole Bolt exchange: 835 MB, against 7 MB for every
stage record combined. That is the difference between evidence that can live in
version control and evidence that cannot.

Two reductions, both lossless in the part that matters.

The whole Bolt exchange is replaced by one summary line recording how many
messages were dropped and from which logger.

The statements themselves are dictionaried. A run issues the same handful of
Cypher texts tens of thousands of times, so the text is written once in a header
record and each execution references it by index. Parameters are truncated to a
bounded head, since the parameter block of a MERGE carries an entire node and
the head is enough to see what shape of value went in.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


MAX_PARAMS = 400


def compact(path: Path) -> tuple[int, int, int]:
    kept, dropped = [], Counter()
    before = path.stat().st_size
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("kind") in ("query", "result"):
                kept.append(record)
            else:
                dropped[record.get("logger", "unknown")] += 1
    if not dropped and not kept:
        return before, before, 0

    statements: dict[str, int] = {}
    compacted = []
    for record in kept:
        cypher = record.pop("cypher", None)
        if cypher is not None:
            index = statements.setdefault(cypher, len(statements))
            record["statement"] = index
        params = record.get("params")
        if isinstance(params, str) and len(params) > MAX_PARAMS:
            record["params"] = params[:MAX_PARAMS] + f"…[{len(params)} chars]"
        record.pop("message", None)
        compacted.append(record)

    lines = [json.dumps({"kind": "statements",
                         "text": [s for s, _ in sorted(statements.items(),
                                                       key=lambda kv: kv[1])],
                         "note": ("each query record below references one of "
                                  "these by index; the text is written once "
                                  "because a run repeats it many thousands of "
                                  "times")}, ensure_ascii=False)]
    lines += [json.dumps(r, ensure_ascii=False) for r in compacted]
    lines.append(json.dumps({
        "kind": "compaction",
        "protocol_messages_not_retained": sum(dropped.values()),
        "by_logger": dict(dropped),
        "note": ("Bolt handshakes, PULL frames, chunk boundaries and pool "
                 "events. Statements and server summaries above are complete."),
    }, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n")
    return before, path.stat().st_size, sum(dropped.values())


def main() -> int:
    paths = sorted((ROOT / "outputs").rglob("driver.jsonl"))
    total_before = total_after = total_dropped = 0
    for path in paths:
        before, after, dropped = compact(path)
        total_before += before
        total_after += after
        total_dropped += dropped
        if dropped:
            print(f"  {path.relative_to(ROOT)}  "
                  f"{before / 1048576:.1f} -> {after / 1048576:.1f} MB, "
                  f"{dropped:,} protocol messages dropped")
    print(f"\n{len(paths)} files: {total_before / 1048576:.0f} MB -> "
          f"{total_after / 1048576:.0f} MB, {total_dropped:,} messages dropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
