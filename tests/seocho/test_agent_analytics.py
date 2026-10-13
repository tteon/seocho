from __future__ import annotations


import pandas as pd
import pytest

from seocho.eval.agent_observability.analytics import (adjust_pvalues,allocation_regret,best_of_n,budget_behavior,
    efficiency,escalation_regret,fairness,fit_bam,mcnemar,minimum_tier,optimal_allocation,
    paired_bootstrap,paired_comparisons,routing_regret)


def frame_rows() -> pd.DataFrame:
    return pd.DataFrame([{'run_id':f'{condition}-{idx}','condition_id':condition,'workspace_id':'lab','case_id':str(idx),
        'seed':42,'sample':0,'correct':correct,'total_tokens_logical':cost,'operator':None}
        for condition,values in [('a',[(True,100),(False,100)]),('b',[(True,50),(True,50)])]
        for idx,(correct,cost) in enumerate(values)])


def test_e3_unit_mean_unknown_cost_not_zero() -> None:
    data = efficiency(frame_rows()).set_index('condition_id')
    assert data.loc['a','E3']==pytest.approx(0.5**2/100)
    assert data.loc['b','A_over_T']==pytest.approx(1/50)
    missing = frame_rows()
    missing.loc[0,'total_tokens_logical']=None
    output = efficiency(missing).set_index('condition_id')
    assert pd.isna(output.loc['a','E3'])
    missing['correct'] = missing['correct'].astype(object)
    missing.loc[1,'correct']=None
    assert pd.isna(efficiency(missing).set_index('condition_id').loc['a','E3'])


def test_paired_statistics_and_multiple_corrections() -> None:
    stats = mcnemar([True,False,False,False],[True,True,True,False])
    assert stats['right_only']==2 and stats['left_only']==0 and stats['p_value']==0.5
    assert mcnemar([True],[True])['p_value']==1
    bootstrap = paired_bootstrap([1,2,3],[2,3,4],draws=200)
    assert bootstrap['effect']==bootstrap['ci_low']==bootstrap['ci_high']==1
    assert adjust_pvalues([0.01,0.04,0.03])==pytest.approx([0.03,0.06,0.06])
    paired = paired_comparisons(frame_rows(),bootstrap_draws=200)
    assert paired.iloc[0]['pairs']==2
    assert paired.iloc[0]['mcnemar_status']=='single_sample_per_unit'
    repeated = pd.concat([frame_rows(),frame_rows().assign(sample=1)],ignore_index=True)
    assert pd.isna(paired_comparisons(repeated,bootstrap_draws=200).iloc[0].p_value)
    with pytest.raises(ValueError):
        mcnemar([0.5],[True])


def test_bam_fit_and_heterogeneous_beta_optimum() -> None:
    budgets = [1,2,4,8,16]
    fitted = fit_bam(budgets,[0.4/b**0.7 for b in budgets])
    assert fitted['c']==pytest.approx(0.4,rel=1e-4)
    assert fitted['beta']==pytest.approx(0.7,rel=1e-4)
    assert fitted['r_squared']>0.99999
    parameters = [{'c':2.,'beta':0.5},{'c':4.,'beta':2.}]
    optimum = optimal_allocation(parameters,20)
    assert sum(optimum)==pytest.approx(20)
    gradients = [p['c']*p['beta']/b**(p['beta']+1) for p,b in zip(parameters,optimum)]
    assert gradients[0]==pytest.approx(gradients[1])
    result = allocation_regret(parameters,[10,10])
    assert result['predicted_error_regret']>=0
    assert result['measured_e3_regret'] is None and result['status']=='model_prediction_only'
    with pytest.raises(ValueError,match='zero errors'):
        fit_bam([1,2,3],[1,0.5,0])


def test_budget_sweep_minimum_tier_and_observed_regrets() -> None:
    data = pd.DataFrame([{'base_run_id':'base','target':'task_1','seed':42,'sample':0,'operator':'set_budget',
        'intervention_budget':b,'correct':c,'target_decoding_tokens_logical':t}
        for b,c,t in [(32,False,20),(64,True,30),(128,True,50)]])
    behavior = budget_behavior(data)
    assert behavior.iloc[0].underthinking_proxy
    assert behavior.iloc[1].overthinking_tokens_proxy==20
    tiers = pd.DataFrame([{'workspace_id':'lab','case_id':'case','seed':42,'sample':0,'signature':'s','tier_policy':'forced',
        'operator':None,'tier':tier,'correct':correct,'total_tokens_logical':cost,'run_id':tier}
        for tier,correct,cost in [('T0_PARAMETRIC',False,10),('T1_RETRIEVAL',True,50),('T2_REFERENCE',True,30)]])
    assert minimum_tier(tiers).iloc[0].tier=='T2_REFERENCE' # cheapest, not blindly lowest ordinal
    regret = escalation_regret('T0_PARAMETRIC',10,'T2_REFERENCE',30,False)
    assert regret['direction']=='under' and regret['under_escalation_failure']
    assert routing_regret('small',{'small':0.1,'large':0.3})['regret']==pytest.approx(0.2)


