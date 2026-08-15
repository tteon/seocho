"""Empirically answer two pre-flight questions against a live vLLM (§11 #12, #20).

#12 — does the KV-event stream expose *block-level* identity (hashes, parent
chain), or only request-level aggregates? Without block identity the WP2
serialization sweeps cannot be observed at all.

#20 — does the default eviction respect prefix depth? The unified-cache-layer
design assumes tree-aware eviction is required (a naive LRU that drops a
mid-chain block strands every descendant). If vLLM already evicts leaves-first,
WP6.1b's tree-aware eviction is validation, not construction.

Method: subscribe to the ZMQ KV-event feed, then
  phase A  two requests sharing a long common prefix -> expect the second
           request to store only suffix blocks (prefix reuse visible as
           parent_block_hash chaining into request 1's blocks)
  phase B  many long, distinct prompts to force cache pressure -> collect
           BlockRemoved order; a removed block whose stored *children* are
           still resident at removal time is mid-chain eviction evidence.

Usage (server started with --kv-events-config '{"enable_kv_cache_events": true,
"publisher": "zmq", "endpoint": "tcp://127.0.0.1:5557"}'):

  python scripts/cache_probe/kv_events_probe.py \
      --base-url http://localhost:8000/v1 --model Qwen/Qwen3-0.6B
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def _decode_batches(raw: bytes) -> List[Dict[str, Any]]:
    """Decode one msgpack payload into event dicts, tolerating schema drift.

    vLLM publishes msgspec-encoded batches whose exact layout has moved
    between versions; rather than pin a struct, walk whatever arrives and
    keep anything that looks like a block event.
    """
    import msgpack

    payload = msgpack.unpackb(raw, raw=False, strict_map_key=False)
    events: List[Dict[str, Any]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if "block_hashes" in obj or "block_hash" in obj:
                events.append(obj)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, (list, tuple)):
            # msgspec array-encoded structs: tagged as [tag, field, ...]
            if obj and isinstance(obj[0], str) and obj[0] in (
                "BlockStored", "BlockRemoved", "AllBlocksCleared"
            ):
                events.append({"_tag": obj[0], "_fields": list(obj[1:])})
            else:
                for value in obj:
                    walk(value)

    walk(payload)
    if not events:
        events.append({"_raw": payload})
    return events


class Subscriber(threading.Thread):
    def __init__(self, endpoint: str, topic: str = "", bind: bool = False) -> None:
        super().__init__(daemon=True)
        self.endpoint = endpoint
        self.topic = topic
        self.bind = bind
        self.frames: List[Dict[str, Any]] = []
        self._stop = threading.Event()

    def run(self) -> None:
        import zmq

        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.SUB)
        # vLLM's ZmqEventPublisher BINDS only when the endpoint contains a
        # wildcard; a concrete host:port makes it CONNECT and expect the
        # subscriber to be the stable bound end (measured on 0.27.1).
        if self.bind:
            sock.bind(self.endpoint)
        else:
            sock.connect(self.endpoint)
        sock.setsockopt_string(zmq.SUBSCRIBE, self.topic)
        sock.RCVTIMEO = 500
        while not self._stop.is_set():
            try:
                parts = sock.recv_multipart()
            except zmq.Again:
                continue
            payload = parts[-1]
            for event in _decode_batches(payload):
                event["_recv_ts"] = time.time()
                self.frames.append(event)
        sock.close()

    def stop(self) -> None:
        self._stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000/v1")
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--events", default="tcp://127.0.0.1:5557")
    parser.add_argument("--bind", action="store_true",
                        help="bind instead of connect (required when the server "
                             "was given a concrete host:port endpoint)")
    parser.add_argument("--pressure-prompts", type=int, default=64)
    parser.add_argument("--pressure-tokens", type=int, default=3000,
                        help="approx prompt length (chars*4) for phase B")
    parser.add_argument("--out", default="outputs/cache_probe/kv_events.jsonl")
    args = parser.parse_args()

    from openai import OpenAI

    client = OpenAI(base_url=args.base_url, api_key="probe")
    sub = Subscriber(args.events, bind=args.bind)
    sub.start()
    time.sleep(2.0)

    def ask(prompt: str, tag: str) -> Dict[str, Any]:
        started = time.perf_counter()
        response = client.completions.create(
            model=args.model, prompt=prompt, max_tokens=8, temperature=0.0)
        usage = getattr(response, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details else None
        return {"tag": tag, "latency_ms": (time.perf_counter() - started) * 1000,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "cached_tokens": cached}

    shared_prefix = ("SEOCHO ontology core v3. " * 120)  # ~600 tokens of shared head
    marker_a = len(sub.frames)
    r1 = ask(shared_prefix + "Question A: summarize transfers.", "prefix_first")
    time.sleep(1.0)
    marker_b = len(sub.frames)
    r2 = ask(shared_prefix + "Question B: list the channels.", "prefix_second")
    time.sleep(1.0)
    marker_c = len(sub.frames)

    stored_a = sub.frames[marker_a:marker_b]
    stored_b = sub.frames[marker_b:marker_c]

    print(f"phase A: first request stored {len(stored_a)} event frames, "
          f"second stored {len(stored_b)}")
    print(f"  first:  {r1}")
    print(f"  second: {r2}")
    if stored_a:
        print("  sample event:", json.dumps(stored_a[0], default=str)[:400])

    # Phase B: pressure. Distinct long prompts until removals appear.
    removals_before = sum(1 for f in sub.frames if f.get("type") == "BlockRemoved")
    for index in range(args.pressure_prompts):
        filler = f"case {index}: " + (f"row-{index}-" * (args.pressure_tokens // 8))
        ask(filler[: args.pressure_tokens * 4], f"pressure_{index}")
    time.sleep(2.0)
    sub.stop()
    sub.join(timeout=3)

    frames = sub.frames
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for frame in frames:
            handle.write(json.dumps(frame, default=str) + "\n")

    stored = [f for f in frames if f.get("type") == "BlockStored"]
    removed = [f for f in frames if f.get("type") == "BlockRemoved"]
    print(f"\ntotal frames={len(frames)} stored_events={len(stored)} "
          f"removed_events={len(removed)} (removed before pressure: {removals_before})")
    print(f"raw feed written to {out}")

    # Parent-chain depth per block (BlockStored on 0.27.1 is dict-shaped:
    # block_hashes, parent_block_hash, token_ids, block_size, medium, ...).
    parent_of: Dict[Any, Any] = {}
    for frame in stored:
        previous = frame.get("parent_block_hash")
        for block in frame.get("block_hashes", []):
            parent_of[block] = previous
            previous = block

    def depth(block: Any) -> int:
        d, cur, seen = 0, parent_of.get(block), set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            d += 1
            cur = parent_of.get(cur)
        return d

    children = defaultdict(set)
    for block, parent in parent_of.items():
        if parent is not None:
            children[parent].add(block)

    resident: set = set()
    mid_chain = 0
    removal_depths: List[int] = []
    for frame in frames:
        kind = frame.get("type")
        if kind == "BlockStored":
            resident.update(frame.get("block_hashes", []))
        elif kind == "BlockRemoved":
            for block in frame.get("block_hashes", []):
                removal_depths.append(depth(block))
                if any(child in resident for child in children.get(block, ())):
                    mid_chain += 1
                resident.discard(block)

    if removal_depths:
        print(f"removed blocks: {len(removal_depths)}; "
              f"mid-chain removals (children still resident): {mid_chain}")
        print(f"removal depths: first 20 = {removal_depths[:20]} "
              f"(descending = leaves-first eviction)")


if __name__ == "__main__":
    main()
