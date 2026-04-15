#!/usr/bin/env python3
"""GraphRAG-Bench runner for SEOCHO.

Runs a knowledge-graph RAG benchmark against a ``Seocho`` client and
writes a reproducible results JSON. Designed for two use cases:

1. CI regression gate — detect quality drops between commits
2. Published comparison — produce numbers for the README / blog

Usage::

    # Smallest smoke run — uses embedded LadybugDB + OpenAI
    OPENAI_API_KEY=... python scripts/benchmarks/graphrag_bench.py \\
        --task sample --limit 5 --out results/smoke.json

    # Full GraphRAG-Bench run against Neo4j
    python scripts/benchmarks/graphrag_bench.py \\
        --task graphrag-bench-v1 \\
        --graph bolt://localhost:7687 \\
        --out results/graphrag_bench_v1.json

Dataset loading:

- ``--task sample``: bundled 5-question smoke dataset
- ``--task graphrag-bench-v1``: download from Hugging Face if available
- ``--dataset <path>``: local JSONL with rows ``{"corpus": [...], "question": str, "answer": str}``

Metrics produced:

- ``exact_match`` — answer string equals the gold answer
- ``substring_match`` — gold answer appears in the predicted answer
- ``entity_recall`` — fraction of gold entities retrieved in the
  evidence bundle
- ``latency_p50_ms`` / ``latency_p95_ms`` — query-side latency
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from _preflight import local_llm_api_key_error

# ---------------------------------------------------------------------------
# Bundled smoke dataset — 5 questions, minimal ontology
# ---------------------------------------------------------------------------

SMOKE_ONTOLOGY = {
    "name": "benchmark_smoke",
    "nodes": {
        "Person": {"name": "str:unique"},
        "Company": {"name": "str:unique"},
        "Country": {"name": "str:unique"},
    },
    "relationships": [
        ("WORKS_AT", "Person", "Company"),
        ("FOUNDED", "Person", "Company"),
        ("HEADQUARTERED_IN", "Company", "Country"),
        ("BORN_IN", "Person", "Country"),
    ],
}

SMOKE_DATASET = [
    {
        "corpus": [
            "Satya Nadella is the CEO of Microsoft. Microsoft is headquartered in the United States.",
            "Sundar Pichai is the CEO of Google. Google is headquartered in the United States.",
            "Satya Nadella was born in India.",
        ],
        "question": "Where was the CEO of Microsoft born?",
        "answer": "India",
        "gold_entities": ["Satya Nadella", "Microsoft", "India"],
    },
    {
        "corpus": [
            "Jeff Bezos founded Amazon in 1994. Amazon is headquartered in the United States.",
            "Andy Jassy is the CEO of Amazon.",
        ],
        "question": "Who founded Amazon?",
        "answer": "Jeff Bezos",
        "gold_entities": ["Jeff Bezos", "Amazon"],
    },
    {
        "corpus": [
            "Larry Page co-founded Google. Google is headquartered in the United States.",
            "Sergey Brin also co-founded Google.",
        ],
        "question": "Which country is Google headquartered in?",
        "answer": "United States",
        "gold_entities": ["Google", "United States"],
    },
    {
        "corpus": [
            "Tim Cook is the CEO of Apple. Apple is headquartered in the United States.",
            "Steve Jobs co-founded Apple in 1976.",
        ],
        "question": "Who is the CEO of Apple?",
        "answer": "Tim Cook",
        "gold_entities": ["Tim Cook", "Apple"],
    },
    {
        "corpus": [
            "Elon Musk founded SpaceX. SpaceX is headquartered in the United States.",
            "Elon Musk was born in South Africa.",
            "Gwynne Shotwell is the President of SpaceX.",
        ],
        "question": "Where was the founder of SpaceX born?",
        "answer": "South Africa",
        "gold_entities": ["Elon Musk", "SpaceX", "South Africa"],
    },
]


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    question: str
    gold_answer: str
    predicted_answer: str
    exact_match: bool
    substring_match: bool
    gold_entities: List[str] = field(default_factory=list)
    retrieved_entities: List[str] = field(default_factory=list)
    entity_recall: float = 0.0
    latency_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class BenchmarkResult:
    task: str
    total_cases: int
    completed_cases: int
    exact_match: float
    substring_match: float
    avg_entity_recall: float
    latency_p50_ms: float
    latency_p95_ms: float
    cases: List[CaseResult] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Ontology builder
# ---------------------------------------------------------------------------

def build_ontology(spec: Dict[str, Any]) -> Any:
    """Construct a seocho Ontology from the dataset's ontology spec."""
    from seocho import Ontology, NodeDef, Property, RelDef

    nodes = {}
    for label, prop_map in spec.get("nodes", {}).items():
        props = {}
        for prop_name, prop_spec in prop_map.items():
            # Syntax: "str:unique" | "str" | "int"
            parts = prop_spec.split(":") if isinstance(prop_spec, str) else [prop_spec]
            type_name = parts[0]
            unique = "unique" in parts[1:]
            py_type = {"str": str, "int": int, "float": float, "bool": bool}.get(
                type_name, str
            )
            props[prop_name] = Property(py_type, unique=unique)
        nodes[label] = NodeDef(properties=props)

    rels = {}
    for rel_spec in spec.get("relationships", []):
        rtype, src, tgt = rel_spec
        rels[rtype] = RelDef(source=src, target=tgt)

    return Ontology(name=spec.get("name", "bench"), nodes=nodes, relationships=rels)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_case(client: Any, case: Dict[str, Any]) -> CaseResult:
    """Index the corpus, ask the question, score the answer."""
    for doc in case["corpus"]:
        try:
            client.add(doc)
        except Exception as exc:
            return CaseResult(
                question=case["question"],
                gold_answer=case["answer"],
                predicted_answer="",
                exact_match=False,
                substring_match=False,
                gold_entities=case.get("gold_entities", []),
                retrieved_entities=[],
                entity_recall=0.0,
                error=f"ingest_failed: {exc}",
            )

    start = time.perf_counter()
    try:
        predicted = client.ask(case["question"])
    except Exception as exc:
        return CaseResult(
            question=case["question"],
            gold_answer=case["answer"],
            predicted_answer="",
            exact_match=False,
            substring_match=False,
            gold_entities=case.get("gold_entities", []),
            retrieved_entities=[],
            entity_recall=0.0,
            error=f"query_failed: {exc}",
        )
    latency_ms = (time.perf_counter() - start) * 1000

    predicted_str = str(predicted).strip()
    gold = str(case["answer"]).strip()
    exact = predicted_str.lower() == gold.lower()
    substring = gold.lower() in predicted_str.lower()

    retrieved: List[str] = []
    # If the client supports semantic() we can inspect evidence
    try:
        if hasattr(client, "semantic"):
            resp = client.semantic(case["question"])
            if isinstance(resp, dict):
                for entity in resp.get("semantic_context", {}).get("entities", []):
                    retrieved.append(str(entity))
    except Exception:
        pass

    gold_ents = [e.lower() for e in case.get("gold_entities", [])]
    retrieved_lc = [e.lower() for e in retrieved]
    if not retrieved_lc:
        # Fallback: check predicted answer for entity mentions
        retrieved_lc = [e for e in gold_ents if e in predicted_str.lower()]
    recall = (
        sum(1 for e in gold_ents if any(e in r or r in e for r in retrieved_lc))
        / max(len(gold_ents), 1)
    )

    return CaseResult(
        question=case["question"],
        gold_answer=gold,
        predicted_answer=predicted_str,
        exact_match=exact,
        substring_match=substring,
        gold_entities=case.get("gold_entities", []),
        retrieved_entities=retrieved[:10],
        entity_recall=round(recall, 3),
        latency_ms=round(latency_ms, 2),
    )


