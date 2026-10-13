from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from seocho.eval.agent_observability.config import Condition, Experiment
from seocho.eval.agent_observability.harness import Case, Harness
from seocho.eval.agent_observability.information import Evidence
from seocho.eval.agent_observability.intervention import (CostGuard, CostLimits, Intervention,
    budget_sweep, load_snapshot, run_intervention)
from seocho.recorder.config import RecorderConfig
from seocho.recorder.mock_backend import create_mock_app
from seocho.recorder.proxy import create_app


@pytest.mark.asyncio
async def test_repeated_interventions_prefix_hit_downstream_fresh(tmp_path: Path) -> None:
    backend = create_mock_app()
    proxy = create_app(RecorderConfig(upstream_url='http://mock/v1', directory=tmp_path),
                       transport=httpx.ASGITransport(app=backend))
    condition = Condition(condition_id='base', strategy='plan_and_budget', tier_policy='forced', tier='T0_PARAMETRIC')
    experiment = Experiment(workspace_id='lab', recorder_url='http://proxy/v1', dataset=tmp_path,
        evaluation=tmp_path, seeds=[42], models={**{r: 'mock-fixed' for r in ['planner','solver','router','synthesis','evaluator']},
                                                'solver_strong': 'mock-strong'}, conditions=[condition])
    case = Case(case_id='fixture', question='question', fixed_passages=[],
        agent_reference=[Evidence(evidence_id='ref', text='Separate agent supporting document.', source='fixture')],
        gold_answer='PRIVATE_GOLD_SENTINEL')
    harness = Harness(experiment, transport=httpx.ASGITransport(app=proxy))
    try:
        base = await harness.run(case, condition, 42, 0)
        snapshot = await load_snapshot(harness.events, experiment.recorder_url, base['run_id'], 'lab')
        assert 'PRIVATE_GOLD_SENTINEL' not in str(snapshot)
        changes = [Intervention(operator='set_budget', subtask='task_1', budget=64),
                   Intervention(operator='knock_in_reference', subtask='task_1'),
                   Intervention(operator='set_tier', subtask='task_1', tier='T1_RETRIEVAL'),
                   Intervention(operator='set_route', subtask='task_1', model_key='solver_strong'),
                   Intervention(operator='set_route', subtask='task_1', agent='solver_strong'),
                   Intervention(operator='set_schedule', schedule='uniform'),
                   Intervention(operator='replace_subanswer', subtask='task_1', subanswer={'answer':'injected supported partial','evidence_refs':[]}),
                   Intervention(operator='replace_plan', plan={'tasks':[{'task_id':'new_task','question':'new independent question','difficulty':1}]})]
        for change in changes:
            before = backend.state.requests
            guard = CostGuard(CostLimits(max_tokens=1_000_000))
            results = await run_intervention(harness, case, condition, snapshot, change, samples=2, guard=guard, seed=42)
            assert len(results) == 2
            assert all(r['status'] == 'completed' for r in results)
            fresh_count = 0
            for result in results:
                calls = proxy.state.store.calls(run_id=result['run_id'])
                fresh = [c for c in calls if not c['finish']['cache_hit']]
                hits = [c for c in calls if c['finish']['cache_hit']]
                fresh_count += len(fresh)
                if change.operator != 'replace_plan':
                    assert hits
                    assert all(c['finish']['origin_call_id'] in {v['call_id'] for v in snapshot['calls']} for c in hits)
                    assert all(c['finish']['provider_work'] is False and c['finish']['cost_usd'] == 0 for c in hits)
                assert all(c['finish']['provider_work'] for c in fresh)
                assert proxy.state.store.audit_agent_events(result['run_id'])['valid']
                assert proxy.state.store.audit(result['run_id'], [a['call_id'] for a in result['attempts']])['coverage_proven']
                if change.operator == 'knock_in_reference':
                    target = next(c for c in calls if c['start']['tags']['agent_id'] == 'solver:task_1')
                    assert target['start']['tags']['tier'] == 'T2_REFERENCE'
                    assert b'Separate agent supporting document' in proxy.state.store.blobs.get(target['start']['request_blob'], 'inference')
            assert backend.state.requests - before == fresh_count
        successful_sweep = await budget_sweep(harness, case, condition, snapshot, 'task_1', [32,64],
            samples=2, guard=CostGuard(CostLimits(max_tokens=1_000_000)), seed=42)
        assert len(successful_sweep) == 4 and all(r['status'] == 'completed' for r in successful_sweep)
        source_replacement = await run_intervention(harness, case, condition, snapshot,
            Intervention(operator='replace_plan', plan_source=base['run_id']), samples=1,
            guard=CostGuard(CostLimits(max_tokens=1_000_000)), seed=42)
        assert source_replacement[0]['status'] == 'completed'
        assert proxy.state.store.db.execute('SELECT COUNT(*) FROM interventions').fetchone()[0] > 0
        unsafe = await run_intervention(harness, case, condition, snapshot,
            Intervention(operator='replace_subanswer', subtask='task_1',
                         subanswer={'answer':'PRIVATE_GOLD_SENTINEL','evidence_refs':[]}),
            samples=1, guard=CostGuard(CostLimits(max_tokens=1_000_000)), seed=42)
        assert unsafe[0]['status'] == 'failed' and unsafe[0]['flags'] == ['LEAK_SUSPECTED']
        for event in proxy.state.store.agent_events(unsafe[0]['run_id']):
            if event['domain'] == 'inference':
                raw = proxy.state.store.blobs.get(event['payload_blob'], 'inference')
                assert b'PRIVATE_GOLD_SENTINEL' not in raw
        assert proxy.state.store.audit_agent_events(unsafe[0]['run_id'])['valid']
        guard = CostGuard(CostLimits(max_tokens=100))
        before = backend.state.requests
        stopped = await budget_sweep(harness, case, condition, snapshot, 'task_1', [32,64], samples=2, guard=guard, seed=42)
        assert stopped[0]['status'] == 'failed'
        assert guard.stopped
        assert backend.state.requests == before  # prefix served, first new dispatch refused
    finally:
        await harness.close()
        await proxy.state.client.aclose()
        proxy.state.store.close()


