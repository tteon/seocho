"""OpenAI Agents SDK structured execution; framework imports stay in adapters."""

from __future__ import annotations

import json

from typing import Any

from agents import Agent, ModelSettings, RunConfig, Runner
from pydantic import BaseModel


async def execute_structured(
    *,
    name: str,
    instructions: str,
    model: Any,
    schema: type[BaseModel],
    prompt: str,
    seed: int,
    hard_output_limit: int,
    prompt_json: bool = False,
) -> BaseModel:
    if prompt_json:
        instructions += (
            "\nReturn raw JSON with exactly the required schema keys; no Markdown or additional fields. Schema: "
            + json.dumps(schema.model_json_schema())
        )
    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        output_type=None if prompt_json else schema,
        model_settings=ModelSettings(
            max_tokens=hard_output_limit, temperature=0, extra_body={"seed": seed}
        ),
    )
    result = await Runner.run(
        agent, prompt, max_turns=1, run_config=RunConfig(tracing_disabled=True)
    )
    return (
        schema.model_validate_json(result.final_output)
        if prompt_json
        else result.final_output
    )