def load_dataset(task: str, dataset_path: Optional[str]) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if dataset_path:
        rows = [json.loads(line) for line in Path(dataset_path).read_text().splitlines() if line.strip()]
        onto = rows[0].get("ontology", SMOKE_ONTOLOGY) if rows else SMOKE_ONTOLOGY
        return onto, rows
    if task == "sample":
        return SMOKE_ONTOLOGY, SMOKE_DATASET
    # TODO: graphrag-bench-v1 loader (requires HF datasets)
    raise ValueError(f"Unknown task: {task}. Use --task sample or --dataset <file>")


def build_client(args: argparse.Namespace, onto: Any) -> Any:
    from seocho import Seocho

    kwargs: Dict[str, Any] = {}
    if args.graph:
        kwargs["graph"] = args.graph
    if args.llm:
        kwargs["llm"] = args.llm
    if args.api_key:
        kwargs["api_key"] = args.api_key
    return Seocho.local(onto, **kwargs)


def aggregate(cases: Sequence[CaseResult]) -> Dict[str, float]:
    completed = [c for c in cases if c.error is None]
    if not completed:
        return {
            "exact_match": 0.0,
            "substring_match": 0.0,
            "avg_entity_recall": 0.0,
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
        }
    em = sum(1 for c in completed if c.exact_match) / len(completed)
    sm = sum(1 for c in completed if c.substring_match) / len(completed)
    recall = sum(c.entity_recall for c in completed) / len(completed)
    latencies = sorted(c.latency_ms for c in completed)
    p50 = statistics.median(latencies) if latencies else 0.0
    p95_idx = max(int(len(latencies) * 0.95) - 1, 0)
    p95 = latencies[p95_idx] if latencies else 0.0
    return {
        "exact_match": round(em, 3),
        "substring_match": round(sm, 3),
        "avg_entity_recall": round(recall, 3),
        "latency_p50_ms": round(p50, 2),
        "latency_p95_ms": round(p95, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="GraphRAG-Bench runner for SEOCHO")
    parser.add_argument("--task", default="sample", help="Bundled task name")
    parser.add_argument("--dataset", default=None, help="Custom JSONL dataset path")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of cases")
    parser.add_argument("--graph", default=None,
                        help="Graph backend URI (defaults to embedded LadybugDB)")
    parser.add_argument("--llm", default="openai/gpt-4o-mini",
                        help="Provider/model string")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY"),
                        help="LLM API key override for local benchmark runs")
    parser.add_argument("--out", default="-", help="Output JSON path (- for stdout)")
    args = parser.parse_args()

    if error := local_llm_api_key_error(args.llm, args.api_key):
        print(error, file=sys.stderr)
        return 2

    onto_spec, dataset = load_dataset(args.task, args.dataset)
    if args.limit:
        dataset = dataset[: args.limit]

    onto = build_ontology(onto_spec)
    client = build_client(args, onto)

    cases: List[CaseResult] = []
    for i, case in enumerate(dataset):
        print(f"[{i + 1}/{len(dataset)}] {case['question'][:60]}", file=sys.stderr)
        cases.append(run_case(client, case))

    agg = aggregate(cases)
    result = BenchmarkResult(
        task=args.task,
        total_cases=len(dataset),
        completed_cases=sum(1 for c in cases if c.error is None),
        exact_match=agg["exact_match"],
        substring_match=agg["substring_match"],
        avg_entity_recall=agg["avg_entity_recall"],
        latency_p50_ms=agg["latency_p50_ms"],
        latency_p95_ms=agg["latency_p95_ms"],
        cases=cases,
        config={
            "graph": args.graph or ".seocho/local.lbug (embedded)",
            "llm": args.llm,
            "dataset": args.dataset or args.task,
        },
    )

    output = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    if args.out == "-":
        print(output)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Wrote results to {args.out}", file=sys.stderr)

    print(
        f"\nResults: EM={agg['exact_match']:.1%} "
        f"SM={agg['substring_match']:.1%} "
        f"Recall={agg['avg_entity_recall']:.1%} "
        f"p50={agg['latency_p50_ms']:.0f}ms p95={agg['latency_p95_ms']:.0f}ms",
        file=sys.stderr,
    )

    try:
        client.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
