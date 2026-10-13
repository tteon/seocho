"""BFCL-inspired FinDER tools: model-selected calls with observable state checks."""

from __future__ import annotations

import asyncio
import json
import re
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import uuid4

from pydantic import BaseModel


class ToolState:
    def __init__(self) -> None:
        self.evidence: dict[str, dict[str, Any]] = {}
        self.numbers: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.assessment: dict[str, Any] | None = None
        self.results: list[dict[str, Any]] = []

    def deliver(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Retain full source state; avoid re-sending unchanged already-visible text."""
        returned = []
        for row in rows:
            previous = self.evidence.get(row["evidence_id"])
            if previous == row:
                returned.append(
                    {
                        "evidence_id": row["evidence_id"],
                        "source": row["source"],
                        "already_delivered": True,
                    }
                )
            else:
                self.evidence[row["evidence_id"]] = row
                returned.append(row)
        return returned

    def extract(
        self, evidence_id: str, value: str, unit: str, period: str
    ) -> dict[str, Any]:
        if evidence_id not in self.evidence:
            raise ValueError("Evidence ID was not returned by a search")
        number = Decimal(value.replace(",", ""))
        if not number.is_finite():
            raise ValueError("Finite number required")
        tokens = re.findall(
            r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w.])",
            self.evidence[evidence_id]["text"],
        )
        if number not in [Decimal(t.replace(",", "")) for t in tokens]:
            raise ValueError("Number does not occur in delivered source")
        receipt = {
            "receipt_id": uuid4().hex,
            "value": str(number),
            "unit": unit,
            "period": period,
            "evidence_id": evidence_id,
            "unit_period_semantics": "not_verified",
        }
        self.numbers[receipt["receipt_id"]] = receipt
        return receipt

    def calculate(self, operation: str, left_id: str, right_id: str) -> dict[str, Any]:
        if left_id not in self.numbers or right_id not in self.numbers:
            raise ValueError("Operand receipt IDs must come from extract_number")
        left, right = self.numbers[left_id], self.numbers[right_id]
        if left["unit"] != right["unit"]:
            raise ValueError("Operand units differ; conversion is not available")
        a, b = Decimal(left["value"]), Decimal(right["value"])
        operations = {
            "subtract": lambda: a - b,
            "add": lambda: a + b,
            "multiply": lambda: a * b,
            "divide": lambda: a / b,
        }
        if operation not in operations:
            raise ValueError("Unsupported operation")
        value = operations[operation]()
        result = {
            "operation": operation,
            "result": str(value),
            "operand_receipts": [left_id, right_id],
            "evidence_refs": sorted({left["evidence_id"], right["evidence_id"]}),
            "units_and_periods": "not_verified",
        }
        self.results.append(result)
        return result

    def metrics(self) -> dict[str, Any]:
        successful = [c for c in self.calls if c["status"] == "completed"]
        keys = [
            json.dumps([c["tool"], c["arguments"]], sort_keys=True) for c in self.calls
        ]
        return {
            "epistemic_assessment": self.assessment,
            "version": "finder-tool-checks-v1",
            "attempted_calls": len(self.calls),
            "successful_calls": len(successful),
            "execution_success_rate": len(successful) / len(self.calls)
            if self.calls
            else None,
            "duplicate_calls": len(keys) - len(set(keys)),
            "invalid_arguments_or_execution_failures": sum(
                c["status"] == "failed" for c in self.calls
            ),
            "delivered_evidence_count": len(self.evidence),
            "operand_receipts": len(self.numbers),
            "completed_calculations": len(self.results),
            "completed_delegations": sum(c["tool"] == "delegate" for c in successful),
            "semantic_tool_relevance": "not_scored",
            "minimal_path_efficiency": "not_scored",
            "state_success": "not_scored_without_precommitted_case_goals",
        }


async def execute_tool_agent(
    *,
    model: Any,
    schema: type[BaseModel],
    prompt: dict[str, Any],
    search: Callable[..., Awaitable[list[Any]]],
    delegate: Callable[..., Awaitable[Any]],
    event: Callable[..., Awaitable[str]],
    parent: str,
    seed: int,
    hard_output_limit: int,
    max_turns: int,
    max_tools: int,
    single_agent: bool = False,
    prompt_json: bool = False,
) -> tuple[Any, ToolState]:
    from agents import Agent, ModelSettings, RunConfig, Runner, function_tool

    state = ToolState()
    if single_agent:
        state.assessment = prompt.get("epistemic_assessment")
        state.evidence.update(
            {
                r["evidence_id"]: r
                for r in prompt.get("initial_evidence", prompt.get("evidence", []))
            }
        )
    delegate_callback = delegate

    async def invoke(
        name: str, arguments: dict, operation: Callable[..., Awaitable[Any]]
    ) -> Any:
        step = uuid4().hex
        record = {
            "tool": name,
            "arguments": arguments,
            "status": "pending",
            "step_id": step,
        }
        state.calls.append(record)
        decision = await event(
            "decision",
            "tool_call",
            step,
            "tool_agent",
            {"tool": name, "arguments": arguments},
            parent=parent,
        )
        try:
            if len(state.calls) > max_tools:
                raise ValueError("Tool call budget exhausted")
            result = await operation()
            record["status"] = "completed"
            record["result"] = result
            await event(
                "realization",
                "tool_call",
                step,
                "tool_agent",
                {"tool": name, "status": "completed", "result": result},
                decision,
                parent,
            )
            return result
        except Exception as exc:
            record["status"] = "failed"
            record["error_type"] = type(exc).__name__
            await event(
                "realization",
                "tool_call",
                step,
                "tool_agent",
                {"tool": name, "status": "failed", "error_type": type(exc).__name__},
                decision,
                parent,
            )
            return {"error": str(exc), "error_type": type(exc).__name__}

    @function_tool(failure_error_function=None)
    async def search_evidence(query: str) -> str:
        """Get fixed original-source passages; returned IDs can be used in other tools."""

        async def action() -> list[dict]:
            rows = await search(query, parent=parent, task="task_1")
            result = [row.model_dump() for row in rows]
            return state.deliver(result)

        return json.dumps(await invoke("search_evidence", {"query": query}, action))

    @function_tool(failure_error_function=None)
    async def extract_number(
        evidence_id: str, value: str, unit: str, period: str
    ) -> str:
        """Record an explicitly present source number. Unit/period association needs semantic checking."""

        async def action() -> dict:
            return state.extract(evidence_id, value, unit, period)

        return json.dumps(
            await invoke(
                "extract_number",
                locals_arguments(
                    evidence_id=evidence_id, value=value, unit=unit, period=period
                ),
                action,
            )
        )

    @function_tool(failure_error_function=None)
    async def calculate(operation: str, left_id: str, right_id: str) -> str:
        """Calculate using operand receipt IDs returned by extract_number; supports add/subtract/multiply/divide."""

        async def action() -> dict:
            return state.calculate(operation, left_id, right_id)

        return json.dumps(
            await invoke(
                "calculate",
                dict(operation=operation, left_id=left_id, right_id=right_id),
                action,
            )
        )

    @function_tool(failure_error_function=None)
    async def delegate(subtask: str) -> str:
        """Ask a financial evidence specialist a subquestion using only already retrieved passages."""

        async def action() -> dict:
            output = await delegate_callback(subtask, list(state.evidence.values()))
            return output.model_dump()

        return json.dumps(await invoke("delegate", {"subtask": subtask}, action))

    agent = Agent(
        name="tool_agent",
        instructions=(
            "Answer only from delivered source evidence. Search when more source evidence is needed. "
            "If an initial epistemic assessment is provided, check its claims against evidence and revise the answer. "
            "Ontology, knowledge_graph and clarification capabilities are unavailable in this arm; report unmet needs. "
            "Choose tools as needed; do not invent evidence IDs or operand receipt IDs. "
            "For a requested numerical calculation, extract source operands then calculate. "
            "Delegate only if it helps; independent tools may run in parallel but dependent calls must wait for returned IDs. "
            "If required evidence is absent, state the missing information; never fabricate values. "
            "Do not reveal private reasoning. Return the required final answer schema."
            + (
                " Return raw JSON with exactly the required keys and no Markdown. Schema: "
                + json.dumps(schema.model_json_schema())
                if prompt_json
                else ""
            )
        ),
        model=model,
        output_type=None if prompt_json else schema,
        tools=[search_evidence, extract_number, calculate]
        + ([] if single_agent else [delegate]),
        model_settings=ModelSettings(
            temperature=0, max_tokens=hard_output_limit, extra_body={"seed": seed}
        ),
    )
    result = await asyncio.wait_for(
        Runner.run(
            agent,
            json.dumps(prompt),
            max_turns=max_turns,
            run_config=RunConfig(tracing_disabled=True),
        ),
        timeout=180,
    )
    return (
        schema.model_validate_json(result.final_output)
        if prompt_json
        else result.final_output
    ), state


def locals_arguments(**kwargs: str) -> dict[str, str]:
    return kwargs
