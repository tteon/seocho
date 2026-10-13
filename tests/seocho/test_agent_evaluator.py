from pathlib import Path
import json

import httpx
import pytest

from seocho.eval.agent_observability.evaluator import (
    DetailedEvaluation, Dimension, ClaimCheck, Calculation, EvaluationSpec,
    load_evaluation, normalize_verdict, validate_receipts)
from seocho.eval.agent_observability.config import Experiment, Condition
from seocho.eval.agent_observability.harness import Case, Harness
from seocho.eval.agent_observability.information import Evidence
from seocho.recorder.proxy import create_app
from seocho.recorder.config import RecorderConfig
from seocho.recorder.mock_backend import create_mock_app


def test_reasoning_label_is_independent_of_type_and_legacy_gold() -> None:
    _, meta = load_evaluation({'gold_answer': 'x', 'metadata': {'reasoning_required': True, 'reasoning_type': 'None'}})
    assert meta.reasoning_required is True
    assert load_evaluation('legacy')[1].reasoning_required is None
    assert normalize_verdict(' Correct ') is True
    assert normalize_verdict('mock_contract_only') is None


def test_receipts_reject_wrong_calculation_and_fabricated_quote() -> None:
    dim = Dimension(verdict='unknown', rationale='unknown')
    judge = DetailedEvaluation(verdict='unknown', rationale='unknown',
        **{k: dim for k in ('answer_equivalence','evidence_support','citation_validity','reasoning_validity','units_and_periods','abstention')},
        claims=[ClaimCheck(claim='x', evidence_id='a', quote='invented', support='supported')],
        calculations=[Calculation(operation='subtract', operands=['2294','1661'], result='632', unit='million', evidence_ids=['a'])])
    result = validate_receipts(judge,[{'evidence_id':'a','text':'real passage'}])
    assert result['quotes'][0]['exact_quote_valid'] is False
    assert result['calculations'][0]['arithmetic_correct'] is False
    assert result['calculations'][0]['operand_source_support'] == 'not_verified'
    judge.calculations[0].result='633'
    assert validate_receipts(judge,[])['calculations'][0]['arithmetic_correct'] is True
    judge.calculations[0].operation='divide'; judge.calculations[0].operands=['1','0']
    assert validate_receipts(judge,[])['calculations'][0]['arithmetic_correct'] is None


@pytest.mark.asyncio
async def test_v2_reference_and_labels_stay_evaluation_only(tmp_path: Path) -> None:
    backend=create_mock_app()
    proxy=create_app(RecorderConfig(upstream_url='http://mock/v1',directory=tmp_path),transport=httpx.ASGITransport(app=backend))
    condition=Condition(condition_id='standard',strategy='vanilla',tier_policy='forced',tier='T1_RETRIEVAL')
    exp=Experiment(workspace_id='lab',recorder_url='http://proxy/v1',dataset=tmp_path/'cases',evaluation=tmp_path/'gold',seeds=[42],models={r:'mock-fixed' for r in ('planner','solver','router','synthesis','evaluator')},conditions=[condition],evaluator_version='v2')
    case=Case(case_id='x',question='q',fixed_passages=[Evidence(evidence_id='a',text='real passage',source='fixture')],agent_reference=[],gold_answer='GOLD_PRIVATE_SENTINEL',evaluation_metadata=EvaluationSpec(domain='PrivateDomain',reasoning_required=True,reasoning_type='None',gold_references=['GOLD_REF_PRIVATE_SENTINEL']))
    harness=Harness(exp,transport=httpx.ASGITransport(app=proxy))
    try:
        result=await harness.run(case,condition,42,0)
        assert result['evaluation']['verdict']=='unknown'
        store=proxy.state.store
        assert store.audit_agent_events(result['run_id'])['valid']
        for call in store.calls(domain='inference'):
            body=store.blobs.get(call['start']['request_blob'],'inference').decode()
            assert 'PRIVATE_SENTINEL' not in body and 'PrivateDomain' not in body
        for event in store.agent_events(result['run_id']):
            if event['domain']=='inference':
                body=store.blobs.get(event['payload_blob'],'inference').decode()
                assert 'PRIVATE_SENTINEL' not in body and 'PrivateDomain' not in body
        evaluation=store.calls(domain='evaluation')[0]
        body=json.loads(store.blobs.get(evaluation['start']['request_blob'],'evaluation'))
        assert 'GOLD_REF_PRIVATE_SENTINEL' in json.dumps(body)
    finally:
        await harness.close(); await proxy.state.client.aclose();proxy.state.store.close()


def test_strata_keep_false_and_unknown_separate() -> None:
    import pandas as pd
    from seocho.eval.agent_observability.analytics import stratified_quality
    dimensions = ('answer_equivalence','evidence_support','citation_validity','reasoning_validity','units_and_periods','abstention')
    base = {'domain':'Financials','reasoning_type':'None','evaluator_version':'v2','harness_backend':'openai_agents','comparison_id':'a','workspace_id':'lab','case_id':'x','seed':42,**{d:'unknown' for d in dimensions}}
    rows = [{**base,'reasoning_required':False,'correct':True},
            {**base,'case_id':'y','reasoning_required':None,'correct':None}]
    result = stratified_quality(pd.DataFrame(rows))
    assert set(result.reasoning_label)=={'false','unknown'}
    assert result[result.reasoning_label=='false'].accuracy.iloc[0]==1
    assert pd.isna(result[result.reasoning_label=='unknown'].accuracy.iloc[0])
