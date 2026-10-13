"""Read-only projections of Recorder truth and explicitly marked derived proxies."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from seocho.eval.agent_observability.analytics import (allocation_sweeps, error_decomposition,
    observed_regrets, records_frame)
from seocho.eval.agent_observability.information import normalized
from seocho.eval.agent_observability.signal_store import load_runs
from seocho.eval.agent_observability.signals import SignalConfig, derive

from .store import RecorderStore


def clean(value: Any) -> Any:
    """Normalize DataFrame NaN values for strict JSON responses."""
    return json.loads(json.dumps(value, default=str), parse_constant=lambda _: None)


class Views:
    def __init__(self, directory: Path, workspace_id: str) -> None:
        self.directory = directory
        self.workspace_id = workspace_id
        with_store = RecorderStore(directory, read_only=True)
        try:
            self.runs, self.gaps, self.source_hash = load_runs(with_store, workspace_id)
            raw = with_store.db.execute('SELECT data FROM agent_events ORDER BY event_id').fetchall()
            ids = sorted({e['run_id'] for (data,) in raw if (e:=json.loads(data))['workspace_id']==workspace_id})
            self.index = []
            for run_id in ids:
                events = self.events(with_store, run_id)
                root = next((e['payload'] for e in events if e['action']=='run' and e['kind']=='decision'), {})
                end = next((e['payload'] for e in events if e['action']=='run' and e['kind']=='realization'), {})
                run = next((r for r in self.runs if r.run_id==run_id), None)
                self.index.append({'run_id':run_id,'condition_id':root.get('condition',{}).get('condition_id'),
                    'case_id':root.get('case_id'),'seed':root.get('seed'),'sample':root.get('sample'),
                    'status':end.get('status','incomplete'),'correct':run.correct if run else None,
                    'base_run_id':run.base_run_id if run else None,
                    'mock':bool(root.get('model_map')) and all(m=='mock-fixed' for m in root['model_map'].values())})
        finally:
            with_store.close()
        self.signals = derive(self.runs, SignalConfig())
        aligned = {(a['run_id'],a['subtask_id']):a for a in self.signals['alignment']}
        for run in self.runs:
            for sub in run.subtasks:
                row = aligned.get((run.run_id,sub.task_id))
                if row:
                    sub.key = row['alignment_key']
                    sub.alignment_method = row['method']
                    sub.alignment_confidence = row['confidence']
        self.frame, _, self.metadata = records_frame(directory, workspace_id)
        self.allocations = clean(allocation_sweeps(self.frame).to_dict('records'))
        escalation, routing = observed_regrets(self.frame)
        self.aggregate = {'source_hash':self.source_hash,'scope':self.metadata['population_scope'],
            'tier_transitions':self.signals['tier_transitions'],
            'error_decomposition':clean(error_decomposition(self.frame).to_dict('records')),
            'escalation_regret':clean(escalation.to_dict('records')),'allocation_regret':self.allocations,
            'routing_regret':clean(routing.to_dict('records')),'gaps':self.gaps+self.signals['gaps'],
            'failure_flow':self.failure_flow(), 'signal_method':'lexical-hash-word-bigram-256-v1; silver is not gold'}

    def events(self, store: RecorderStore, run_id: str) -> list[dict[str, Any]]:
        return [dict(e,payload=json.loads(store.blobs.get(e['payload_blob'],e['domain'])))
                for e in store.agent_events(run_id) if e['workspace_id']==self.workspace_id]

    def failure_flow(self) -> list[dict[str, Any]]:
        counts: Counter[tuple[str,str]] = Counter()
        by_id = {r.run_id:r for r in self.runs}
        for divergence in self.signals['first_divergence']:
            run = by_id[divergence['run_id']]
            if run.correct is not False or divergence['status']!='first_silver_divergence':
                continue
            position = divergence['position']
            # Aligned positions, not arbitrary task IDs across independently made plans.
            slots = [s for s in sorted(run.subtasks,key=lambda s:s.position) if s.position>=position]
            labels = [f'position {s.position} · {s.key}' for s in slots]+['final incorrect']
            for source,target in zip(labels,labels[1:]):
                counts[source,target]+=1
        return [{'source':s,'target':t,'value':n,'scope':'aligned order flow, not dependency or causation'}
                for (s,t),n in sorted(counts.items())]

    def graph(self, run_id: str, *, offset: int=0, limit: int=12) -> dict[str, Any]:
        if run_id not in {r['run_id'] for r in self.index}:
            raise KeyError(run_id)
        store = RecorderStore(self.directory, read_only=True)
        try:
            events = self.events(store,run_id)
            root = next((e['payload'] for e in events if e['action']=='run' and e['kind']=='decision'),{})
            root = {'question':'Missing recorded run root','condition':{'condition_id':'missing root'},'seed':None,'sample':None,'case_id':None,**root}
            plan_outputs = [e['payload']['output'] for e in events if e['action']=='plan' and e['kind']=='realization' and 'output' in e['payload']]
            public_plan = next((p for p in plan_outputs if 'decompose' in p), None)
            plans = [p['tasks'] for p in plan_outputs if 'tasks' in p]
            if public_plan and public_plan['steps']:
                plans = [[dict(t, task_id=t['step_id']) for t in public_plan['steps']]]
            tasks = plans[0] if plans else [{'task_id':'task_1','question':root['question'],'depends_on':[]}]
            allocations = next((e['payload'].get('allocations',{}) for e in events if e['action']=='budget' and e['kind']=='realization'),{})
            run = next((r for r in self.runs if r.run_id==run_id), None)
            divergence = next((d for d in self.signals['first_divergence'] if d['run_id']==run_id and d['status']=='first_silver_divergence'),None)
            start = divergence['subtask_id'] if divergence and run and run.correct is False else None
            highlighted = {start} if start else set()
            for _ in tasks:
                highlighted.update(t['task_id'] for t in tasks if any(dep in highlighted for dep in t.get('depends_on',[])))
            ordered = sorted(enumerate(tasks),key=lambda item:(item[1]['task_id'] not in highlighted,item[0]))
            selected = ordered[offset:offset+limit]
            calls = store.calls(run_id=run_id,domain='inference')
            all_calls = {c['call_id']:c for c in store.calls(domain='inference')}
            def output_tokens(call: dict[str,Any]) -> int | None:
                seen = set()
                while call.get('finish',{}).get('origin_call_id'):
                    if call['call_id'] in seen:
                        return None
                    seen.add(call['call_id'])
                    call = all_calls.get(call['finish']['origin_call_id'],{})
                return call.get('finish',{}).get('usage',{}).get('output_tokens')
            nodes,edges = [],[]
            for position,task in selected:
                task_id = task['task_id']
                sub = next((s for s in run.subtasks if s.task_id==task_id),None) if run else None
                solve_steps = {e['step_id'] for e in events if e['action']=='solve' and e.get('subtask_id')==task_id}
                task_calls = [c for c in calls if c['start']['tags'].get('step_id') in solve_steps]
                used = [output_tokens(c) for c in task_calls]
                silver = next((s for s in self.signals['silver_labels'] if s['case_id']==root['case_id'] and sub and s['alignment_key']==sub.key),None)
                entropy = next((s for s in self.signals['entropy'] if run and sub and s['unit']==[run.workspace_id,run.case_id,run.seed,run.signature,sub.key] and s['tier']==sub.tier),None)
                knock = next((k for k in self.signals['knock_in'] if k['base_run_id']==run_id and sub and k['alignment_key']==sub.key),None)
                optimal = next((a for a in self.allocations if a.get('base_run_id')==run_id and task_id in a.get('subtasks',[])),None)
                nodes.append({'id':task_id,'kind':'subtask','label':task['question'],'position':position,
                    'assigned_budget':allocations.get(task_id),'used_decoding_tokens':sum(used) if used and all(v is not None for v in used) else None,
                    'optimal_budget':optimal['optimal_budget'][optimal['subtasks'].index(task_id)] if optimal else None,
                    'tier':sub.tier if sub else None,'entropy_bits':entropy['entropy_bits'] if entropy else None,
                    'silver_match':normalized(sub.answer)==normalized(silver['answer']) if sub and silver else None,
                    'knock_in_score':knock['bottleneck_score'] if knock else None,
                    'highlight':task_id in highlighted,'call_ids':[c['call_id'] for c in task_calls]})
                for call in task_calls:
                    agent = 'agent:'+call['start']['tags']['agent_id']+':'+task_id
                    if agent not in {n['id'] for n in nodes}:
                        nodes.append({'id':agent,'kind':'agent','label':call['start']['tags']['agent_id']+' / '+call['start']['model_requested'],
                            'position':position,'highlight':task_id in highlighted,'call_ids':[call['call_id']]})
                        regret = next((r for r in self.aggregate['routing_regret'] if r.get('unit',[None,None])[:2]==[run_id,task_id]),None)
                        edges.extend([{'source':task_id,'target':agent,'kind':'routing','highlight':task_id in highlighted,'regret':regret.get('regret') if regret else None},
                                      {'source':agent,'target':'final','kind':'returned_context','highlight':task_id in highlighted}])
            shown = {n['id'] for n in nodes}
            for _,task in selected:
                edges.extend({'source':dep,'target':task['task_id'],'kind':'dependency','highlight':dep in highlighted and task['task_id'] in highlighted}
                             for dep in task.get('depends_on',[]) if dep in shown)
            path_final_steps = {e['step_id'] for e in events if e['kind']=='realization' and e['action']=='solve' and e['agent_id']=='single_solver' and not e.get('subtask_id')}
            nodes.append({'id':'final','kind':'final','label':'Final answer','position':0,'highlight':bool(start),
                'final_answer':next((e['payload'].get('output') for e in events if e['kind']=='realization' and e['action']=='solve' and e['agent_id']=='single_solver' and not e.get('subtask_id')),None) if root['condition'].get('execution_path','none')!='none' else next((e['payload'].get('output') for e in events if e['kind']=='realization' and e['action']==('synthesize' if plans else 'solve')),None),
                'call_ids':[c['call_id'] for c in calls if c['start']['tags']['step_id'] in path_final_steps] if root['condition'].get('execution_path','none')!='none' else [c['call_id'] for c in calls if c['start']['tags']['step_id'] in {e['step_id'] for e in events if e['action']=='synthesize'}] or ([c['call_id'] for c in calls if c['start']['tags']['step_id'] in {e['step_id'] for e in events if e['action']=='solve'}] if not plans else [])})
            return clean({'run_id':run_id,'public_execution_plan':public_plan,'source_hash':self.source_hash,'root':root,'nodes':nodes,'edges':edges,
                'total_subtasks':len(tasks),'offset':offset,'next_offset':offset+limit if offset+limit<len(tasks) else None,
                'hidden_dependencies':sum(dep not in shown for _,t in selected for dep in t.get('depends_on',[])),
                'first_divergence':divergence,'audit':store.audit(run_id),'agent_audit':store.audit_agent_events(run_id),
                'calls':[{'call_id':c['call_id'],'agent_id':c['start']['tags']['agent_id'],'subtask_id':c['start']['tags'].get('subtask_id'),
                          'domain':c['start']['domain'],'model':c['start']['model_requested'],'state':c.get('finish',{}).get('state')} for c in store.calls(run_id=run_id)],
                'notes':['Dependency edges use recorded depends_on only; current harness requires independent tasks.',
                         'Red path is an observed first silver divergence proxy, not a causal diagnosis.',
                         'Unknown badges and optimal budgets require additional measured samples.']})
        finally:
            store.close()

    def call(self, run_id: str, call_id: str, domain: str) -> dict[str, Any]:
        store = RecorderStore(self.directory,read_only=True)
        try:
            record = next((c for c in store.calls(run_id=run_id,domain=domain) if c['call_id']==call_id and c['start']['tags']['workspace_id']==self.workspace_id),None)
            if not record:
                raise KeyError(call_id)
            bodies = {stage:store.blobs.get(data[key],domain).decode(errors='replace')
                for stage,key in [('start','request_blob'),('finish','response_blob')]
                if (data:=record.get(stage)) and data.get(key)}
            return {'record':record,'bodies':bodies,'evaluation_contains_gold':domain=='evaluation'}
        finally:
            store.close()