def test_fairness_and_best_of_n_selection() -> None:
    matched = {'workspace_id':'lab','case_id':'case','seed':42,'model_map':{'solver':'m'},'tool_contract':{'reference':'fixed'},
               'task_fingerprint':'source','budget_policy':'global','soft_budget':100,'hard_output_limit':100,'max_calls':10}
    assert fairness(matched,matched)['matched']
    assert not fairness(matched,{**matched,'hard_output_limit':200})['matched']
    assert not fairness(matched,{**matched,'tool_contract':None})['matched']
    rows = frame_rows().iloc[:2].assign(case_id='same',score=[0.1,0.9])
    selected = best_of_n(rows,selection='precommitted_score',score_column='score')
    assert selected.iloc[0].run_id=='a-1' and not selected.iloc[0].correct
    assert selected.iloc[0].compute_total_tokens==200
    assert selected.iloc[0].total_tokens_logical==200
    assert selected.iloc[0].selected_total_tokens_logical==100
    oracle = best_of_n(rows,selection='oracle_upper_bound')
    assert oracle.iloc[0].correct and oracle.iloc[0].oracle_is_not_deployable
    with pytest.raises(ValueError,match='non-gold'):
        best_of_n(rows,selection='precommitted_score',score_column='correct')


def test_incomplete_pairs_and_single_unit_uncertainty() -> None:
    data = pd.concat([frame_rows(),frame_rows().iloc[:1].assign(sample=1)],ignore_index=True)
    assert paired_comparisons(data,bootstrap_draws=200).iloc[0]['pairs']==1
    result = paired_bootstrap([0],[1],draws=200)
    assert result['ci_low'] is None and result['status']=='insufficient_paired_units'


@pytest.mark.asyncio
async def test_recorder_backed_dataframe_and_report(tmp_path) -> None:
    import httpx
    from seocho.eval.agent_observability.config import Condition, Experiment
    from seocho.eval.agent_observability.harness import Case, Harness
    from seocho.eval.agent_observability.analytics import records_frame
    from seocho.eval.agent_observability.report import ReportSpec, build_report
    from seocho.recorder.config import RecorderConfig
    from seocho.recorder.mock_backend import create_mock_app
    from seocho.recorder.proxy import create_app
    condition = Condition(condition_id='baseline',strategy='vanilla',tier_policy='forced',tier='T0_PARAMETRIC')
    experiment = Experiment(workspace_id='lab',recorder_url='http://proxy/v1',dataset=tmp_path,evaluation=tmp_path,
        seeds=[42],models={r:'mock-fixed' for r in ['planner','solver','router','synthesis','evaluator']},conditions=[condition])
    proxy = create_app(RecorderConfig(upstream_url='http://mock/v1',directory=tmp_path/'raw'),
        transport=httpx.ASGITransport(app=create_mock_app()))
    harness = Harness(experiment,transport=httpx.ASGITransport(app=proxy))
    try:
        await harness.run(Case(case_id='fixture',question='Question',fixed_passages=[],agent_reference=[],gold_answer='PRIVATE'),condition,42,0)
    finally:
        await harness.close()
        await proxy.state.client.aclose()
        proxy.state.store.close()
    frame,gaps,metadata = records_frame(tmp_path/'raw','lab')
    assert len(frame)==1 and frame.iloc[0].total_tokens_logical==28
    assert frame.iloc[0].evaluator_total_tokens==28
    assert frame.iloc[0].correct is None and frame.iloc[0].task_fingerprint
    assert pd.isna(efficiency(frame).iloc[0].E3)
    assert 'unknown_final_correctness' in gaps.reason.tolist()
    report = build_report(ReportSpec(name='Mock contract fixture',workspace_id='lab',directory=tmp_path/'raw',output=tmp_path/'report',bootstrap_draws=100))
    assert report['source']['source_hash']==metadata['source_hash']
    assert all(__import__('pathlib').Path(path).is_file() for path in report['figures'])
    assert records_frame(tmp_path/'raw','lab')[2]['source_hash']==metadata['source_hash']
