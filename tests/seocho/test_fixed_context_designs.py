"""Deterministic design contracts, not live-service or timing evidence."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext

from seocho.agent.collaboration import Answer, Claim, Limits
from seocho.eval.fixed_context_designs import (
    DESIGNS, PartialResult, Subtask, run_design, validate_subtasks,
)


def context(name: str) -> ToolContext:
    return ToolContext(context=None, tool_name=name, tool_call_id='test', tool_arguments='{}')


@pytest.mark.parametrize('design', DESIGNS)
def test_design_contracts_share_evidence_and_return_packets(monkeypatch, design):
    async def runner(agent=None, text=None, **kwargs):
        agent = agent or kwargs["starting_agent"]
        text = text or kwargs["input"]
        payload = json.loads(text)
        assert 'expected_answer' not in payload and 'gold' not in payload
        assert payload['evidence'] == [{'evidence_id': 'source:1', 'text': 'visible original source'}]
        if agent.name.startswith('Specialist_') or agent.name == 'SingleSpecialist':
            await asyncio.sleep(0.01)
            return SimpleNamespace(final_output=PartialResult(task_id=payload['task']['task_id'],
                findings='Supported', evidence_refs=['source:1'], missing=[]))
        if agent.tools:
            tasks = [Subtask(task_id=f'task_{i}', question=f'Independent fact {i}') for i in (1, 2)]
            await agent.tools[0].on_invoke_tool(context('declare_plan'),
                                             json.dumps({'tasks': [t.model_dump() for t in tasks]}))
            if design == 'decomposition_only':
                for t in tasks:
                    result = PartialResult(task_id=t.task_id, findings='Supported',
                                           evidence_refs=['source:1'], missing=[])
                    await agent.tools[1].on_invoke_tool(context('record_subtask'),
                                                      json.dumps({'result': result.model_dump()}))
            else:
                jobs = [agent.tools[i].on_invoke_tool(context(f'delegate_task_{i}'),
                        '{"input":"task request"}') for i in (1, 2)]
                await asyncio.gather(*jobs)
        return SimpleNamespace(final_output=Answer(claims=[Claim(text='Answer', evidence_refs=['source:1'])],
                                                   missing_slots=[]))
    monkeypatch.setattr('seocho.eval.fixed_context_designs.Runner.run', runner)
    report = asyncio.run(run_design(model=None, question='q', evidence=[{
        'evidence_id': 'source:1', 'text': 'visible original source'}], design=design,
        workspace_id='w', limits=Limits()))
    assert report['status'] == 'completed', report['error']
    assert report['workspace_id'] == 'w'
    assert len(report['plan']) == (0 if design in ('standard', 'cot') else 2)
    assert len(report['partial_results']) == len(report['plan'])
    assert report['parallel_dispatch_observed'] == (design == 'multi_delegation')
    if design == 'single_delegation':
        assert {e['agent'] for e in report['events'] if e['kind'] == 'delegation_start'} == {'SingleSpecialist'}
    if design == 'multi_delegation':
        assert len({e['agent'] for e in report['events'] if e['kind'] == 'delegation_start'}) == 2


@pytest.mark.parametrize('tasks', [[], [Subtask(task_id='task_1', question='q')],
    [Subtask(task_id='task_1', question='q'), Subtask(task_id='task_1', question='q')],
    [Subtask(task_id='task_1', question=''), Subtask(task_id='task_2', question='q')]])
def test_subtask_ids_count_and_questions_are_validated(tasks):
    with pytest.raises(ValueError):
        validate_subtasks(tasks)


def test_unknown_final_citations_fail_without_publishing_answer(monkeypatch):
    async def runner(*args, **kwargs):
        return SimpleNamespace(final_output=Answer(claims=[Claim(text='Unsupported', evidence_refs=['invented'])],
                                                  missing_slots=[]))
    monkeypatch.setattr('seocho.eval.fixed_context_designs.Runner.run', runner)
    report = asyncio.run(run_design(model=None, question='q', evidence=[{'evidence_id': 'known'}],
                         design='standard', workspace_id='w', limits=Limits()))
    assert report['status'] == 'failed'
    assert report['answer'] is None


def test_incomplete_decomposition_is_rejected(monkeypatch):
    async def runner(*args, **kwargs):
        return SimpleNamespace(final_output=Answer(claims=[Claim(text='Supported', evidence_refs=['known'])],
                                                  missing_slots=[]))
    monkeypatch.setattr('seocho.eval.fixed_context_designs.Runner.run', runner)
    report = asyncio.run(run_design(model=None, question='q', evidence=[{'evidence_id': 'known'}],
                         design='decomposition_only', workspace_id='w', limits=Limits()))
    assert report['status'] == 'failed'
    assert 'Every declared subtask' in report['error']


def test_explicit_overload_retry_charges_each_attempt(monkeypatch):
    import httpx
    from agents import ModelSettings
    from openai import RateLimitError
    from seocho.agent.collaboration import Ledger
    from seocho.eval.fixed_context_designs import RetryingBudgetedModel

    attempts = []
    waits = []

    async def response(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise RateLimitError('overloaded', response=httpx.Response(429,
                request=httpx.Request('POST', 'https://example.invalid/v1')), body={})
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=2, output_tokens=1))

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr('seocho.eval.fixed_context_designs.asyncio.sleep', sleep)
    ledger = Ledger(Limits(max_calls=2))
    model = RetryingBudgetedModel(SimpleNamespace(get_response=response), ledger)
    asyncio.run(model.get_response('instructions', 'input', ModelSettings(), [], None, [], None))
    assert ledger.calls == 2
    assert ledger.usage_missing_calls == 1
    assert waits == [5]
    assert ledger.receipts[0]['status'] == 'failed_or_cancelled'


def test_fixed_source_selection_does_not_use_gold():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / 'examples/graphrag-collaboration/run_fixed_finder.py'
    spec = importlib.util.spec_from_file_location('fixed_finder_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def passage(ref: str, text: str, ordinal: int) -> SimpleNamespace:
        return SimpleNamespace(evidence_id=ref, text=text, ordinal=ordinal,
                               graph_row=lambda: {'evidence_id': ref, 'text': text})

    item = SimpleNamespace(case=SimpleNamespace(question='Operating earnings 2022 2023'),
                           references=['GOLD_A'])
    corpus = SimpleNamespace(selected=[item], filings=[SimpleNamespace(passages=[
        passage('p1', 'Operating earnings 2022 2023', 1),
        passage('p2', 'Unrelated source material', 2)])], gold_mapping={'GOLD_A': 'p2'})
    before = module.fixed_evidence(corpus, count=1)
    item.references = ['GOLD_B']
    corpus.gold_mapping = {'GOLD_B': 'p1'}
    assert module.fixed_evidence(corpus, count=1) == before
    assert before[0]['evidence_id'] == 'p1'
