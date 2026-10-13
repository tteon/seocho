"""Live MARA agent-as-tool context/return probe; no graph service required."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from typing import Any
from pathlib import Path
from uuid import uuid4

from agents import Agent, ModelSettings, RunConfig, Runner, function_tool
from pydantic import BaseModel, ConfigDict

from seocho.agent.collaboration import BudgetedModel, Ledger, Limits


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context_marker: str
    tool_reply: str


async def probe(model_name: str, output: Path, recorder_url: str) -> int:
    if not recorder_url:
        raise ValueError('Live probes require --recorder-url; direct provider bypass is disabled')
    context_marker, tool_reply = 'context-' + uuid4().hex, 'reply-' + uuid4().hex
    events: list[dict[str, str]] = []
    ledger = Ledger(Limits(max_calls=8, max_tool_calls=4, max_output_tokens=1024, timeout_seconds=90))
    config = RunConfig(tracing_disabled=True, trace_include_sensitive_data=False)
    from seocho.recorder.adapters.openai_agents import recorded_client
    delegate, client = recorded_client(recorder_url=recorder_url, model=model_name,
        workspace_id='mara-probe', run_id=uuid4().hex, tier='T2_REFERENCE',
        template_version='mara-tool-probe-v1', timeout=90)
    try:

        @function_tool(failure_error_function=None)
        async def read_probe(context_marker_received: str) -> str:
            """Read the probe reply for the exact marker received from the manager."""
            ledger.reserve_tool()
            if context_marker_received != context_marker:
                raise ValueError('Specialist did not receive the exact manager context marker')
            events.append({'kind': 'specialist_context_verified', 'marker': context_marker_received})
            return json.dumps({'context_marker': context_marker_received, 'tool_reply': tool_reply})

        specialist = Agent(name='ContextSpecialist',
            instructions='Read the context marker in your input. Call read_probe with that exact marker. '
                         'Return its context_marker and tool_reply without modification.',
            model=BudgetedModel(delegate, ledger, json_object_mode=True, role='ContextSpecialist'),
            tools=[read_probe], output_type=ProbeResult,
            model_settings=ModelSettings(parallel_tool_calls=False))

        async def extract(result: Any) -> str:
            value = result.final_output
            if value.context_marker != context_marker or value.tool_reply != tool_reply:
                raise ValueError('Specialist reply did not match local probe')
            events.append({'kind': 'specialist_reply_verified', 'reply': value.tool_reply})
            return value.model_dump_json()

        tool = specialist.as_tool(tool_name='ask_specialist',
            tool_description='Send the context marker to the specialist and receive its probe result.',
            custom_output_extractor=extract, run_config=config, max_turns=8, failure_error_function=None)
        original = tool.on_invoke_tool

        async def invoke(context: Any, arguments: str) -> str:
            ledger.reserve_tool()
            events.append({'kind': 'manager_delegation'})
            return await original(context, arguments)

        tool.on_invoke_tool = invoke
        manager = Agent(name='ContextManager',
            instructions='Call ask_specialist with the context marker from the user input. '
                         'Then return the exact context_marker and tool_reply received from the specialist. '
                         'Do not guess the reply; only the specialist tool can obtain it.',
            model=BudgetedModel(delegate, ledger, json_object_mode=True, role='ContextManager'),
            tools=[tool], output_type=ProbeResult,
            model_settings=ModelSettings(parallel_tool_calls=False))
        answer = None
        error = None
        try:
            result = await asyncio.wait_for(Runner.run(manager,
                json.dumps({'context_marker': context_marker}), max_turns=8, run_config=config), timeout=90)
            answer = result.final_output.model_dump()
            if answer != {'context_marker': context_marker, 'tool_reply': tool_reply}:
                raise ValueError('Manager final response did not preserve the specialist reply')
            events.append({'kind': 'manager_reply_verified'})
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({'live': True, 'model': model_name, 'graph_service': None,
            'status': 'passed' if error is None else 'failed', 'error': error,
            'answer': answer, 'events': events, 'recording_attempts': delegate.attempts, 'budget': ledger.summary()}, indent=2) + '\n')
        print(f"{'PASSED' if error is None else 'FAILED'}: {output}; model calls={ledger.calls}")
        return int(error is not None)
    finally:
        await client.close()



class ParallelResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    left: ProbeResult
    right: ProbeResult


async def parallel_probe(model_name: str, output: Path, recorder_url: str) -> int:
    """Let a supervisor decompose two independent requests and delegate concurrently."""
    if not recorder_url:
        raise ValueError('Live probes require --recorder-url; direct provider bypass is disabled')
    expected = {side: ProbeResult(context_marker='context-' + uuid4().hex,
                                tool_reply='reply-' + uuid4().hex) for side in ('left', 'right')}
    ledger = Ledger(Limits(max_calls=12, max_tool_calls=6, max_output_tokens=2048, timeout_seconds=120))
    events: list[dict[str, Any]] = []
    intervals: dict[str, tuple[float, float]] = {}
    config = RunConfig(tracing_disabled=True, trace_include_sensitive_data=False)
    from seocho.recorder.adapters.openai_agents import recorded_client
    delegate, client = recorded_client(recorder_url=recorder_url, model=model_name,
        workspace_id='mara-probe', run_id=uuid4().hex, tier='T2_REFERENCE',
        template_version='mara-tool-probe-v1', timeout=120)
    try:

        def specialist_tool(side: str) -> Any:
            @function_tool(failure_error_function=None)
            async def read_probe(context_marker_received: str) -> str:
                """Read a private reply using the exact context marker delegated by the supervisor."""
                ledger.reserve_tool()
                if context_marker_received != expected[side].context_marker:
                    raise ValueError(f'{side}: delegated context mismatch')
                events.append({'kind': 'specialist_context_verified', 'side': side})
                return expected[side].model_dump_json()

            specialist = Agent(name=f'Specialist_{side}',
                instructions='Read your delegated task and context marker. Call read_probe with the exact '
                             'marker, then return its result unchanged.',
                model=BudgetedModel(delegate, ledger, json_object_mode=True, role=f'Specialist_{side}'),
                tools=[read_probe], output_type=ProbeResult,
                model_settings=ModelSettings(parallel_tool_calls=False))

            async def extract(result: Any) -> str:
                if result.final_output != expected[side]:
                    raise ValueError(f'{side}: specialist reply mismatch')
                return result.final_output.model_dump_json()

            tool = specialist.as_tool(tool_name=f'ask_{side}', tool_description=f'Perform the {side} subtask.',
                custom_output_extractor=extract, run_config=config, max_turns=6, failure_error_function=None)
            original = tool.on_invoke_tool

            async def invoke(context: Any, arguments: str) -> str:
                ledger.reserve_tool()
                started = time.monotonic()
                events.append({'kind': 'delegation_start', 'side': side,
                               'seconds': started - ledger.started, 'arguments': json.loads(arguments)})
                result = await original(context, arguments)
                ended = time.monotonic()
                intervals[side] = (started, ended)
                events.append({'kind': 'delegation_return', 'side': side, 'seconds': ended - ledger.started})
                return result

            tool.on_invoke_tool = invoke
            return tool

        manager = Agent(name='ParallelSupervisor',
            instructions='Decompose the user request into independent left and right subtasks. '
                         'Send each task description and its original context marker to the matching '
                         'ask_left or ask_right agent tool. Call BOTH tools in the SAME turn so they '
                         'execute in parallel. Neither subtask depends on the other. '
                         'Collect both specialist replies and return them as left and right unchanged.',
            model=BudgetedModel(delegate, ledger, json_object_mode=True, role='ParallelSupervisor'),
            tools=[specialist_tool('left'), specialist_tool('right')], output_type=ParallelResult,
            model_settings=ModelSettings(parallel_tool_calls=True))
        answer, error = None, None
        overlap = 0.0
        try:
            payload = {side: {'request': f'Retrieve the private probe reply for {side}',
                             'context_marker': value.context_marker} for side, value in expected.items()}
            result = await asyncio.wait_for(Runner.run(manager, json.dumps(payload),
                max_turns=6, run_config=config), timeout=120)
            answer = result.final_output.model_dump()
            if answer != {side: value.model_dump() for side, value in expected.items()}:
                raise ValueError('Supervisor final aggregation mismatch')
            left, right = intervals['left'], intervals['right']
            overlap = max(0.0, min(left[1], right[1]) - max(left[0], right[0]))
            if overlap <= 0:
                raise ValueError('Delegations completed sequentially; parallel execution was not observed')
            events.append({'kind': 'supervisor_aggregation_verified'})
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({'live': True, 'model': model_name, 'graph_service': None,
            'design': 'supervisor_parallel_two_specialists', 'status': 'passed' if error is None else 'failed',
            'error': error, 'answer': answer, 'delegation_overlap_seconds': overlap,
            'events': events, 'recording_attempts': delegate.attempts, 'budget': ledger.summary()}, indent=2) + '\n')
        print(f"{'PASSED' if error is None else 'FAILED'}: {output}; model calls={ledger.calls}")
        return int(error is not None)
    finally:
        await client.close()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recorder-url', default=os.getenv('SEOCHO_RECORDER_URL'))
    parser.add_argument('--model', default='MiniMax-M2.5')
    parser.add_argument('--parallel', action='store_true', help='Supervisor decomposes and delegates to two specialists')
    parser.add_argument('--output', type=Path, default=Path('outputs/collaboration-mara/tool-smoke.json'))
    args = parser.parse_args()
    try:
        return asyncio.run((parallel_probe if args.parallel else probe)(args.model, args.output, args.recorder_url))
    except ValueError as exc:
        parser.error(str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
