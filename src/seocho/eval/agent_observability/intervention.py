"""Causal branch replay and declared interventions, independent of the SDK."""
from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Intervention(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operator: Literal['set_budget', 'set_schedule', 'set_route', 'set_tier',
                      'knock_in_reference', 'replace_plan', 'replace_subanswer']
    subtask: str | None = None
    budget: int | None = Field(default=None, ge=0)
    schedule: Literal['uniform', 'difficulty_proportional'] | None = None
    model_key: str | None = None
    agent: str | None = None
    tier: Literal['T0_PARAMETRIC', 'T1_RETRIEVAL', 'T2_REFERENCE'] | None = None
    plan: dict[str, Any] | None = None
    plan_source: str | None = None
    subanswer: dict[str, Any] | None = None

    @model_validator(mode='after')
    def complete(self) -> Intervention:
        if self.operator not in ('set_schedule', 'replace_plan') and not self.subtask:
            raise ValueError('Subtask required')
        required = {'set_budget': 'budget', 'set_schedule': 'schedule', 'set_route': 'model_key',
                    'set_tier': 'tier', 'replace_plan': 'plan', 'replace_subanswer': 'subanswer'}
        if self.operator in required and getattr(self, required[self.operator]) is None:
            if not ((self.operator == 'set_route' and self.agent is not None) or
                    (self.operator == 'replace_plan' and self.plan_source is not None)):
                raise ValueError('Operator value required')
        return self


class CostLimits(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_tokens: int = Field(gt=0)
    max_usd: float | None = Field(default=None, gt=0)
    # Explicit conservative input reservation and versioned upper-bound prices.
    input_token_reservation: int = Field(default=8192, gt=0)
    price_version: str | None = None
    usd_per_token_upper_bound: float | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def pricing(self) -> CostLimits:
        if self.max_usd is not None and (not self.price_version or self.usd_per_token_upper_bound is None):
            raise ValueError('Dollar cap requires explicit versioned upper-bound price')
        return self


class CostGuard:
    """Reserve conservatively before dispatch; shared across N samples/sweeps."""
    def __init__(self, limits: CostLimits) -> None:
        self.limits = limits
        self.reserved_tokens = 0
        self.observed_tokens = 0
        self.stopped = False

    def reserve(self, hard_output_limit: int, request_bytes: int) -> None:
        # A byte bound is conservative for byte-level tokenizer input. The fixed
        # reservation must also cover framework prompts/schemas; reject oversized inputs.
        if request_bytes > self.limits.input_token_reservation:
            raise ValueError('Input exceeds configured conservative reservation')
        amount = self.limits.input_token_reservation + hard_output_limit
        dollars = (self.reserved_tokens + amount) * (self.limits.usd_per_token_upper_bound or 0)
        if self.stopped or self.reserved_tokens + amount > self.limits.max_tokens or (
                self.limits.max_usd is not None and dollars > self.limits.max_usd):
            self.stopped = True
            raise ValueError('Intervention cost cap exhausted')
        self.reserved_tokens += amount

    def observe(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            if record.get('cache_hit'):
                continue
            usage = record.get('usage', {})
            inp, out = usage.get('input_tokens'), usage.get('output_tokens')
            if inp is None or out is None:
                self.stopped = True  # uncertain work retains its reservation, no new dispatch
            else:
                self.observed_tokens += inp + out
                if self.observed_tokens > self.limits.max_tokens:
                    self.stopped = True


class ReplaySession:
    def __init__(self, snapshot: dict[str, Any], intervention: Intervention, guard: CostGuard) -> None:
        self.snapshot = snapshot
        self.intervention = intervention
        self.guard = guard
        self.used: set[str] = set()
        self.base_run_id = snapshot['run_id']

    def source(self, action: str, task: str | None, agent_id: str) -> dict[str, Any] | None:
        change = self.intervention
        fresh = action in ('synthesize', 'evaluate') or change.operator == 'replace_plan'
        if action == 'solve':
            fresh = fresh or change.operator == 'set_schedule' or (task == change.subtask and change.operator != 'replace_subanswer')
        if fresh:
            return None
        decisions = [e for e in self.snapshot['events'] if e['kind'] == 'decision'
                     and e['action'] == action and e.get('subtask_id') == task and e['agent_id'] == agent_id]
        if len(decisions) != 1:
            raise ValueError('Replay prefix step missing or ambiguous')
        step = decisions[0]['step_id']
        calls = [c for c in self.snapshot['calls'] if c['start']['tags']['step_id'] == step]
        if len(calls) != 1:
            raise ValueError('Replay requires one successful non-streaming call per step')
        source = calls[0]
        if source['call_id'] in self.used:
            raise ValueError('Replay origin reused within one derived run')
        self.used.add(source['call_id'])
        return source

    async def replay(self, source: dict[str, Any], client: httpx.AsyncClient, base: str,
                     tags: dict[str, str], attempts: list[dict[str, str]]) -> str:
        call_id = uuid4().hex
        attempts.append({'call_id': call_id, 'run_id': tags['X-Run-Id'],
                         'step_id': tags['X-Step-Id'], 'agent_id': tags['X-Agent-Id']})
        response = await client.post(base + '/v1/' + source['start']['endpoint'],
            content=source['request'].encode(), headers={**tags, 'Content-Type': 'application/json',
            'X-Call-Id': call_id, 'X-Replay-Origin': source['call_id']})
        response.raise_for_status()
        if response.headers.get('X-Call-Id') != call_id or response.headers.get('X-Cache-Hit') != 'true':
            raise ValueError('Pinned replay did not return cached evidence')
        body = response.json()
        return body['choices'][0]['message']['content']


async def load_snapshot(client: httpx.AsyncClient, recorder_url: str, run_id: str,
                        workspace_id: str) -> dict[str, Any]:
    base = recorder_url.rstrip('/')
    if base.endswith('/v1'):
        base = base[:-3]
    response = await client.get(base + '/runs/' + run_id + '/snapshot', params={'workspace_id': workspace_id})
    response.raise_for_status()
    return response.json()


async def run_intervention(harness: Any, case: Any, condition: Any, snapshot: dict[str, Any],
                           intervention: Intervention, *, samples: int, guard: CostGuard,
                           seed: int) -> list[dict[str, Any]]:
    if samples < 1:
        raise ValueError('Positive N required')
    if intervention.operator == 'replace_plan' and intervention.plan is None:
        source = await load_snapshot(harness.events, harness.experiment.recorder_url,
                                     intervention.plan_source, harness.experiment.workspace_id)
        plans = [e['payload']['output'] for e in source['events'] if e['action'] == 'plan'
                 and e['kind'] == 'realization' and 'output' in e['payload']]
        if len(plans) != 1:
            raise ValueError('Replacement source must have one recorded plan')
        intervention = intervention.model_copy(update={'plan': plans[0]})
    from .harness import HarnessRunError
    root = next(e for e in snapshot['events'] if e['kind'] == 'decision' and e['action'] == 'run')
    payload = root['payload']
    if root['workspace_id'] != harness.experiment.workspace_id or payload['case_id'] != case.case_id or payload['question'] != case.question or payload['condition'] != condition.model_dump() or payload['model_map'] != harness.experiment.models:
        raise ValueError('Base case/condition/model mapping mismatch')
    planned = condition.strategy not in ('vanilla', 'global_budget')
    if not planned and intervention.operator in ('replace_plan', 'set_schedule', 'set_budget'):
        raise ValueError('Intervention requires a planned local-budget strategy')
    if intervention.operator in ('set_budget', 'set_schedule') and condition.strategy not in ('plan_and_budget', 'routed_plan_and_budget'):
        raise ValueError('Intervention requires local-budget strategy')
    results = []
    for sample in range(samples):
        if guard.stopped:
            break
        replay = ReplaySession(snapshot, intervention, guard)
        try:
            result = await harness.run(case, condition, seed, sample, replay=replay)
        except HarnessRunError as exc:
            results.append(exc.report)
            break
        results.append(result)
    return results


async def budget_sweep(harness: Any, case: Any, condition: Any, snapshot: dict[str, Any],
                       subtask: str, budgets: list[int], *, samples: int, guard: CostGuard,
                       seed: int) -> list[dict[str, Any]]:
    if not budgets or any(b < 0 for b in budgets) or len(set(budgets)) != len(budgets):
        raise ValueError('Distinct nonnegative budget levels required')
    results = []
    for budget in budgets:
        if guard.stopped:
            break
        results.extend(await run_intervention(harness, case, condition, snapshot,
            Intervention(operator='set_budget', subtask=subtask, budget=budget),
            samples=samples, guard=guard, seed=seed))
    return results


def frozen_information(session: ReplaySession, task: str | None) -> tuple[list[dict[str, Any]], str] | None:
    change = session.intervention
    if change.operator == 'replace_plan' or (task == change.subtask and change.operator in ('set_tier', 'knock_in_reference')):
        return None
    events = [e for e in session.snapshot['events'] if e['kind'] == 'realization'
              and e['action'] == 'fetch' and e.get('subtask_id') == task]
    if len(events) != 1 or 'evidence' not in events[0]['payload']:
        raise ValueError('Frozen information prefix missing or ambiguous')
    return events[0]['payload']['evidence'], events[0]['event_id']
