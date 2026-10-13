from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from seocho.eval.agent_observability.config import Condition, Experiment
from seocho.eval.agent_observability.harness import Case, Harness
from seocho.eval.agent_observability.information import Evidence
from seocho.eval.agent_observability.signal_store import AnalysisStore, analyze
from seocho.eval.agent_observability.signals import (Run, SignalConfig, Subanswer, derive)
from seocho.recorder.config import RecorderConfig
from seocho.recorder.mock_backend import create_mock_app
from seocho.recorder.proxy import create_app


def sample(run_id: str, tier: str, correct: bool | None, answers: list[str], idx: int = 0) -> Run:
    return Run(run_id,'lab','case',42,idx,'matched-signature',correct,
        [Subanswer(f'task_{i}',f'question about slot {i}',i,text,tier,run_id+str(i)) for i,text in enumerate(answers)],
        {'strategy':'planned_vanilla'})


def test_transitions_silver_entropy_and_first_divergence() -> None:
    runs = [sample('t0a','T0_PARAMETRIC',True,['wrong','stable'],0),
            sample('t0b','T0_PARAMETRIC',False,['different','stable'],1),
            sample('t1a','T1_RETRIEVAL',False,['wrong','stable'],0),
            sample('t1b','T1_RETRIEVAL',False,['different','stable'],1),
            sample('t2a','T2_REFERENCE',True,['correct','stable'],0),
            sample('t2b','T2_REFERENCE',True,['correct','stable'],1)]
    data = derive(runs,SignalConfig())
    assert len(data['silver_labels']) == 2
    assert all(row['status']=='SILVER_NOT_GOLD' for row in data['silver_labels'])
    assert sum(row['retrieval_harm'] for row in data['tier_transitions']) == 1
    deviation = next(row for row in data['first_divergence'] if row['run_id']=='t0a')
    assert deviation['position'] == 0 and deviation['status']=='first_silver_divergence'
    assert any(row['drop_bits']==1 and row['classification']=='knowledge_gap_proxy' for row in data['entropy_change'])
    assert all(not row['semantic'] for row in data['entropy'])


def test_unknown_verdicts_and_cached_support_do_not_become_labels() -> None:
    a = sample('a','T2_REFERENCE',True,['same'])
    b = sample('b','T2_REFERENCE',True,['same'],1)
    b.subtasks[0].origin = a.subtasks[0].origin
    unknown = sample('unknown','T0_PARAMETRIC',None,['different'])
    data = derive([a,b,unknown],SignalConfig())
    assert not data['silver_labels']
    assert not data['tier_transitions']
    assert next(row for row in data['entropy'] if row['tier']=='T2_REFERENCE')['samples']==1
    assert any(row['reason']=='insufficient_or_conflicting_consensus' for row in data['gaps'])


def test_paired_knock_in_rescue_and_observed_reference_mapping() -> None:
    base = sample('base','T0_PARAMETRIC',False,['wrong'])
    derived = sample('derived','T2_REFERENCE',True,['correct'])
    derived.intervention = {'operator':'knock_in_reference','subtask':'task_0'}
    derived.base_run_id = 'base'
    derived.subtasks[0].evidence = [{'evidence_id':'ref','text':'question about slot 0','source':'filing'}]
    data = derive([base,derived],SignalConfig())
    assert data['knock_in'][0]['bottleneck_score']==1
    assert data['knock_in'][0]['samples']==1  # mechanism observation, not a population estimate
    assert data['information_mapping'][0]['method']=='observed_delivery'
    assert data['information_mapping'][0]['delivery_confidence']==1
    assert 'not relevance' in data['information_mapping'][0]['limitation']
    assert not data['silver_labels']  # injected/replayed intervention samples cannot vote


def test_ambiguous_and_missing_prefix_cannot_claim_first_error() -> None:
    high1 = sample('high1','T2_REFERENCE',True,['a','b'])
    high2 = sample('high2','T2_REFERENCE',True,['a','b'],1)
    low = sample('low','T0_PARAMETRIC',False,['a','different'])
    low.subtasks[0].text = 'unrelated xyz phrase'
    data = derive([high1,high2,low],SignalConfig(alignment_threshold=0.95))
    row = next(r for r in data['first_divergence'] if r['run_id']=='low')
    assert row['status']=='unresolved_prefix'
    assert any(r['method']=='unmatched' for r in data['alignment'])


