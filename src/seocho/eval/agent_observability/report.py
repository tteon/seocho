"""Static research report artifacts; no live graph UI or automatic paid reruns."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .analytics import (bam_sweeps, budget_behavior, efficiency, error_decomposition,
    fairness, stratified_quality, minimum_tier, allocation_sweeps, observed_regrets, paired_comparisons, records_frame)


class ReportSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    hypothesis: str | None = None
    workspace_id: str
    directory: Path
    output: Path
    cost: Literal['decoding_tokens','total_tokens','cost_usd','latency'] = 'total_tokens'
    basis: Literal['logical','physical'] = 'logical'
    bootstrap_draws: int = Field(default=2000,ge=100)
    correction: Literal['holm','bonferroni','none'] = 'holm'


def build_report(spec: ReportSpec) -> dict[str,Any]:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    spec.output.mkdir(parents=True,exist_ok=True)
    frame,gaps,metadata = records_frame(spec.directory,spec.workspace_id)
    cost = spec.cost+'_'+spec.basis
    tables = {'stratified_quality':stratified_quality(frame),'runs':frame,'gaps':gaps,'efficiency':efficiency(frame,spec.cost,spec.basis),
        'error_decomposition':error_decomposition(frame),'budget_behavior':budget_behavior(frame),
        'allocation_regret':allocation_sweeps(frame),'bam':bam_sweeps(frame),'minimum_tier':minimum_tier(frame,cost),
        'paired_accuracy':paired_comparisons(frame,bootstrap_draws=spec.bootstrap_draws,correction=spec.correction)}
    if not frame.empty:
        tables['paired_cost'] = paired_comparisons(frame,value=cost,bootstrap_draws=spec.bootstrap_draws,correction=spec.correction)
    tables['escalation_regret'],tables['routing_regret'] = observed_regrets(frame,cost)
    fairness_rows = []
    if not frame.empty:
        for _,unit in frame.groupby(['workspace_id','case_id','seed','sample']):
            # Only compare regular single/parallel runs with one declared design;
            # include missing provenance as failed matching, never assume it.
            singles = unit[unit.delegation=='single']
            multis = unit[unit.delegation=='parallel']
            for _,left in singles.iterrows():
                for _,right in multis.iterrows():
                    fairness_rows.append({'left_run':left.run_id,'right_run':right.run_id,
                                          **fairness(left.to_dict(),right.to_dict())})
    tables['fairness'] = pd.DataFrame(fairness_rows)
    paths = {}
    for name,table in tables.items():
        path = spec.output/(name+'.csv')
        table.to_csv(path,index=False)
        paths[name] = str(path)
    measured = frame.groupby('comparison_id')[cost].mean().dropna().sort_values() if not frame.empty else pd.Series(dtype=float)
    fig,axis = plt.subplots(figsize=(12,max(4,len(measured)*0.4)))
    if len(measured):
        measured.plot.barh(ax=axis,color='#315f94')
        axis.set_xlabel(f'{spec.cost} ({spec.basis}; mean per run)')
    else:
        axis.text(0.5,0.5,'No known measured costs',ha='center',transform=axis.transAxes)
    axis.set_title(spec.name+' — observed work')
    fig.tight_layout()
    cost_plot = spec.output/'cost.png'
    fig.savefig(cost_plot,dpi=150)
    plt.close(fig)
    fig,axis = plt.subplots(figsize=(10,5))
    eligible = tables['efficiency'].dropna(subset=['E3']) if not tables['efficiency'].empty else pd.DataFrame()
    if not eligible.empty:
        axis.scatter(eligible['T'],eligible['A'])
        for _,row in eligible.iterrows():
            axis.annotate(row.condition_id,(row['T'],row['A']),fontsize=7)
        axis.set_xlabel(f'{spec.cost} ({spec.basis})')
        axis.set_ylabel('Accuracy A (task/seed mean)')
    else:
        axis.text(0.5,0.5,'No eligible accuracy/E3 evidence\nUnknown verdicts or costs remain unknown',ha='center',transform=axis.transAxes)
    axis.set_title('Observed accuracy versus work')
    fig.tight_layout()
    accuracy_plot = spec.output/'accuracy.png'
    fig.savefig(accuracy_plot,dpi=150)
    plt.close(fig)
    result = {'name':spec.name,'hypothesis':spec.hypothesis,'source':metadata,'configuration':spec.model_dump(mode='json'),
        'implementation_hash':hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name('analytics.py').read_bytes()+Path(__file__).with_name('signal_store.py').read_bytes()).hexdigest(),
        'row_counts':{name:len(table) for name,table in tables.items()},'tables':paths,
        'figures':[str(cost_plot),str(accuracy_plot)],
        'limits':['Population covers audited completed runs only; excluded/failed sources stay visible in gaps', 'Unknown outcomes/prices are excluded, never zero-filled','Replay logical and physical work are separate',
                  'Proxy error decomposition is not a diagnosis','BAM predictions are not measured E3 or an oracle',
                  'No automatic model/price/graph-service rerun','McNemar requires one binary sample per task/seed; repeated samples use bootstrap of unit means']}
    (spec.output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    lines = [f'# {spec.name}','', 'Offline report from audited Recorder originals.',
             f'Cost: {spec.cost}, basis: {spec.basis}; evaluator costs reported separately.', '',
             '## Evidence', '',f'Runs: {len(frame)}. Source gaps: {len(gaps)}.',
             'Unknown outcomes and prices are not imputed.', '', '## Tables', '']
    lines += [f'- [{name}]({Path(path).name}): {len(tables[name])} rows' for name,path in paths.items()]
    lines += ['', '## Figures', '', '![Observed work](cost.png)', '', '![Accuracy evidence](accuracy.png)',
              '', '## Interpretation limits', '', *['- '+line for line in result['limits']]]
    (spec.output/'report.md').write_text('\n'.join(lines)+'\n')
    return result


def report_from_yaml(path: Path) -> dict[str,Any]:
    import yaml
    spec = ReportSpec.model_validate(yaml.safe_load(path.read_text()))
    for name in ('directory','output'):
        value = getattr(spec,name)
        if not value.is_absolute():
            setattr(spec,name,(path.parent/value).resolve())
    return build_report(spec)
