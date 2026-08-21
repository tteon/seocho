"""MARA-backed, fail-closed Text2Cypher fallback for unknown read intents."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from seocho.metrics import get_metrics
from seocho.store.llm import LLMBackend

from .workload_compiler import Text2CypherFallbackPolicy, validate_text2cypher_fallback


Explain = Callable[[str, Mapping[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Text2CypherResult:
    cypher: str
    params: Mapping[str, Any]
    attempts: int
    explained: bool
    prompt_version: str = "seocho.text2cypher.v1"


async def generate_validated_cypher(
    *,
    question: str,
    schema: Mapping[str, tuple[str, ...]],
    params: Mapping[str, Any],
    policy: Text2CypherFallbackPolicy,
    backend: LLMBackend,
    model: str,
    explain: Explain,
) -> Text2CypherResult:
    """Generate, validate, EXPLAIN, and at most once repair a read query."""

    # The contract has to be stated exactly, because the validator checks it
    # exactly. "It must include tenant scope" left the model to guess the
    # expression, and it guessed `{workspace_id: $workspace_id}` -- rejected as
    # unknown_properties AND missing_workspace_scope_expression. Measured live
    # against MiniMax-M2.7: 2 of 2 generations failed that way, and the repair
    # loop reproduced the identical violation on every attempt, because the
    # feedback names what is wrong and the prompt never says what is right.
    scope_expression = f"{{{policy.workspace_property}: $workspace_id}}"
    system = (
        "SEOCHO Text2Cypher v1. Return one JSON object with key cypher. Generate a "
        "read-only Cypher query using only the supplied schema and named parameters.\n"
        f"Every matched node MUST carry the tenant scope {scope_expression} — that "
        f"exact property name, matched inline in the node pattern.\n"
        "The query MUST end with LIMIT $limit and MUST RETURN something.\n"
        "Compare against PARAMETERS, never inline literals: write `WHERE n.name = "
        "$name`, not `WHERE n.name = 'Tesla'`. An inlined literal creates a new "
        "plan-cache entry per entity. LIMIT/SKIP/hop bounds are the exception."
    )
    feedback: list[str] = []
    metrics = get_metrics()
    started = time.perf_counter()

    def _finish(outcome: str) -> None:
        metrics.record(
            "seocho.text2cypher.duration",
            time.perf_counter() - started,
            {"stage": "generate_validated", "outcome": outcome},
        )

    for attempt in range(1, policy.max_repair_attempts + 2):
        response = await backend.acomplete(
            system=system,
            user=json.dumps(
                {
                    "question": question,
                    "schema": schema,
                    "required_node_scope": scope_expression,
                    "available_parameters": sorted(params),
                    "max_hops": policy.max_graph_hops,
                    "prior_failures": feedback,
                },
                sort_keys=True,
            ),
            temperature=0.0,
            # 700 truncated the JSON mid-string on multi-clause queries, which
            # surfaced as StructuredOutputError and rejected two otherwise valid
            # generations in the SF1000 arm comparison. Reasoning-capable models also
            # spend budget before emitting the object.
            max_tokens=int(os.getenv("SEOCHO_TEXT2CYPHER_MAX_TOKENS", "2000")),
            response_format={"type": "json_object"},
            task_hint="text2cypher",
            mode="pipeline",
            model=model,
        )
        payload = response.json()
        cypher = str(payload.get("cypher", "")).strip()
        violations = validate_text2cypher_fallback(cypher, params=params, policy=policy)
        if violations:
            # Violations read "unknown_labels:Account,Foo" — only the code
            # before the colon is bounded; the payload is data-dependent and
            # belongs in traces, not metric labels.
            metrics.add(
                "seocho.text2cypher.validation_failure.count",
                attributes={"reason": violations[0].split(":", 1)[0]},
            )
            feedback = list(violations)
            continue
        try:
            await explain(cypher, params)
        except Exception as exc:
            metrics.add(
                "seocho.text2cypher.execution_failure.count",
                attributes={"error.type": type(exc).__name__},
            )
            feedback = [f"explain_failed:{type(exc).__name__}"]
            continue
        _finish("ok")
        return Text2CypherResult(
            cypher=cypher,
            params=dict(params),
            attempts=attempt,
            explained=True,
        )
    _finish("rejected")
    raise ValueError("text2cypher rejected: " + ",".join(feedback))


__all__ = ["Text2CypherResult", "generate_validated_cypher"]
