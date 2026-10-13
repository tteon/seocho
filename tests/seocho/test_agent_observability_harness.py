from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from seocho.eval.agent_observability.config import Condition, Experiment
from seocho.eval.agent_observability.harness import Case, Harness
from seocho.eval.agent_observability.information import Evidence, GoldGuard
from seocho.recorder.config import RecorderConfig
from seocho.recorder.mock_backend import create_mock_app
from seocho.recorder.proxy import create_app


@pytest.mark.asyncio
async def test_all_strategies_tiers_and_causal_pairs(tmp_path: Path) -> None:
    backend = create_mock_app()
    proxy = create_app(RecorderConfig(upstream_url='http://mock/v1', directory=tmp_path),
                       transport=httpx.ASGITransport(app=backend))
    strategies = ['vanilla', 'global_budget', 'planned_vanilla', 'planned_global_budget',
                  'plan_and_budget', 'routed_plan_and_budget']
    tiers = ['T0_PARAMETRIC', 'T1_RETRIEVAL', 'T2_REFERENCE']
    conditions = [Condition(condition_id=f'{strategy}-{tier}', strategy=strategy,
        tier_policy='forced', tier=tier) for strategy in strategies for tier in tiers]
    experiment = Experiment(workspace_id='lab', recorder_url='http://proxy/v1', dataset=tmp_path / 'cases.json',
        evaluation=tmp_path / 'evaluation.json', seeds=[42], models={r: 'mock-fixed' for r in ['planner', 'solver', 'router', 'synthesis', 'evaluator']},
        conditions=conditions)
    case = Case(case_id='fixture', question='What is the reporting period?',
        fixed_passages=[Evidence(evidence_id='fixed-1', text='Reporting period is fiscal 2023.', source='fixture')],
        agent_reference=[Evidence(evidence_id='reference-1', text='Reference describes the reporting period.', source='fixture')],
        gold_answer='GOLD_PRIVATE_SENTINEL')
    harness = Harness(experiment, transport=httpx.ASGITransport(app=proxy))
    try:
        for condition in conditions:
            result = await harness.run(case, condition, 42, 0)
            run = result['run_id']
            store = proxy.state.store
            assert store.audit(run, [a['call_id'] for a in result['attempts']])['coverage_proven']
            assert store.audit_agent_events(run)['valid']
            events = store.agent_events(run)
            steps = {e['step_id'] for e in events}
            for call in store.calls(run_id=run):
                assert call['start']['tags']['step_id'] in steps
            for call in store.calls(run_id=run, domain='inference'):
                raw = store.blobs.get(call['start']['request_blob'], 'inference')
                assert b'GOLD_PRIVATE_SENTINEL' not in raw
            if result['local_budgets']:
                assert sum(result['local_budgets'].values()) == condition.soft_budget
        for router in ('static', 'llm'):
            condition = Condition(condition_id='decided-' + router, strategy='routed_plan_and_budget',
                                  tier_policy='agent_decided', router=router)
            result = await harness.run(case, condition, 7, 0)
            assert proxy.state.store.audit_agent_events(result['run_id'])['valid']
        assert backend.state.requests > 0
    finally:
        await harness.close()
        await proxy.state.client.aclose()
        proxy.state.store.close()


def test_condition_policies_and_gold_guard() -> None:
    with pytest.raises(ValidationError):
        Condition(condition_id='bad', strategy='vanilla', tier_policy='agent_decided', tier='T0_PARAMETRIC')
    with pytest.raises(ValueError, match='LEAK_SUSPECTED'):
        GoldGuard(['Secret   Answer']).check([Evidence(evidence_id='x', text='The SECRET ANSWER is here', source='x')])
    GoldGuard(['secret']).check([Evidence(evidence_id='x', text='ordinary evidence', source='x')])


