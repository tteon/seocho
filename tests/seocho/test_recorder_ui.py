from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml

from seocho.eval.agent_observability.config import Condition, Experiment
from seocho.eval.agent_observability.harness import Case, Harness
from seocho.recorder.config import RecorderConfig
from seocho.recorder.mock_backend import create_mock_app
from seocho.recorder.proxy import create_app
from seocho.recorder.ui import create_ui
from seocho.recorder.views import Views


@pytest.mark.asyncio
async def test_ui_read_scope_raw_bodies_and_isolated_mock_intervention(tmp_path: Path) -> None:
    condition = Condition(condition_id='baseline',strategy='plan_and_budget',tier_policy='forced',tier='T0_PARAMETRIC')
    public = {'case_id':'fixture','question':'What period?','fixed_passages':[],
        'agent_reference':[{'evidence_id':'ref','text':'The reporting period is fiscal 2023.','source':'fixture'}]}
    (tmp_path/'cases.json').write_text(json.dumps([public]))
    (tmp_path/'evaluation.json').write_text(json.dumps({'fixture':'PRIVATE_GOLD_SENTINEL'}))
    experiment = Experiment(workspace_id='lab',recorder_url='http://proxy/v1',dataset=tmp_path/'cases.json',evaluation=tmp_path/'evaluation.json',
        seeds=[42],models={r:'mock-fixed' for r in ['planner','solver','router','synthesis','evaluator']},conditions=[condition])
    path = tmp_path/'experiment.yaml'
    path.write_text(yaml.safe_dump(experiment.model_dump(mode='json')))
    proxy = create_app(RecorderConfig(upstream_url='http://mock/v1',directory=tmp_path/'raw',cache_namespace='custom-fixture-namespace'),transport=httpx.ASGITransport(app=create_mock_app()))
    harness = Harness(experiment,transport=httpx.ASGITransport(app=proxy))
    try:
        result = await harness.run(Case.model_validate({**public,'gold_answer':'PRIVATE_GOLD_SENTINEL'}),condition,42,0)
    finally:
        await harness.close()
        await proxy.state.client.aclose()
        proxy.state.store.close()
    app = create_ui(tmp_path/'raw','lab',experiment=path,job_output=tmp_path/'jobs')
    fingerprint = app.state.source.source_hash
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
        config = (await client.get('/api/config')).json()
        listed = (await client.get('/api/runs')).json()
        assert listed['total']==1
        graph = (await client.get('/api/runs/'+result['run_id'],params={'limit':1})).json()
        assert graph['total_subtasks']==2 and graph['next_offset']==1
        assert sum(n['kind']=='subtask' for n in graph['nodes'])==1
        assert not any(e['kind']=='dependency' for e in graph['edges'])
        assert graph['audit']['valid']
        call = next(c for c in graph['calls'] if c['domain']=='inference')
        raw = (await client.get(f"/api/runs/{result['run_id']}/calls/{call['call_id']}")).json()
        assert 'PRIVATE_GOLD_SENTINEL' not in raw['bodies']['start']
        evaluation = next(c for c in graph['calls'] if c['domain']=='evaluation')
        assert (await client.get(f"/api/runs/{result['run_id']}/calls/{evaluation['call_id']}")).status_code==404
        privileged = (await client.get(f"/api/runs/{result['run_id']}/calls/{evaluation['call_id']}",params={'domain':'evaluation'})).json()
        assert privileged['evaluation_contains_gold'] and 'PRIVATE_GOLD_SENTINEL' in privileged['bodies']['start']
        assert (await client.get('/api/runs/foreign')).status_code==404
        assert (await client.get('/api/runs',params={'limit':1000})).status_code==422
        html = await client.get('/')
        assert 'frame-ancestors' in html.headers['Content-Security-Policy']
        assert (await client.get('/',headers={'Host':'external.example'})).status_code==403
        body = {'run_id':result['run_id'],'workspace_id':'lab','intervention':{'operator':'knock_in_reference','subtask':'task_1'},'max_tokens':100000}
        assert (await client.post('/api/interventions',json=body)).status_code==403
        headers = {'X-UI-Token':config['mutation_token']}
        assert (await client.post('/api/interventions',json=body,headers={**headers,'Origin':'http://external.example'})).status_code==403
        assert (await client.post('/api/interventions',json={**body,'workspace_id':'foreign'},headers=headers)).status_code==409
        response = await client.post('/api/interventions',json=body,headers=headers)
        assert response.status_code==202
        job_id = response.json()['job_id']
        await asyncio.gather(*app.state.jobs.tasks)
        job = (await client.get('/api/jobs/'+job_id)).json()
        assert job['status']=='completed',job
        assert job['observed_tokens']==84
        branch = await client.get('/api/runs/'+job['results'][0]['run_id'],params={'branch':job_id})
        assert branch.status_code==200
        assert (await client.get('/api/runs',params={'branch':'../raw'})).status_code==404
        assert (await client.get('/static/vendor/d3.min.js')).status_code==200
        limited = await client.post('/api/interventions',json={**body,'max_tokens':1},headers=headers)
        assert limited.status_code==202
        await asyncio.gather(*app.state.jobs.tasks)
        failed_job = (await client.get('/api/jobs/'+limited.json()['job_id'])).json()
        assert failed_job['status']=='failed'
        failure = await client.get('/api/runs/'+failed_job['results'][0]['run_id'],params={'branch':failed_job['job_id']})
        assert failure.status_code==200
    assert Views(tmp_path/'raw','lab').source_hash==fingerprint
    assert len(Views(tmp_path/'raw','lab').index)==1
    assert Views(tmp_path/'raw','foreign').index==[]


def test_ui_jobs_cannot_write_source(tmp_path: Path) -> None:
    from seocho.recorder.ui import MockJobs
    class Source:
        directory = tmp_path
    with pytest.raises(ValueError,match='separate'):
        MockJobs(Source(),None,tmp_path/'jobs')


@pytest.mark.asyncio
async def test_positive_silver_failure_path_and_aggregate_projection(tmp_path: Path) -> None:
    # Reuse a recorded scripted fixture with known outcomes, not invented UI badges.
    from test_agent_signals import test_scripted_recorded_outcomes_drive_all_signals
    await test_scripted_recorded_outcomes_drive_all_signals(tmp_path)
    views = Views(tmp_path/'raw','lab')
    base_id = views.signals['knock_in'][0]['base_run_id']
    failed = next(r for r in views.index if r['run_id']==base_id)
    graph = views.graph(failed['run_id'],limit=1)
    node = next(n for n in graph['nodes'] if n['kind']=='subtask')
    assert node['highlight'] and node['silver_match'] is False
    assert node['knock_in_score']==1 and node['entropy_bits']==1
    assert graph['first_divergence']['status']=='first_silver_divergence'
    assert any(e['highlight'] and e['target']=='final' for e in graph['edges'])
    assert len(views.aggregate['tier_transitions'])==6
    assert views.aggregate['failure_flow']
    assert all(f['value']>0 for f in views.aggregate['failure_flow'])
