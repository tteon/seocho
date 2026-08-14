"""WP0 offline cache simulator: serialized context -> blocks -> MRC. No GPU.

The unified-cache-layer plan (design note v0.3 §4.3) requires sweeping
serialization/ordering/granularity combinations in minutes, which rules out
measuring each one on an engine. This simulator reproduces the two mechanics
that decide prefix-cache behavior and nothing else:

1. **Content-chained blockification** — vLLM v1 identifies a KV block by the
   hash of (parent hash, block tokens), so two requests share cache exactly
   as far as their token streams are byte-identical. ``blockify`` mirrors
   that: shared prefixes produce identical hash chains, and one divergent
   token breaks every hash after it. This is why WP2's canonicalization
   matters and the simulator can show it before an engine ever runs.

2. **Exact LRU miss-ratio curves via Mattson stack distances** — one pass
   over the block-access stream yields the hit rate at *every* capacity
   simultaneously, which dissolves the "cache-pressure-free measurement"
   trap (§6-①): capacity is an axis of the output, not a knob of the run.

Measurement discipline baked in: results are always produced for both
arrival orders (as-given/session-clustered and seeded-shuffle, §6-②).

Input: trace-schema v1 episodes (scripts/pattern_traces/schema.py) or any
JSONL with a ``context`` text field / ``token_ids`` list per record.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

DEFAULT_BLOCK_SIZE = 16  # vLLM v1 default, measured in this repo's engine logs


# ----------------------------------------------------------------------------
# Tokenization. The simulator needs *deterministic* tokens, not model-true
# ones: MRC shape depends on shared-prefix structure, which any stable
# injective-enough tokenizer preserves. A real HF tokenizer can be plugged in
# when absolute block counts must match an engine.
# ----------------------------------------------------------------------------

def chars_tokenizer(text: str, chars_per_token: int = 4) -> List[int]:
    """Deterministic fallback tokenizer: fixed-width character chunks."""
    return [
        int.from_bytes(hashlib.blake2b(
            text[i:i + chars_per_token].encode("utf-8"), digest_size=4).digest(), "big")
        for i in range(0, len(text), chars_per_token)
    ]


def hf_tokenizer(name: str):
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(name)

    def encode(text: str) -> List[int]:
        return tokenizer.encode(text, add_special_tokens=False)

    return encode


# ----------------------------------------------------------------------------
# Blockification
# ----------------------------------------------------------------------------

def blockify(token_ids: Sequence[int], block_size: int = DEFAULT_BLOCK_SIZE) -> List[str]:
    """Content-chained block hashes; only full blocks are cacheable (vLLM v1)."""
    hashes: List[str] = []
    parent = b"root"
    for start in range(0, len(token_ids) - len(token_ids) % block_size, block_size):
        chunk = token_ids[start:start + block_size]
        digest = hashlib.blake2b(digest_size=16)
        digest.update(parent)
        digest.update(",".join(str(t) for t in chunk).encode())
        block = digest.hexdigest()
        hashes.append(block)
        parent = block.encode()
    return hashes


# ----------------------------------------------------------------------------
# Exact LRU MRC (Mattson). Stack distance per access; hit at capacity C iff
# distance <= C. O(N * uniq) worst case with a plain list — fine at trace
# scale (10^5 accesses) and dependency-free; swap in a tree if it ever isn't.
# ----------------------------------------------------------------------------

@dataclass
class MRCResult:
    accesses: int
    unique_blocks: int
    distance_histogram: Dict[int, int]          # stack distance -> count (-1 = cold)
    capacities: List[int] = field(default_factory=list)
    hit_rates: List[float] = field(default_factory=list)

    def hit_rate_at(self, capacity_blocks: int) -> float:
        hits = sum(count for distance, count in self.distance_histogram.items()
                   if 0 <= distance <= capacity_blocks)
        return hits / self.accesses if self.accesses else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accesses": self.accesses,
            "unique_blocks": self.unique_blocks,
            "capacities": self.capacities,
            "hit_rates": self.hit_rates,
            "cold_misses": self.distance_histogram.get(-1, 0),
        }


def mattson_mrc(block_stream: Iterable[str],
                capacities: Optional[Sequence[int]] = None) -> MRCResult:
    stack: List[str] = []          # most recent at end
    position: Dict[str, int] = {}
    histogram: Counter = Counter()
    accesses = 0
    for block in block_stream:
        accesses += 1
        if block in position:
            index = stack.index(block)          # distance from the top
            distance = len(stack) - index       # 1-based unique-since-last-use
            histogram[distance] += 1
            stack.pop(index)
        else:
            histogram[-1] += 1
        stack.append(block)
        position[block] = 1
    unique = len(position)
    result = MRCResult(accesses=accesses, unique_blocks=unique,
                       distance_histogram=dict(histogram))
    if capacities is None:
        # log-spaced sweep up to the full working set
        capacities, cap = [], 16
        while cap < unique * 2:
            capacities.append(cap)
            cap *= 2
        capacities.append(unique)
    result.capacities = sorted(set(int(c) for c in capacities))
    result.hit_rates = [round(result.hit_rate_at(c), 4) for c in result.capacities]
    return result


# ----------------------------------------------------------------------------
# Reuse-distance over *episodes*: each episode contributes its block chain in
# root->leaf order, which is how a prefix cache actually touches blocks.
# ----------------------------------------------------------------------------

def episode_block_stream(contexts: Sequence[Sequence[int]],
                         block_size: int = DEFAULT_BLOCK_SIZE) -> List[str]:
    stream: List[str] = []
    for token_ids in contexts:
        stream.extend(blockify(token_ids, block_size))
    return stream


@dataclass
class SweepReport:
    block_size: int
    as_given: MRCResult
    shuffled: MRCResult
    shuffle_seed: int
    shared_prefix_blocks: int      # blocks appearing in >1 episode

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_size": self.block_size,
            "shuffle_seed": self.shuffle_seed,
            "shared_prefix_blocks": self.shared_prefix_blocks,
            "as_given": self.as_given.to_dict(),
            "shuffled": self.shuffled.to_dict(),
        }


def sweep(contexts: Sequence[Sequence[int]], *,
          block_size: int = DEFAULT_BLOCK_SIZE,
          shuffle_seed: int = 20260815) -> SweepReport:
    """Both arrival orders, one report — §6-② is not optional here."""
    ordered = list(contexts)
    stream = episode_block_stream(ordered, block_size)
    shuffled_contexts = list(ordered)
    random.Random(shuffle_seed).shuffle(shuffled_contexts)
    shuffled_stream = episode_block_stream(shuffled_contexts, block_size)

    per_episode_sets = [set(blockify(c, block_size)) for c in ordered]
    seen: Counter = Counter()
    for blocks in per_episode_sets:
        seen.update(blocks)
    shared = sum(1 for _, count in seen.items() if count > 1)

    return SweepReport(
        block_size=block_size,
        as_given=mattson_mrc(stream),
        shuffled=mattson_mrc(shuffled_stream),
        shuffle_seed=shuffle_seed,
        shared_prefix_blocks=shared,
    )


# ----------------------------------------------------------------------------
# Trace loading
# ----------------------------------------------------------------------------

def load_contexts(path: Path, *, tokenizer, text_field: str = "context") -> List[List[int]]:
    """Pull per-episode context token streams out of a JSONL trace file.

    Accepts trace-schema v1 episodes (uses each LLM step's prompt sections
    reconstructed by the caller into ``context``), or any JSONL bearing
    ``token_ids`` / a text field.
    """
    contexts: List[List[int]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "token_ids" in record:
            contexts.append([int(t) for t in record["token_ids"]])
        elif text_field in record:
            contexts.append(tokenizer(str(record[text_field])))
    return contexts


def main() -> None:  # pragma: no cover - CLI wiring
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True, help="JSONL episodes")
    parser.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    parser.add_argument("--tokenizer", default="chars:4",
                        help="chars:N (deterministic) or hf:<model-name>")
    parser.add_argument("--text-field", default="context")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if args.tokenizer.startswith("hf:"):
        tokenizer = hf_tokenizer(args.tokenizer[3:])
    else:
        width = int(args.tokenizer.split(":", 1)[1]) if ":" in args.tokenizer else 4
        tokenizer = lambda text: chars_tokenizer(text, width)  # noqa: E731

    contexts = load_contexts(Path(args.trace), tokenizer=tokenizer,
                             text_field=args.text_field)
    report = sweep(contexts, block_size=args.block_size)
    rendered = json.dumps(report.to_dict(), indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered)
    print(rendered)


if __name__ == "__main__":
    main()
