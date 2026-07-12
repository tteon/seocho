from __future__ import annotations

import asyncio
import json

from seocho.query.text2cypher import generate_validated_cypher
from seocho.query.workload_compiler import Text2CypherFallbackPolicy
from seocho.store.llm import LLMResponse


class Backend:
    def __init__(self) -> None:
        self.calls = 0

    async def acomplete(self, **kwargs):
        self.calls += 1
        cypher = (
            "MATCH (e:Unknown)-[:BAD]->(n) RETURN n LIMIT $limit"
            if self.calls == 1
            else "MATCH (i:ExchangeIntent {id:$intent_id,workspace:$workspace_id})"
            "-[:HAS_EVENT]->(e:ExchangeMemoryEvent) "
            "RETURN e.step AS step LIMIT $limit"
        )
        return LLMResponse(text=json.dumps({"cypher": cypher}))


def test_generated_query_is_repaired_validated_and_explained() -> None:
    explained = []

    async def explain(cypher, params):
        explained.append((cypher, params))

    policy = Text2CypherFallbackPolicy(
        allowed_labels=("ExchangeIntent", "ExchangeMemoryEvent"),
        allowed_relationships=("HAS_EVENT", "NEXT"),
        allowed_properties=("id", "workspace", "step"),
        workspace_property="workspace",
        required_parameters=("workspace_id", "intent_id"),
        max_repair_attempts=1,
    )
    result = asyncio.run(
        generate_validated_cypher(
            question="show event history",
            schema={"labels": policy.allowed_labels, "relationships": policy.allowed_relationships},
            params={"workspace_id": "ws", "intent_id": "i", "limit": 10},
            policy=policy,
            backend=Backend(),
            model="test",
            explain=explain,
        )
    )
    assert result.attempts == 2
    assert result.explained is True
    assert len(explained) == 1
