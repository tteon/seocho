"""MARA-backed, fail-closed Text2Cypher fallback for unknown read intents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

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

    system = (
        "SEOCHO Text2Cypher v1. Return one JSON object with key cypher. Generate a "
        "read-only Cypher query using only the supplied schema and named parameters. "
        "It must include tenant scope, RETURN, and LIMIT $limit. Never insert literal IDs."
    )
    feedback: list[str] = []
    for attempt in range(1, policy.max_repair_attempts + 2):
        response = await backend.acomplete(
            system=system,
            user=json.dumps(
                {
                    "question": question,
                    "schema": schema,
                    "available_parameters": sorted(params),
                    "max_hops": policy.max_graph_hops,
                    "prior_failures": feedback,
                },
                sort_keys=True,
            ),
            temperature=0.0,
            max_tokens=700,
            response_format={"type": "json_object"},
            task_hint="text2cypher",
            mode="pipeline",
            model=model,
        )
        payload = response.json()
        cypher = str(payload.get("cypher", "")).strip()
        violations = validate_text2cypher_fallback(cypher, params=params, policy=policy)
        if violations:
            feedback = list(violations)
            continue
        try:
            await explain(cypher, params)
        except Exception as exc:
            feedback = [f"explain_failed:{type(exc).__name__}"]
            continue
        return Text2CypherResult(
            cypher=cypher,
            params=dict(params),
            attempts=attempt,
            explained=True,
        )
    raise ValueError("text2cypher rejected: " + ",".join(feedback))


__all__ = ["Text2CypherResult", "generate_validated_cypher"]