@pytest.mark.asyncio
async def test_leak_is_blocked_and_failure_visible(tmp_path: Path) -> None:
    backend = create_mock_app()
    proxy = create_app(RecorderConfig(upstream_url='http://mock/v1', directory=tmp_path),
                       transport=httpx.ASGITransport(app=backend))
    condition = Condition(condition_id='reference', strategy='vanilla', tier_policy='forced', tier='T2_REFERENCE')
    experiment = Experiment(workspace_id='lab', recorder_url='http://proxy/v1', dataset=tmp_path,
        evaluation=tmp_path / 'evaluation.json', seeds=[1], models={r: 'mock-fixed' for r in ['planner', 'solver', 'router', 'synthesis', 'evaluator']},
        conditions=[condition])
    case = Case(case_id='leak', question='question', fixed_passages=[],
                agent_reference=[Evidence(evidence_id='x', text='private gold', source='x')], gold_answer='private gold')
    harness = Harness(experiment, transport=httpx.ASGITransport(app=proxy))
    try:
        with pytest.raises(ValueError, match='LEAK_SUSPECTED'):
            await harness.run(case, condition, 1, 0)
        assert backend.state.requests == 0
        rows = proxy.state.store.db.execute('SELECT data FROM agent_events').fetchall()
        assert len(rows) == 4
        assert all('private gold' not in raw for raw, in rows)
        run = json.loads(rows[0][0])['run_id']
        assert proxy.state.store.audit_agent_events(run)['valid']
    finally:
        await harness.close()
        await proxy.state.client.aclose()
        proxy.state.store.close()


@pytest.mark.asyncio
async def test_model_routing_soft_budget_and_independent_samples(tmp_path: Path) -> None:
    backend = create_mock_app()
    proxy = create_app(RecorderConfig(upstream_url='http://mock/v1', directory=tmp_path),
                       transport=httpx.ASGITransport(app=backend))
    condition = Condition(condition_id='route', strategy='routed_plan_and_budget',
                          tier_policy='forced', tier='T0_PARAMETRIC')
    experiment = Experiment(workspace_id='lab', recorder_url='http://proxy/v1', dataset=tmp_path,
        evaluation=tmp_path, seeds=[42], samples=2,
        models={**{r: 'mock-fixed' for r in ['planner', 'solver', 'router', 'synthesis', 'evaluator']},
                'solver_easy': 'mock-small', 'solver_hard': 'mock-large'}, conditions=[condition])
    case = Case(case_id='fixture', question='question', fixed_passages=[], agent_reference=[],
                gold_answer='PRIVATE_SENTINEL')
    harness = Harness(experiment, transport=httpx.ASGITransport(app=proxy))
    try:
        runs = [await harness.run(case, condition, 42, sample) for sample in range(2)]
        assert runs[0]['run_id'] != runs[1]['run_id']
        for result in runs:
            calls = proxy.state.store.calls(run_id=result['run_id'])
            models = {c['start']['model_requested'] for c in calls}
            assert {'mock-small', 'mock-large'} <= models
            assert all(not c['finish']['cache_hit'] for c in calls)
            assert proxy.state.store.audit_agent_events(result['run_id'])['valid']
        global_condition = Condition(condition_id='global', strategy='global_budget',
            tier_policy='forced', tier='T0_PARAMETRIC', soft_budget=1, hard_output_limit=1024)
        result = await harness.run(case, global_condition, 42, 0)
        assert result['global_budget_complied'] is False
        assert result['inference_output_tokens'] == 8
        calls = proxy.state.store.calls(run_id=result['run_id'], domain='inference')
        assert calls[0]['start']['soft_budget'] == '1'
        assert calls[0]['start']['hard_budget'] == 1024
    finally:
        await harness.close()
        await proxy.state.client.aclose()
        proxy.state.store.close()


@pytest.mark.asyncio
async def test_cached_profile_rejected_before_experiment(tmp_path: Path) -> None:
    backend = create_mock_app()
    proxy = create_app(RecorderConfig(upstream_url='http://mock/v1', directory=tmp_path, cache_mode='read_write'),
                       transport=httpx.ASGITransport(app=backend))
    condition = Condition(condition_id='test', strategy='vanilla', tier_policy='forced', tier='T0_PARAMETRIC')
    experiment = Experiment(workspace_id='lab', recorder_url='http://proxy/v1', dataset=tmp_path,
        evaluation=tmp_path, seeds=[42], models={r: 'mock-fixed' for r in ['planner', 'solver', 'router', 'synthesis', 'evaluator']},
        conditions=[condition])
    harness = Harness(experiment, transport=httpx.ASGITransport(app=proxy))
    try:
        with pytest.raises(ValueError, match='cache_mode=off'):
            await harness.run(Case(case_id='x', question='q', fixed_passages=[], agent_reference=[], gold_answer='gold'),
                              condition, 42, 0)
        assert not proxy.state.store.calls()
        assert backend.state.requests == 0
    finally:
        await harness.close()
        await proxy.state.client.aclose()
        proxy.state.store.close()