def test_dollar_cap_requires_price_and_reserves_before_dispatch() -> None:
    with pytest.raises(ValueError, match='Dollar cap'):
        CostLimits(max_tokens=1000, max_usd=1)
    guard = CostGuard(CostLimits(max_tokens=10000, max_usd=0.001, price_version='fixture-v1',
                                usd_per_token_upper_bound=0.0001, input_token_reservation=100))
    with pytest.raises(ValueError, match='cost cap'):
        guard.reserve(100, 80)
    assert guard.reserved_tokens == 0
    guard = CostGuard(CostLimits(max_tokens=1000, input_token_reservation=100))
    guard.reserve(100, 80)
    guard.observe([{'cache_hit':False,'usage':{}}])
    assert guard.stopped
    assert guard.reserved_tokens == 200


@pytest.mark.asyncio
async def test_pinned_mismatch_cannot_dispatch_and_snapshots_scope_workspace(tmp_path: Path) -> None:
    backend = create_mock_app()
    proxy = create_app(RecorderConfig(upstream_url='http://mock/v1', directory=tmp_path),
                       transport=httpx.ASGITransport(app=backend))
    headers = {'X-Call-Id':'origin', 'X-Workspace-Id':'lab', 'X-Run-Id':'run',
               'X-Agent-Id':'solver', 'X-Step-Id':'step', 'X-Tier':'T0_PARAMETRIC', 'X-Template-Version':'v1'}
    body = {'model':'mock-fixed', 'messages':[{'role':'user','content':'question'}], 'temperature':0}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url='http://proxy') as client:
        try:
            response = await client.post('/v1/chat/completions', headers=headers, json=body)
            assert response.status_code == 200
            response = await client.post('/v1/chat/completions', headers={**headers, 'X-Call-Id':'mismatch',
                'X-Replay-Origin':'origin'}, json={**body,'temperature':0.5})
            assert response.status_code == 409
            assert backend.state.requests == 1
            response = await client.post('/v1/chat/completions', headers={**headers, 'X-Call-Id':'foreign',
                'X-Replay-Origin':'origin','X-Workspace-Id':'other'}, json=body)
            assert response.status_code == 409
            assert backend.state.requests == 1
            response = await client.get('/runs/run/snapshot', params={'workspace_id':'other'})
            assert response.status_code == 404
        finally:
            await proxy.state.client.aclose()
            proxy.state.store.close()
