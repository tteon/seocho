"""Probe how a reasoning model behaves across the six workload patterns.

The MARA API exposes no KV cache, so what CAN be measured there is behavior:
per pattern, how a reasoning model spends tokens, whether structured output
survives strict parsing or needs salvage, and how latency scales with the
prompt family. Each pattern below uses a prompt *shaped like* the SEOCHO
surface it names (text2cypher carries the real v1 system prompt; the others
mirror their production prompt families in miniature), so the numbers rank
patterns against each other rather than certify any one of them.

Full-stack episodes (real graph, real tool loops) come from the collector
that transforms SEOCHO's JSONL spans; this probe is the LLM-layer slice that
needs nothing but an API key, and the smoke test for trace schema v1.

Usage:
  MARA_API_KEY=... python scripts/pattern_traces/probe_reasoning.py \
      --model MiniMax-M2.7 --repeats 1 --out outputs/pattern_traces/probe.jsonl
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path
from typing import Any, Dict, Tuple

_spec = importlib.util.spec_from_file_location(
    "pattern_trace_schema", Path(__file__).resolve().parent / "schema.py")
schema = importlib.util.module_from_spec(_spec)
import sys as _sys  # noqa: E402

# dataclasses resolves field types through sys.modules[cls.__module__];
# an unregistered importlib module breaks that on Python >= 3.13.
_sys.modules["pattern_trace_schema"] = schema
_spec.loader.exec_module(schema)


_FIN_SCHEMA = {
    "Account": ["acct_no", "open_date"],
    "Company": ["name", "country"],
    "TRANSFER": ["amount", "channel", "ts"],
    "OWN": ["share"],
}

_CONTEXT_TRIPLES = (
    "Account(9000001) -TRANSFER {amount: 48000, channel: WIRE_CROSSBORDER}-> Account(9000002)\n"
    "Account(9000002) -TRANSFER {amount: 47500, channel: WIRE_CROSSBORDER}-> Account(9000003)\n"
    "Company(Hanbit Trading) -OWN {share: 1.0}-> Account(9000003)\n"
    "Account(9000003) -TRANSFER {amount: 95000, channel: INTERNAL}-> Account(9000001)"
)

_EMAIL_SNIPPET = (
    "From: kyle@hanbit.example  To: ops@hanbit.example\n"
    "Subject: urgent wire\n"
    "Please move 48,000 USD from account 9000001 to 9000002 today via cross-border "
    "wire, then sweep the balance of 9000003 back internally before Friday's audit."
)

# (role, system, user, expects_json)
_PATTERN_CALLS: Dict[str, Tuple[str, str, str, bool]] = {
    "indexing": (
        "extract",
        "Extract entities and relationships as one JSON object with keys "
        "'nodes' (list of {label, properties}) and 'relationships' (list of "
        "{type, source, target, properties}). Use only labels Account, Company "
        "and relationship types TRANSFER, OWN. Return JSON only.",
        _EMAIL_SNIPPET,
        True,
    ),
    "search": (
        "synthesize",
        "Answer the question strictly from the graph context. Cite the rows "
        "you used. If the context cannot support an answer, say so.",
        f"Context:\n{_CONTEXT_TRIPLES}\n\nQuestion: Who ultimately receives the "
        "swept funds, and through which company?",
        False,
    ),
    "text2cypher": (
        "generate",
        # The real production system prompt (src/seocho/query/text2cypher.py).
        "SEOCHO Text2Cypher v1. Return one JSON object with key cypher. Generate a "
        "read-only Cypher query using only the supplied schema and named parameters. "
        "It must include tenant scope, RETURN, and LIMIT $limit. Never insert literal IDs.",
        json.dumps({
            "question": "How many distinct accounts sent transfers into account 9000003?",
            "schema": _FIN_SCHEMA,
            "available_parameters": ["acct_no", "limit", "workspace_id"],
            "max_hops": 2,
            "prior_failures": [],
        }, sort_keys=True),
        True,
    ),
    "single_agent": (
        "act",
        "You are an analyst agent with one tool: graph_query(cypher). Decide your "
        "next action as one JSON object {\"action\": \"graph_query\"|\"final\", "
        "\"cypher\"?: str, \"answer\"?: str}. Return JSON only.",
        "Task: find the fan-in of account 9000003.\nLast tool result: "
        "[{\"sender\": 9000002, \"amount\": 47500}] (row_cap 50, more_available false)",
        True,
    ),
    "multi_agent": (
        "route",
        "You are a supervisor. Choose which specialist handles the task and say why, "
        "as one JSON object {\"route\": \"graph_analyst\"|\"doc_reader\"|\"calculator\", "
        "\"reason\": str}. Return JSON only.",
        "Task: verify whether the amounts wired out of account 9000001 this week sum "
        "to more than its declared monthly limit of 100,000 USD.",
        True,
    ),
    "agent_agent": (
        "critique",
        "You are a verifier agent in a debate. Another agent claims the answer below. "
        "Attack the claim from the evidence, then return one JSON object "
        "{\"verdict\": \"supported\"|\"refuted\"|\"insufficient\", \"reason\": str}. "
        "Return JSON only.",
        f"Evidence:\n{_CONTEXT_TRIPLES}\n\nClaim: account 9000001 is the final "
        "beneficiary of a layered round-trip through Hanbit Trading.",
        True,
    ),
}


def _parse_outcome(text: str, response: Any) -> str:
    try:
        json.loads(text.strip())
        return "ok"
    except Exception:
        pass
    try:
        response.json()
        return "salvaged"
    except Exception:
        return "failed"


def run_probe(*, provider: str, model: str, repeats: int, out: Path,
              temperature: float = 0.0) -> Dict[str, Any]:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from seocho.store.llm import create_llm_backend

    backend = create_llm_backend(provider=provider, model=model)
    summary: Dict[str, Any] = {}
    for pattern, (role, system, user, expects_json) in _PATTERN_CALLS.items():
        for repeat in range(repeats):
            episode = schema.Episode(
                pattern=pattern, case_id=f"probe_{pattern}_{repeat}",
                model=model, provider=provider,
            )
            started = time.perf_counter()
            error = None
            try:
                response = backend.complete(
                    system=system, user=user, temperature=temperature,
                    task_hint=pattern, mode="pipeline",
                )
            except Exception as exc:  # recorded, not raised: a dead pattern is data
                response = None
                error = f"{type(exc).__name__}: {exc}"
            latency_ms = (time.perf_counter() - started) * 1000.0
            step = schema.LLMStep(
                role=role, model=model, latency_ms=round(latency_ms, 1),
                usage=dict(getattr(response, "usage", {}) or {}),
                text_chars=len(getattr(response, "text", "") or ""),
                parse=(_parse_outcome(response.text, response) if response and expects_json
                       else "not_applicable"),
                prompt_sections={"system": len(system), "user": len(user)},
                error=error,
            )
            episode.steps.append(step)
            episode.outcome = {
                "ok": error is None,
                "e2e_ms": round(latency_ms, 1),
                "expects_json": expects_json,
            }
            schema.append_episode(out, episode)
            summary.setdefault(pattern, []).append({
                "parse": step.parse, "latency_ms": step.latency_ms,
                "usage": step.usage, "text_chars": step.text_chars,
                "error": error,
            })
            print(f"{pattern:13s} r{repeat} {step.parse:14s} {step.latency_ms:8.0f}ms "
                  f"chars={step.text_chars} usage={step.usage}", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="mara")
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--out", default="outputs/pattern_traces/probe.jsonl")
    parser.add_argument("--patterns", default="all",
                        help="comma-separated subset, default all six")
    args = parser.parse_args()

    if args.patterns != "all":
        wanted = {p.strip() for p in args.patterns.split(",")}
        unknown = wanted - schema.PATTERNS
        if unknown:
            raise SystemExit(f"unknown patterns: {sorted(unknown)}")
        for pattern in list(_PATTERN_CALLS):
            if pattern not in wanted:
                del _PATTERN_CALLS[pattern]

    run_probe(provider=args.provider, model=args.model, repeats=args.repeats,
              out=Path(args.out), temperature=args.temperature)


if __name__ == "__main__":
    main()