@pytest.mark.asyncio
async def test_real_recorder_analysis_recomputes_without_raw_mutation(tmp_path: Path) -> None:
    backend = create_mock_app()
    proxy = create_app(RecorderConfig(upstream_url='http://mock/v1',directory=tmp_path/'raw'),
                       transport=httpx.ASGITransport(app=backend))
    condition = Condition(condition_id='reference',strategy='planned_vanilla',tier_policy='forced',tier='T2_REFERENCE')
    experiment = Experiment(workspace_id='lab',recorder_url='http://proxy/v1',dataset=tmp_path,evaluation=tmp_path,
        seeds=[42],models={r:'mock-fixed' for r in ['planner','solver','router','synthesis','evaluator']},conditions=[condition])
    case = Case(case_id='fixture',question='question',fixed_passages=[],
        agent_reference=[Evidence(evidence_id='ref',text='supporting reference',source='fixture')],gold_answer='PRIVATE_SENTINEL')
    harness = Harness(experiment,transport=httpx.ASGITransport(app=proxy))
    try:
        await harness.run(case,condition,42,0)
        before = proxy.state.store.db.execute('SELECT * FROM raw_events ORDER BY event_id').fetchall()
        before_events = proxy.state.store.db.execute('SELECT * FROM agent_events ORDER BY event_id').fetchall()
    finally:
        await harness.close()
        await proxy.state.client.aclose()
        proxy.state.store.close()
    result = analyze(tmp_path/'raw',tmp_path/'analysis.duckdb','lab')
    assert result['row_counts']['alignment']==2
    assert result['row_counts']['silver_labels']==0  # Mock judge's verdict remains unknown
    repeated = analyze(tmp_path/'raw',tmp_path/'analysis.duckdb','lab')
    assert repeated['job_id']==result['job_id']
    changed = analyze(tmp_path/'raw',tmp_path/'analysis.duckdb','lab',config=SignalConfig(silver_k=3))
    assert changed['job_id']!=result['job_id']
    store = AnalysisStore(tmp_path/'analysis.duckdb')
    try:
        assert any(row.get('reason')=='unknown_final_correctness' for row in store.rows(result['job_id'],'gaps'))
        assert store.db.execute('SELECT count(*) FROM analysis_jobs').fetchone()[0]==2
        assert b'PRIVATE_SENTINEL' not in json.dumps(store.rows(result['job_id'],'information_mapping')).encode()
    finally:
        store.close()
    from seocho.recorder.store import RecorderStore
    raw = RecorderStore(tmp_path/'raw',read_only=True)
    try:
        assert raw.db.execute('SELECT * FROM raw_events ORDER BY event_id').fetchall()==before
        assert raw.db.execute('SELECT * FROM agent_events ORDER BY event_id').fetchall()==before_events
    finally:
        raw.close()
    with pytest.raises(ValueError,match='separate'):
        analyze(tmp_path/'raw',tmp_path/'raw'/'recorder.duckdb','lab')


