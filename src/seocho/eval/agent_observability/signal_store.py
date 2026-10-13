"""Read immutable Recorder sources; append separately versioned derived analyses."""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from ...recorder.store import RecorderStore, canonical
from .evaluator import normalize_verdict
from .signals import ALGORITHM_VERSION, Encoder, LexicalHashEncoder, Run, SignalConfig, Subanswer, derive


def load_runs(store: RecorderStore, workspace_id: str, run_ids: list[str] | None = None) -> tuple[list[Run], list[dict[str, Any]], str]:
    rows = store.db.execute('SELECT event_id, data FROM agent_events ORDER BY event_id').fetchall()
    selected = sorted({data['run_id'] for _, raw in rows if (data:=json.loads(raw))['workspace_id']==workspace_id
                       and (run_ids is None or data['run_id'] in run_ids)})
    missing = sorted(set(run_ids or [])-set(selected))
    gaps = [{'run_id':run,'reason':'run_missing_or_foreign_workspace'} for run in missing]
    runs, source = [], []
    all_calls = {c['call_id']:c for c in store.calls()}
    dependencies: dict[str, dict[str, Any]] = {}

    def root_origin(call_id: str) -> str:
        seen = set()
        while True:
            if call_id in seen or call_id not in all_calls:
                raise ValueError('Cache origin missing or cyclic')
            seen.add(call_id)
            dependencies[call_id] = all_calls[call_id]
            origin = all_calls[call_id].get('finish',{}).get('origin_call_id')
            if not origin:
                return call_id
            call_id = origin

    for run_id in selected:
        events = store.agent_events(run_id)
        calls = store.calls(run_id=run_id)
        source.append({'run_id':run_id,'events':sorted(events,key=lambda e:e['event_id']),
                       'calls':sorted(calls,key=lambda c:c['call_id'])})
        if not store.audit(run_id)['valid'] or not store.audit_agent_events(run_id)['valid']:
            gaps.append({'run_id':run_id,'reason':'source_audit_failed'})
            continue
        expanded = [dict(e,payload=json.loads(store.blobs.get(e['payload_blob'],e['domain'])))
                    for e in events if e['domain']=='inference' or (e['action']=='evaluate' and e['kind']=='realization')]
        roots = [e for e in expanded if e['action']=='run' and e['kind']=='decision']
        ends = [e for e in expanded if e['action']=='run' and e['kind']=='realization']
        if len(roots)!=1 or len(ends)!=1 or ends[0]['payload'].get('status')!='completed':
            gaps.append({'run_id':run_id,'reason':'not_completed_unique_run'})
            continue
        payload = roots[0]['payload']
        condition = payload['condition']
        signature = hashlib.sha256(canonical({
            'condition':{k:v for k,v in condition.items() if k not in ('condition_id','tier')},
            'models':payload['model_map'],'template':payload['template_version'],
            'harness_backend':payload.get('experiment_config',{}).get('harness_backend','openai_agents'),
            'evaluator_version':payload.get('experiment_config',{}).get('evaluator_version','v1')})).hexdigest()
        if condition.get('execution_path', 'none') != 'none':
            judgments = [e['payload'].get('output',{}).get('verdict') for e in expanded
                if e['action']=='evaluate' and e['kind']=='realization']
            correct = normalize_verdict(judgments[0]) if len(judgments)==1 else None
            runs.append(Run(run_id,workspace_id,payload['case_id'],payload['seed'],payload['sample'],signature,
                correct,[],condition,None,None))
            gaps.append({'run_id':run_id,'reason':'public_path_silver_alignment_not_enabled'})
            continue
        plans = [e for e in expanded if e['action']=='plan' and e['kind']=='realization']
        tasks = plans[0]['payload']['output']['tasks'] if plans else [{'task_id':'task_1','question':payload['question']}]
        judgments = [e['payload'].get('output',{}).get('verdict') for e in expanded
                     if e['action']=='evaluate' and e['kind']=='realization']
        verdict = judgments[0] if len(judgments)==1 else None
        correct = normalize_verdict(verdict)
        if correct is None:
            gaps.append({'run_id':run_id,'reason':'unknown_final_correctness','verdict':verdict})
        subanswers = []
        for position, task in enumerate(tasks):
            delegates = [e for e in expanded if e['action'] in ('delegate','solve') and e['kind']=='realization'
                         and e.get('subtask_id')==task['task_id'] and 'returned_context' in e['payload']]
            fetches = [e for e in expanded if e['action']=='fetch' and e['kind']=='realization' and e.get('subtask_id')==task['task_id']]
            solvers = [e for e in expanded if e['action']=='solve' and e['kind']=='realization'
                       and e.get('subtask_id')==task['task_id'] and e['payload'].get('call_ids')]
            tool_loop = condition['strategy'] == 'tool_agent'
            if len(delegates)!=1 or (not tool_loop and len(fetches)!=1) or len(solvers)!=1 or (not tool_loop and len(solvers[0]['payload']['call_ids'])!=1):
                gaps.append({'run_id':run_id,'subtask_id':task['task_id'],'reason':'missing_or_ambiguous_subanswer'})
                continue
            answer = delegates[0]['payload']['returned_context']['answer']
            origin = root_origin(solvers[0]['payload']['call_ids'][-1] if tool_loop else solvers[0]['payload']['call_ids'][0])
            evidence = list({row['evidence_id']:row for f in fetches for row in f['payload'].get('evidence',[])}.values())
            subanswers.append(Subanswer(task['task_id'],task['question'],position,answer,
                (fetches[0]['payload']['tier'] if fetches else condition['tier']),origin,evidence))
        interventions = [e for e in expanded if e['action']=='intervention' and e['kind']=='decision']
        change = interventions[0]['payload']['operator'] if interventions else None
        base = interventions[0]['payload']['base_run_id'] if interventions else None
        runs.append(Run(run_id,workspace_id,payload['case_id'],payload['seed'],payload['sample'],signature,
                        correct,subanswers,condition,change,base))
    source_hash = hashlib.sha256(canonical({'workspace_id':workspace_id,'selection':sorted(run_ids) if run_ids is not None else None,'raw':source,'cache_dependencies':sorted(dependencies.items())})).hexdigest()
    return runs,gaps,source_hash


class AnalysisStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True,exist_ok=True)
        self.db = duckdb.connect(str(path))
        self.db.execute('CREATE TABLE IF NOT EXISTS analysis_jobs (job_id VARCHAR PRIMARY KEY, metadata JSON)')
        self.db.execute('CREATE TABLE IF NOT EXISTS signal_rows (job_id VARCHAR, kind VARCHAR, ordinal INTEGER, data JSON, PRIMARY KEY(job_id,kind,ordinal))')

    def save(self, metadata: dict[str, Any], signals: dict[str, list[dict[str, Any]]]) -> str:
        job_id = hashlib.sha256(canonical(metadata)).hexdigest()
        if self.db.execute('SELECT 1 FROM analysis_jobs WHERE job_id=?',[job_id]).fetchone():
            return job_id
        self.db.execute('BEGIN')
        try:
            self.db.execute('INSERT INTO analysis_jobs VALUES (?,?)',[job_id,canonical(dict(metadata,created_at=datetime.now(timezone.utc).isoformat())).decode()])
            for kind, rows in signals.items():
                for i,row in enumerate(rows):
                    self.db.execute('INSERT INTO signal_rows VALUES (?,?,?,?)',[job_id,kind,i,canonical(row).decode()])
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        return job_id

    def rows(self, job_id: str, kind: str) -> list[dict[str, Any]]:
        return [json.loads(raw) for raw, in self.db.execute('SELECT data FROM signal_rows WHERE job_id=? AND kind=? ORDER BY ordinal',[job_id,kind]).fetchall()]

    def close(self) -> None:
        self.db.close()


def analyze(directory: Path, output: Path, workspace_id: str, *, config: SignalConfig | None = None,
            run_ids: list[str] | None = None, encoder: Encoder | None = None) -> dict[str, Any]:
    if output.resolve()==(directory/'recorder.duckdb').resolve():
        raise ValueError('Derived database must be separate from raw Recorder')
    config = config or SignalConfig()
    encoder = encoder or LexicalHashEncoder()
    source = RecorderStore(directory,read_only=True)
    try:
        runs,gaps,source_hash = load_runs(source,workspace_id,run_ids)
    finally:
        source.close()
    signals = derive(runs,config,encoder)
    signals['gaps'].extend(gaps)
    metadata = {'algorithm_version':ALGORITHM_VERSION,'workspace_id':workspace_id,
                'source_hash':source_hash,'config':config.model_dump(),'encoder':encoder.identity,
                'semantic':encoder.semantic,'python_version':platform.python_version(),
                'implementation_hash':hashlib.sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in ('signal_store.py','signals.py','encoders.py','information.py'))).hexdigest(),
                'run_ids':sorted(r.run_id for r in runs)}
    derived = AnalysisStore(output)
    try:
        job_id = derived.save(metadata,signals)
    finally:
        derived.close()
    return {'job_id':job_id, 'metadata':metadata,'row_counts':{k:len(v) for k,v in signals.items()},
            'limitations':['Silver is not subtask gold','Observed rescue is not causal proof',
                           'Unknown evaluator outcomes are excluded','Default embeddings are lexical, not semantic']}
