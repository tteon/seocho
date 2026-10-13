"""Population metrics on audited Recorder evidence; unknowns stay unknown."""
from __future__ import annotations

import json
import math
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit, brentq
from scipy.stats import binomtest

from ...recorder.store import RecorderStore, canonical
from .signal_store import load_runs

Cost = Literal['decoding_tokens','total_tokens','cost_usd','latency']


def _sum_known(values: list[Any]) -> float | None:
    return float(sum(values)) if values and all(v is not None and math.isfinite(float(v)) for v in values) else None


def records_frame(directory: Path, workspace_id: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    store = RecorderStore(directory,read_only=True)
    rows = []
    try:
        runs,gaps,fingerprint = load_runs(store,workspace_id)
        all_calls = {c['call_id']:c for c in store.calls()}
        for run in runs:
            events = store.agent_events(run.run_id)
            calls = store.calls(run_id=run.run_id,domain='inference')
            evaluation = store.calls(run_id=run.run_id,domain='evaluation')
            def original(call: dict[str,Any]) -> dict[str,Any]:
                seen = set()
                while call['finish'].get('origin_call_id'):
                    if call['call_id'] in seen:
                        raise ValueError('Cyclic cost origin')
                    seen.add(call['call_id'])
                    call = all_calls[call['finish']['origin_call_id']]
                return call
            logical = [original(c)['finish'] for c in calls]
            physical = [c['finish'] for c in calls if not c['finish'].get('cache_hit')]
            times = {action:[datetime.fromisoformat(e['received_at']) for e in events
                            if e['action']==action and e['kind']=='decision'] for action in ('run','evaluate')}
            latency = (min(times['evaluate'])-min(times['run'])).total_seconds()*1000 if times['run'] and times['evaluate'] else None
            row = {'run_id':run.run_id,'workspace_id':run.workspace_id,'case_id':run.case_id,'seed':run.seed,
                'sample':run.sample,'condition_id':events[0]['condition_id'],'signature':run.signature,
                'correct':run.correct,'tier':run.tier,'strategy':run.condition['strategy'],
                'tier_policy':run.condition['tier_policy'],'delegation':run.condition['delegation'],
                'soft_budget':run.condition['soft_budget'],'hard_output_limit':run.condition['hard_output_limit'],
                'max_calls':run.condition['max_calls'],'base_run_id':run.base_run_id,
                'operator':run.intervention.get('operator') if run.intervention else None,
                'target':run.intervention.get('subtask') if run.intervention else None,
                'intervention_budget':run.intervention.get('budget') if run.intervention else None,
                'cache_hits':sum(c['finish'].get('cache_hit',False) for c in calls),
                'inference_calls':len(calls),'evaluator_calls':len(evaluation),'latency_physical':latency,
                'latency_logical':latency if not any(c['finish'].get('cache_hit') for c in calls) else None}
            for name,finishes in [('logical',logical),('physical',physical)]:
                inp = [f.get('usage',{}).get('input_tokens') for f in finishes]
                out = [f.get('usage',{}).get('output_tokens') for f in finishes]
                row['decoding_tokens_'+name] = _sum_known(out) if finishes else 0
                row['total_tokens_'+name] = _sum_known([a+b if a is not None and b is not None else None for a,b in zip(inp,out)]) if finishes else 0
                row['cost_usd_'+name] = _sum_known([f.get('cost_usd') for f in finishes]) if finishes else 0
            row['evaluator_total_tokens'] = _sum_known([f['finish'].get('usage',{}).get('input_tokens',0)+f['finish'].get('usage',{}).get('output_tokens',0)
                                                       if f['finish'].get('usage',{}).get('input_tokens') is not None and f['finish'].get('usage',{}).get('output_tokens') is not None else None for f in evaluation])
            row['subtasks'] = [{'task_id':s.task_id,'tier':s.tier,'origin':s.origin} for s in run.subtasks]
            run_payload = next(json.loads(store.blobs.get(e['payload_blob'],'inference')) for e in events if e['action']=='run' and e['kind']=='decision')
            row['model_map'] = run_payload['model_map']
            row['tool_contract'] = run_payload.get('information_contract')
            row['task_fingerprint'] = run_payload.get('case_fingerprint')
            row['budget_policy'] = 'none' if run.condition['strategy'] in ('vanilla','planned_vanilla') else ('global' if run.condition['strategy'] in ('global_budget','planned_global_budget') else 'local')
            solve_steps = {e['step_id'] for e in events if e['action']=='solve'}
            target_calls = [c for c in calls if c['start']['tags'].get('subtask_id')==row['target'] and c['start']['tags']['step_id'] in solve_steps] if row['target'] else []
            models = {s.task_id:{c['start']['model_requested'] for c in calls if c['start']['tags'].get('subtask_id')==s.task_id and c['start']['tags']['step_id'] in solve_steps} for s in run.subtasks}
            row['subtask_models'] = {task:next(iter(values)) if len(values)==1 else None for task,values in models.items()}
            row['actual_target_model'] = row['subtask_models'].get(row['target'])
            row['comparison_signature'] = hashlib.sha256(canonical({'condition':{k:v for k,v in run.condition.items() if k not in ('condition_id','tier','tier_policy')},'models':row['model_map'],'task':row['task_fingerprint']})).hexdigest()
            row['target_decoding_tokens_logical'] = _sum_known([original(c)['finish'].get('usage',{}).get('output_tokens') for c in target_calls]) if target_calls else None
            context = dict(run.intervention) if run.intervention else None
            if context and context['operator'] in ('replace_plan','replace_subanswer'):
                context['replacement_hash'] = hashlib.sha256(canonical([json.loads(store.blobs.get(e['payload_blob'],e['domain'])) for e in events if e['kind']=='realization' and ((context['operator']=='replace_plan' and e['action']=='plan') or (context['operator']=='replace_subanswer' and e['action']=='delegate' and e.get('subtask_id')==context.get('subtask')))])).hexdigest()
            row['comparison_id'] = row['condition_id'] if not context else row['condition_id']+'@'+hashlib.sha256(canonical(context)).hexdigest()[:12]
            budgets = [json.loads(store.blobs.get(e['payload_blob'],'inference')).get('allocations') for e in events if e['action']=='budget' and e['kind']=='realization']
            row['allocated_budgets'] = budgets[0] if len(budgets)==1 else None
            judgments = [json.loads(store.blobs.get(e['payload_blob'], 'evaluation')) for e in events
                         if e['action']=='evaluate' and e['kind']=='realization' and e['domain']=='evaluation']
            evaluation_payload = judgments[0] if len(judgments)==1 else {}
            metadata = evaluation_payload.get('evaluation_metadata', {})
            row['domain'] = metadata.get('domain', 'unknown')
            row['reasoning_required'] = metadata.get('reasoning_required')
            row['reasoning_type'] = metadata.get('reasoning_type', 'unknown')
            row['evaluator_version'] = evaluation_payload.get('rubric_version', 'unknown')
            row['harness_backend'] = run_payload.get('experiment_config', {}).get('harness_backend', 'openai_agents')
            for dimension in ('answer_equivalence','evidence_support','citation_validity','reasoning_validity','units_and_periods','abstention','tool_relevance','dependency_handling','handoff_quality','context_use','contradiction_handling','synthesis_faithfulness'):
                row[dimension] = evaluation_payload.get('output', {}).get(dimension, {}).get('verdict', 'unknown')
            tool_checks = [json.loads(store.blobs.get(e['payload_blob'], 'evaluation')) for e in events
                           if e['action']=='verify_receipts' and e['kind']=='realization' and e['agent_id']=='tool_checker']
            row['tool_metrics'] = tool_checks[0] if len(tool_checks)==1 else None
            row['handoff_judgment_conflict'] = bool(row['tool_metrics'] is not None and row['tool_metrics']['completed_delegations']==0 and row['handoff_quality']!='not_applicable')
            row['raw_handoff_quality'] = row['handoff_quality']
            if row['handoff_judgment_conflict']:
                row['handoff_quality'] = 'unknown'
            row['status'] = 'completed'
            rows.append(row)
    finally:
        store.close()
    return pd.DataFrame(rows),pd.DataFrame(gaps),{'source_hash':fingerprint,'workspace_id':workspace_id,
        'evaluation_cost_policy':'separate; E3 excludes evaluator','latency_unit':'milliseconds',
        'cost_basis':'logical includes inherited prefix token work; physical counts new dispatch work',
        'derived_latency_policy':'logical latency unknown for replay; actual physical wall time retained',
        'population_scope':'audited completed runs only; excluded source runs remain in gaps, not a full intention-to-treat estimate'}


def efficiency(frame: pd.DataFrame, cost: Cost = 'total_tokens', basis: Literal['logical','physical'] = 'logical') -> pd.DataFrame:
    rows = []
    if frame.empty:
        return pd.DataFrame(rows)
    column = cost+'_'+basis
    for condition,group in frame.groupby('comparison_id' if 'comparison_id' in frame else 'condition_id',sort=True):
        known = group.dropna(subset=['correct',column])
        complete = len(known)==len(group)
        units = known.groupby(['workspace_id','case_id','seed'],sort=True).agg(A=('correct','mean'),T=(column,'mean'))
        outcomes = group.dropna(subset=['correct']).groupby(['workspace_id','case_id','seed']).correct.mean()
        costs = group.dropna(subset=[column]).groupby(['workspace_id','case_id','seed'])[column].mean()
        a = float(outcomes.mean()) if len(outcomes) else None
        t = float(costs.mean()) if len(costs) else None
        valid = complete and t is not None and t>0
        rows.append({'condition_id':condition,'runs':len(group),'eligible_runs':len(known),'paired_units':len(units),
            'A':a,'T':t,'cost':cost,'basis':basis,'E3':a*a/t if valid else None,
            'A_over_T':a/t if valid else None,'status':'observed' if valid else 'missing_or_nonpositive_cost_or_outcome'})
    return pd.DataFrame(rows)


def error_decomposition(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if frame.empty:
        return pd.DataFrame(rows)
    base = frame[frame.operator.isna()] if 'operator' in frame else frame
    for key,group in base.groupby(['workspace_id','case_id','seed','sample','signature'],sort=True):
        tiers = {tier:part for tier,part in group.groupby('tier')}
        if any(t not in tiers or len(tiers[t])!=1 or pd.isna(tiers[t].iloc[0].correct) for t in ('T0_PARAMETRIC','T1_RETRIEVAL','T2_REFERENCE')):
            rows.append({'unit':key,'hypothesis':'unresolved'})
            continue
        t0,t1,t2 = (bool(tiers[t].iloc[0].correct) for t in ('T0_PARAMETRIC','T1_RETRIEVAL','T2_REFERENCE'))
        category = 'plan_allocation_reasoning_proxy' if not t2 else ('retrieval_quality_proxy' if not t1 else ('knowledge_gap_proxy' if not t0 else 'all_correct'))
        rows.append({'unit':key,'hypothesis':category,'not_a_diagnosis':True})
    return pd.DataFrame(rows)


def matched_units(left: pd.DataFrame, right: pd.DataFrame, value: str) -> pd.DataFrame:
    keys = ['workspace_id','case_id','seed']
    def group(frame: pd.DataFrame, suffix: str) -> pd.DataFrame:
        valid = frame.dropna(subset=[value])
        return valid.groupby(keys,sort=True)[value].mean().rename(suffix).reset_index()
    return group(left,'left').merge(group(right,'right'),on=keys,validate='one_to_one')


def mcnemar(left: list[bool], right: list[bool]) -> dict[str,Any]:
    if len(left)!=len(right) or not left or any(type(v) is not bool for v in left+right):
        raise ValueError('Nonempty paired binary outcomes required')
    b = sum(a and not z for a,z in zip(left,right))
    c = sum(not a and z for a,z in zip(left,right))
    return {'pairs':len(left),'left_only':b,'right_only':c,'p_value':float(binomtest(b,b+c,0.5).pvalue) if b+c else 1.0,
            'method':'exact_mcnemar_binomial','effect':sum(int(z)-int(a) for a,z in zip(left,right))/len(left)}


def paired_bootstrap(left: list[float],right: list[float], *, seed: int=42, draws: int=2000,confidence: float=0.95) -> dict[str,Any]:
    if len(left)!=len(right) or not left or draws<100 or not 0<confidence<1:
        raise ValueError('Valid paired values, confidence and >=100 draws required')
    delta = np.asarray(right,dtype=float)-np.asarray(left,dtype=float)
    if not np.isfinite(delta).all():
        raise ValueError('Finite paired values required')
    if len(delta)<2:
        return {'pairs':1,'effect':float(delta[0]),'ci_low':None,'ci_high':None,'confidence':confidence,
                'seed':seed,'draws':0,'method':'paired_unit_percentile_bootstrap','status':'insufficient_paired_units'}
    rng = np.random.default_rng(seed)
    sampled = [float(delta[rng.integers(0,len(delta),len(delta))].mean()) for _ in range(draws)]
    low,high = np.quantile(sampled,[(1-confidence)/2,(1+confidence)/2])
    return {'pairs':len(delta),'effect':float(delta.mean()),'ci_low':float(low),'ci_high':float(high),
            'confidence':confidence,'seed':seed,'draws':draws,'method':'paired_unit_percentile_bootstrap'}


def adjust_pvalues(values: list[float],method: Literal['holm','bonferroni','none']='holm') -> list[float]:
    if any(not math.isfinite(p) or not 0<=p<=1 for p in values):
        raise ValueError('P-values must be in [0,1]')
    if method=='none':
        return list(values)
    if method=='bonferroni':
        return [min(1,p*len(values)) for p in values]
    if method!='holm':
        raise ValueError('Unsupported correction')
    output = [0.0]*len(values)
    previous = 0.0
    for rank,(index,p) in enumerate(sorted(enumerate(values),key=lambda pair:pair[1])):
        previous = max(previous,min(1,p*(len(values)-rank)))
        output[index] = previous
    return output


def fit_bam(budgets: list[float],errors: list[float]) -> dict[str,Any]:
    if len(budgets)!=len(errors) or len(set(budgets))<3 or any(b<=0 or not math.isfinite(b) for b in budgets) or any(e<=0 or not math.isfinite(e) for e in errors):
        raise ValueError('BAM needs >=3 distinct positive budgets and positive finite errors; zero errors are not clipped')
    x,y = np.asarray(budgets),np.asarray(errors)
    def model(b: np.ndarray,c: float,beta: float) -> np.ndarray:
        return c/np.power(b,beta)
    params,covariance = curve_fit(model,x,y,p0=[float(max(y)*min(x)),1],bounds=([0,0],[np.inf,10]),maxfev=10000)
    predicted = model(x,*params)
    residuals = y-predicted
    total = float(np.sum((y-y.mean())**2))
    return {'c':float(params[0]),'beta':float(params[1]),'r_squared':1-float(np.sum(residuals**2))/total if total else None,
            'residuals':residuals.tolist(),'predicted':predicted.tolist(),'budgets':list(budgets),'observed_errors':list(errors),
            'covariance':covariance.tolist() if np.isfinite(covariance).all() else None,'status':'model_fit_not_oracle'}


def optimal_allocation(parameters: list[dict[str,float]],total_budget: float) -> list[float]:
    if not parameters or not math.isfinite(total_budget) or total_budget<=0 or any(not math.isfinite(p['c']) or not math.isfinite(p['beta']) or p['c']<=0 or p['beta']<=0 for p in parameters):
        raise ValueError('Positive budget, c and beta required')
    def allocations(log_lambda: float) -> list[float]:
        return [math.exp((math.log(p['c']*p['beta'])-log_lambda)/(p['beta']+1)) for p in parameters]
    root = brentq(lambda value:sum(allocations(value))-total_budget,-700,700)
    return allocations(root)


def allocation_regret(parameters: list[dict[str,float]],actual: list[float], *, measured_actual_e3: float | None=None,
                      measured_optimal_e3: float | None=None, predicted_actual_accuracy: float | None=None,
                      predicted_optimal_accuracy: float | None=None, predicted_actual_cost: float | None=None,
                      predicted_optimal_cost: float | None=None, prediction_model: str | None=None) -> dict[str,Any]:
    if len(parameters)!=len(actual) or any(b<=0 for b in actual):
        raise ValueError('Matching positive allocations required')
    optimum = optimal_allocation(parameters,sum(actual))
    error = lambda budgets:sum(p['c']/b**p['beta'] for p,b in zip(parameters,budgets))
    predicted_e3 = None
    if any(v is not None for v in (predicted_actual_accuracy,predicted_optimal_accuracy,predicted_actual_cost,predicted_optimal_cost)):
        if not prediction_model or any(v is None for v in (predicted_actual_accuracy,predicted_optimal_accuracy,predicted_actual_cost,predicted_optimal_cost)) or not 0<=predicted_actual_accuracy<=1 or not 0<=predicted_optimal_accuracy<=1 or predicted_actual_cost<=0 or predicted_optimal_cost<=0:
            raise ValueError('Predicted E3 requires an explicit accuracy/cost model and complete valid predictions')
        predicted_e3 = predicted_optimal_accuracy**2/predicted_optimal_cost-predicted_actual_accuracy**2/predicted_actual_cost
    return {'optimal_budget':optimum,'actual_budget':actual,'predicted_error_regret':error(actual)-error(optimum),
            'measured_e3_regret':measured_optimal_e3-measured_actual_e3 if measured_optimal_e3 is not None and measured_actual_e3 is not None else None,
            'status':'measured_e3_and_model_prediction' if measured_optimal_e3 is not None and measured_actual_e3 is not None else 'model_prediction_only',
            'predicted_error_is_not_e3':True,'predicted_e3_regret':predicted_e3,'prediction_model':prediction_model}


def budget_behavior(frame: pd.DataFrame,cost: str='target_decoding_tokens_logical') -> pd.DataFrame:
    rows = []
    keys = ['base_run_id','target','seed','sample']
    if frame.empty or 'intervention_budget' not in frame:
        return pd.DataFrame(rows)
    sweeps = frame[frame.operator=='set_budget'].dropna(subset=['intervention_budget','correct',cost])
    for key,group in sweeps.groupby(keys,sort=True):
        levels = group.sort_values('intervention_budget')
        if levels.intervention_budget.duplicated().any():
            rows.append({'unit':key,'status':'ambiguous_levels'})
            continue
        for (_,low),(_,high) in zip(levels.iloc[:-1].iterrows(),levels.iloc[1:].iterrows()):
            rows.append({'unit':key,'lower_budget':low.intervention_budget,'higher_budget':high.intervention_budget,
                'underthinking_proxy':not bool(low.correct) and bool(high.correct),
                'overthinking_tokens_proxy':max(0,float(high[cost])-float(low[cost])) if bool(low.correct) and bool(high.correct) else None,
                'scope':'target_output_token/final_outcome_proxy; no subtask gold'})
    return pd.DataFrame(rows)


def minimum_tier(frame: pd.DataFrame,cost: str='total_tokens_logical') -> pd.DataFrame:
    rows = []
    if frame.empty:
        return pd.DataFrame(rows)
    base = frame[(frame.tier_policy=='forced') & frame.operator.isna()]
    keys = ['workspace_id','case_id','seed','sample','signature']
    for key,group in base.groupby(keys,sort=True):
        if group.tier.duplicated().any() or group.correct.isna().any() or group[cost].isna().any():
            rows.append({'unit':key,'status':'unknown_or_ambiguous'})
            continue
        candidates = group[group.correct==True].sort_values([cost,'tier'])
        if candidates.empty:
            rows.append({'unit':key,'status':'no_observed_sufficient_tier'})
        else:
            chosen = candidates.iloc[0]
            rows.append({'unit':key,'tier':chosen.tier,'cost':float(chosen[cost]),'run_id':chosen.run_id,
                         'status':'cheapest_observed_success','observed_tiers':sorted(group.tier.dropna().tolist())})
    return pd.DataFrame(rows)


def escalation_regret(actual_tier: str,actual_cost: float, sufficient_tier: str,sufficient_cost: float,
                      correct: bool) -> dict[str,Any]:
    order = ['T0_PARAMETRIC','T1_RETRIEVAL','T2_REFERENCE']
    if actual_tier not in order or sufficient_tier not in order or actual_cost<0 or sufficient_cost<0:
        raise ValueError('Valid tiers and nonnegative measured costs required')
    difference = order.index(actual_tier)-order.index(sufficient_tier)
    return {'cost_regret':actual_cost-sufficient_cost,'direction':'under' if difference<0 else ('over' if difference>0 else 'matched'),
            'under_escalation_failure':difference<0 and not correct,'status':'observed_comparator_only'}


def routing_regret(actual_route: str,scores: dict[str,float]) -> dict[str,Any]:
    if actual_route not in scores or not scores or any(not math.isfinite(v) for v in scores.values()):
        raise ValueError('Actual route and finite observed candidate scores required')
    best = max(scores,key=scores.get)
    return {'actual_route':actual_route,'best_observed_route':best,'regret':scores[best]-scores[actual_route],
            'status':'candidate_sweep_observation'}


def fairness(left: dict[str,Any],right: dict[str,Any]) -> dict[str,Any]:
    fields = ['workspace_id','case_id','seed','model_map','tool_contract','task_fingerprint','budget_policy','soft_budget','hard_output_limit','max_calls']
    missing = [key for key in fields if key not in left or key not in right or left[key] is None or right[key] is None]
    if 'tool_contract' not in missing and (any(v is None for v in left['tool_contract'].values()) or any(v is None for v in right['tool_contract'].values())):
        missing.append('tool_contract')
    differences = [key for key in fields if key not in missing and left[key]!=right[key]]
    return {'matched':not missing and not differences,'missing':missing,'differences':differences,
            'scope':'declared admission match, not measured compute equality'}


def best_of_n(candidates: pd.DataFrame, *, selection: Literal['precommitted_score','oracle_upper_bound'],
              score_column: str | None=None) -> pd.DataFrame:
    if selection=='precommitted_score' and (not score_column or score_column=='correct'):
        raise ValueError('A precommitted non-gold selection score required')
    if selection not in ('precommitted_score','oracle_upper_bound'):
        raise ValueError('Explicit selection policy required')
    rows = []
    for key,group in candidates.groupby(['workspace_id','case_id','seed'],sort=True):
        score = 'correct' if selection=='oracle_upper_bound' else score_column
        if group[score].isna().any():
            continue
        chosen = group.sort_values([score,'run_id'],ascending=[False,True]).iloc[0].to_dict()
        # Charge every candidate, including discarded generations. Wall time
        # requires a scheduling receipt and cannot be inferred by summation.
        for column in group.columns:
            if column.startswith(('total_tokens_', 'decoding_tokens_', 'cost_usd_')):
                chosen['selected_'+column] = chosen[column]
                chosen[column] = _sum_known(group[column].tolist())
            elif column.startswith('latency_'):
                chosen[column] = None
        chosen.update({'best_of_n':len(group),'selection':selection,
                       'compute_total_tokens':float(group.total_tokens_logical.sum()) if group.total_tokens_logical.notna().all() else None,
                       'oracle_is_not_deployable':selection=='oracle_upper_bound'})
        rows.append(chosen)
    return pd.DataFrame(rows)


def paired_comparisons(frame: pd.DataFrame,value: str='correct', *, bootstrap_draws: int=2000,
                       correction: str='holm') -> pd.DataFrame:
    rows = []
    if frame.empty:
        return pd.DataFrame(rows)
    conditions = sorted(frame.condition_id.unique())
    for i,left_id in enumerate(conditions):
        for right_id in conditions[i+1:]:
            left,right = frame[frame.condition_id==left_id],frame[frame.condition_id==right_id]
            # Interventions are compared through explicit base links, not pooled
            # under an unchanged condition_id with independent base samples.
            if 'operator' in frame:
                left,right = left[left.operator.isna()],right[right.operator.isna()]
            keys = ['workspace_id','case_id','seed']
            def coverage(data: pd.DataFrame) -> dict[tuple[Any,...],tuple[int,...]]:
                return {key:tuple(sorted(part['sample'].tolist())) for key,part in data.groupby(keys)
                        if part[value].notna().all() and not part['sample'].duplicated().any()}
            lc,rc = coverage(left),coverage(right)
            shared = {key for key in lc.keys() & rc.keys() if lc[key]==rc[key]}
            left = left[left[keys].apply(tuple,axis=1).isin(shared)]
            right = right[right[keys].apply(tuple,axis=1).isin(shared)]
            pairs = matched_units(left,right,value)
            if pairs.empty:
                rows.append({'left':left_id,'right':right_id,'status':'no_known_paired_units'})
                continue
            stats = paired_bootstrap(pairs.left.tolist(),pairs.right.tolist(),draws=bootstrap_draws)
            single_left = left.groupby(['workspace_id','case_id','seed']).size()
            single_right = right.groupby(['workspace_id','case_id','seed']).size()
            binary = value=='correct' and all(single_left.get(tuple(row),0)==1 and single_right.get(tuple(row),0)==1
                for row in pairs[['workspace_id','case_id','seed']].itertuples(index=False,name=None))
            binary_stats = mcnemar([bool(v) for v in pairs.left],[bool(v) for v in pairs.right]) if binary else {}
            rows.append({'left':left_id,'right':right_id,'status':'paired_unit_comparison','value':value,
                         **stats,'p_value':binary_stats.get('p_value'),
                         'mcnemar_status':'single_sample_per_unit' if binary else 'not_binary_single_sample; bootstrap_of_unit_means',
                         'left_only':binary_stats.get('left_only'),'right_only':binary_stats.get('right_only')})
    eligible = [i for i,row in enumerate(rows) if row.get('p_value') is not None]
    adjusted = adjust_pvalues([rows[i]['p_value'] for i in eligible],correction)
    for index,p in zip(eligible,adjusted):
        rows[index]['p_adjusted'] = p
        rows[index]['correction'] = correction
    return pd.DataFrame(rows)


def bam_sweeps(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if frame.empty:
        return pd.DataFrame(rows)
    sweeps = frame[frame.operator=='set_budget'].dropna(subset=['intervention_budget','correct'])
    for key,group in sweeps.groupby(['base_run_id','target','seed'],sort=True):
        levels = group.groupby('intervention_budget').correct.mean().sort_index()
        budgets,errors = levels.index.astype(float).tolist(),(1-levels).astype(float).tolist()
        try:
            fitted = fit_bam(budgets,errors)
            rows.append({'unit':key,'outcome':'final_error_rate_proxy',**fitted})
        except ValueError as exc:
            rows.append({'unit':key,'status':'not_fitted','reason':str(exc),'budgets':budgets,'observed_errors':errors})
    return pd.DataFrame(rows)


def observed_regrets(frame: pd.DataFrame,cost: str='total_tokens_logical') -> tuple[pd.DataFrame,pd.DataFrame]:
    escalation,route_rows = [],[]
    if frame.empty:
        return pd.DataFrame(escalation),pd.DataFrame(route_rows)
    # A comparison signature deliberately ignores tier policy, while preserving
    # strategy/roles/admission/source identity for forced vs decided comparisons.
    for _,actual in frame[frame.tier_policy=='agent_decided'].iterrows():
        group = frame[(frame.comparison_signature==actual.comparison_signature)&(frame.case_id==actual.case_id)&
                      (frame.workspace_id==actual.workspace_id)&(frame.seed==actual.seed)&(frame['sample']==actual['sample'])]
        sufficient = minimum_tier(group,cost)
        candidates = sufficient[sufficient.status=='cheapest_observed_success'] if not sufficient.empty else sufficient
        if len(candidates)!=1 or pd.isna(actual.tier) or pd.isna(actual[cost]) or pd.isna(actual.correct):
            escalation.append({'run_id':actual.run_id,'status':'unresolved_comparator'})
            continue
        best = candidates.iloc[0]
        escalation.append({'run_id':actual.run_id,**escalation_regret(actual.tier,float(actual[cost]),best.tier,float(best.cost),bool(actual.correct)),
                           'includes_information_choice_overhead':True})
    sweeps = frame[frame.operator=='set_route']
    for key,group in sweeps.groupby(['base_run_id','target','seed'],sort=True):
        scores = {}
        for route,part in group.groupby('actual_target_model'):
            if part.correct.isna().any() or part[cost].isna().any() or part[cost].mean()<=0:
                continue
            scores[route] = float(part.correct.mean())**2/float(part[cost].mean())
        bases = frame[frame.run_id==key[0]]
        actual_route = bases.iloc[0].subtask_models.get(key[1]) if len(bases)==1 else None
        if actual_route not in scores or len(scores)<2:
            route_rows.append({'unit':key,'status':'insufficient_controlled_candidate_routes'})
            continue
        route_rows.append({'unit':key,**routing_regret(actual_route,scores),'metric':'E3_final_outcome_proxy','scores':scores})
    return pd.DataFrame(escalation),pd.DataFrame(route_rows)


def allocation_sweeps(frame: pd.DataFrame) -> pd.DataFrame:
    fits = bam_sweeps(frame)
    rows = []
    if fits.empty:
        return pd.DataFrame(rows)
    fitted = [row for row in fits.to_dict('records') if row.get('status')=='model_fit_not_oracle']
    groups: dict[tuple[str,int],dict[str,dict[str,float]]] = {}
    for row in fitted:
        base,target,seed = row['unit']
        groups.setdefault((base,seed),{})[target] = {'c':row['c'],'beta':row['beta']}
    for (base,seed),parameters in groups.items():
        actual = frame[frame.run_id==base]
        allocations = actual.iloc[0].allocated_budgets if len(actual)==1 else None
        if not allocations or set(parameters)!=set(allocations) or any(v<=0 for v in allocations.values()):
            rows.append({'base_run_id':base,'seed':seed,'status':'incomplete_subtask_fits_or_allocation'})
            continue
        ordered = sorted(allocations)
        try:
            result = allocation_regret([parameters[key] for key in ordered],[allocations[key] for key in ordered])
            rows.append({'base_run_id':base,'seed':seed,'subtasks':ordered,**result,
                         'assumption':'additive context-local final-error proxy; not a validated accuracy model'})
        except ValueError as exc:
            rows.append({'base_run_id':base,'status':'not_estimated','reason':str(exc)})
    return pd.DataFrame(rows)


def stratified_quality(frame: pd.DataFrame) -> pd.DataFrame:
    """Task/seed means per observed stratum; empty cells are not invented."""
    if frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    data['reasoning_label'] = data.reasoning_required.map(lambda v: 'unknown' if pd.isna(v) else 'true' if bool(v) else 'false')
    keys = ['domain','reasoning_label','reasoning_type','evaluator_version','harness_backend','comparison_id']
    rows = []
    for key, group in data.groupby(keys, dropna=False):
        known = group.dropna(subset=['correct'])
        units = known.groupby(['workspace_id','case_id','seed']).correct.mean()
        row = dict(zip(keys,key))
        row.update(runs=len(group),known_runs=len(known),unknown_runs=len(group)-len(known),
                   task_seed_units=len(units),accuracy=float(units.mean()) if len(units) else None,
                   quality_source='model_judgment; not human ground truth')
        for dimension in ('answer_equivalence','evidence_support','citation_validity','reasoning_validity','units_and_periods','abstention','tool_relevance','dependency_handling','handoff_quality','context_use','contradiction_handling','synthesis_faithfulness'):
            if dimension not in group:
                continue
            scored = group[group[dimension].isin(['pass','fail'])].copy()
            scored['pass_value'] = (scored[dimension]=='pass').astype(float)
            rates = scored.groupby(['workspace_id','case_id','seed']).pass_value.mean()
            row[dimension+'_pass_rate'] = float(rates.mean()) if len(rates) else None
        rows.append(row)
    return pd.DataFrame(rows)