@pytest.mark.asyncio
async def test_scripted_recorded_outcomes_drive_all_signals(tmp_path: Path) -> None:
    """Scripted provider verdicts test math/provenance, never FinDER accuracy."""
    backend = create_mock_app()
    upstream_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=backend),base_url='http://mock')

    async def scripted(request: httpx.Request) -> httpx.Response:
        response = await upstream_client.post('/v1/chat/completions',content=request.content)
        result = response.json()
        body = json.loads(request.content)
        instructions = ' '.join(str(m.get('content','')) for m in body['messages'] if m['role'] in ('system','developer'))
        prompt = json.loads(next(m['content'] for m in reversed(body['messages']) if m['role']=='user'))
        if 'ROLE=solver' in instructions:
            known = bool(prompt.get('evidence'))
            result['choices'][0]['message']['content'] = json.dumps({'answer':'right' if known else f'wrong_{prompt["sample"]}',
                'evidence_refs':[r['evidence_id'] for r in prompt.get('evidence',[])]})
        elif 'ROLE=synthesis' in instructions:
            result['choices'][0]['message']['content'] = json.dumps({'answer':prompt['returned_contexts'][0]['answer'],
                'evidence_refs':prompt['returned_contexts'][0]['evidence_refs']})
        elif 'ROLE=evaluator' in instructions:
            result['choices'][0]['message']['content'] = json.dumps({'verdict':'correct' if prompt['answer']['answer']=='right' else 'incorrect',
                'rationale':'Scripted contract fixture only'})
        return httpx.Response(200,json=result)

    proxy = create_app(RecorderConfig(upstream_url='http://mock/v1',directory=tmp_path/'raw'),transport=httpx.MockTransport(scripted))
    conditions = [Condition(condition_id=t,strategy='plan_and_budget',tier_policy='forced',tier=t)
                  for t in ['T0_PARAMETRIC','T1_RETRIEVAL','T2_REFERENCE']]
    experiment = Experiment(workspace_id='lab',recorder_url='http://proxy/v1',dataset=tmp_path,evaluation=tmp_path,
        seeds=[42],models={r:'mock-fixed' for r in ['planner','solver','router','synthesis','evaluator']},conditions=conditions)
    case = Case(case_id='fixture',question='question',fixed_passages=[],
        agent_reference=[Evidence(evidence_id='ref',text='Supporting document.',source='fixture')],gold_answer='PRIVATE_SENTINEL')
    harness = Harness(experiment,transport=httpx.ASGITransport(app=proxy))
    from seocho.eval.agent_observability.intervention import CostGuard, CostLimits, Intervention, load_snapshot, run_intervention
    try:
        base = None
        for idx in range(2):
            for condition in conditions:
                run = await harness.run(case,condition,42,idx)
                if idx==0 and condition.tier=='T0_PARAMETRIC':
                    base = run
        snapshot = await load_snapshot(harness.events,experiment.recorder_url,base['run_id'],'lab')
        await run_intervention(harness,case,conditions[0],snapshot,
            Intervention(operator='knock_in_reference',subtask='task_1'),samples=2,
            guard=CostGuard(CostLimits(max_tokens=1_000_000)),seed=42)
    finally:
        await harness.close()
        await proxy.state.client.aclose()
        proxy.state.store.close()
        await upstream_client.aclose()
    result = analyze(tmp_path/'raw',tmp_path/'analysis.duckdb','lab')
    derived = AnalysisStore(tmp_path/'analysis.duckdb')
    try:
        assert len(derived.rows(result['job_id'],'silver_labels'))==2
        assert len(derived.rows(result['job_id'],'tier_transitions'))==6
        assert derived.rows(result['job_id'],'knock_in')[0]['bottleneck_score']==1
        assert derived.rows(result['job_id'],'knock_in')[0]['samples']==2
        assert any(r['drop_bits']==1 for r in derived.rows(result['job_id'],'entropy_change'))
        assert all(r['status']=='first_silver_divergence' for r in derived.rows(result['job_id'],'first_divergence'))
    finally:
        derived.close()


def test_semantic_vector_plugin_provenance_no_fallback_and_no_input_mutation(tmp_path: Path) -> None:
    from seocho.eval.agent_observability.encoders import PrecomputedEncoder
    path = tmp_path/'vectors.json'
    path.write_text(json.dumps({'model':'toy-fixture','revision':'v1','kind':'semantic','vectors':[
        {'text':'question about slot 0','vector':[0,0,1]},
        {'text':'profit rose','vector':[1,0,0]}, {'text':'earnings increased','vector':[1,0.01,0]},
        {'text':'loss','vector':[0,1,0]}]}))
    encoder = PrecomputedEncoder(path)
    high1 = sample('h1','T2_REFERENCE',True,['profit rose'])
    high2 = sample('h2','T2_REFERENCE',True,['earnings increased'],1)
    low = sample('low','T0_PARAMETRIC',False,['loss'])
    data = derive([high1,high2,low],SignalConfig(agreement='embedding'),encoder)
    assert data['silver_labels'][0]['agreement']=='embedding'
    assert 'sha256:' in data['silver_labels'][0]['encoder']
    assert all(row['semantic'] for row in data['alignment'])
    assert high1.subtasks[0].key is None  # analysis must not mutate caller objects
    with pytest.raises(ValueError,match='missing'):
        encoder.encode('unprovided text')
    path.write_text(json.dumps({'model':'toy','revision':'v1','kind':'semantic',
        'vectors':[{'text':'invalid','vector':[0,0]}]}))
    with pytest.raises(ValueError,match='nonzero'):
        PrecomputedEncoder(path)


def test_single_baseline_does_not_erase_longer_plan_alignment() -> None:
    single = sample('single','T0_PARAMETRIC',None,['one'])
    planned = sample('planned','T2_REFERENCE',True,['one','two'])
    other = sample('other','T2_REFERENCE',True,['one','two'],1)
    data = derive([single,planned,other],SignalConfig())
    assert len(data['silver_labels'])==2
    assert all(row['alignment_key'] for row in data['alignment'])
    assert all(row['anchor_run_id']!='single' for row in data['alignment'])
