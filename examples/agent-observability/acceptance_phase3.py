"""Actual loopback Phase 3 acceptance, deterministic local services only."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

from seocho.recorder.store import RecorderStore


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory.exists():
        raise ValueError('Use a fresh directory')
    directory.mkdir(parents=True)
    mock_port, proxy_port = free_port(), free_port()
    profile = {'upstream_url': f'http://127.0.0.1:{mock_port}/v1', 'directory': str(directory / 'store'),
               'cache_namespace': 'phase3-local', 'cache_mode': 'off'}
    (directory / 'recorder.yaml').write_text(yaml.safe_dump(profile))
    example = Path(__file__).resolve().parent
    config = yaml.safe_load((example / 'experiment.mock.yaml').read_text())
    config.update(recorder_url=f'http://127.0.0.1:{proxy_port}/v1',
                  dataset=str(example / 'mock-cases.json'), evaluation=str(example / 'mock-evaluation.json'))
    config['conditions'] = [c for c in config['conditions'] if c['condition_id'] == 'plan_and_budget-T0_PARAMETRIC']
    (directory / 'experiment.yaml').write_text(yaml.safe_dump(config))
    (directory / 'intervention.yaml').write_text(yaml.safe_dump({'operator':'knock_in_reference','subtask':'task_1'}))
    (directory / 'limits.yaml').write_text(yaml.safe_dump({'max_tokens':1_000_000}))
    processes = []
    logs = []
    try:
        for name, command in [('mock', ['mock', '--port', str(mock_port)]),
                              ('recorder', ['serve', '--config', str(directory / 'recorder.yaml'), '--port', str(proxy_port)])]:
            log = (directory / (name + '.log')).open('w')
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, '-m', 'seocho.recorder', *command], stdout=log, stderr=log))
        with httpx.Client() as client:
            for _ in range(100):
                try:
                    if client.get(f'http://127.0.0.1:{proxy_port}/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError('Proxy startup failed')
        subprocess.run([sys.executable, '-m', 'seocho.eval.agent_observability', 'run', '--config',
                        str(directory / 'experiment.yaml'), '--output', str(directory / 'trials.json')], check=True)
        base_trial = json.loads((directory / 'trials.json').read_text())['trials'][0]
        for repetition in range(2):
            subprocess.run([sys.executable, '-m', 'seocho.eval.agent_observability', 'intervene',
                '--config', str(directory / 'experiment.yaml'), '--base-run', base_trial['run_id'],
                '--spec', str(directory / 'intervention.yaml'), '--limits', str(directory / 'limits.yaml'),
                '--samples', '2', '--output', str(directory / f'derived-{repetition}.json')], check=True)
        with httpx.Client() as client:
            provider_requests = client.get(f'http://127.0.0.1:{mock_port}/stats').json()['requests']

    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()
    store = RecorderStore(directory / 'store', read_only=True)
    try:
        trials = json.loads((directory / 'trials.json').read_text())['trials']
        base_run_id = trials[0]['run_id']
        derived = [trial for repetition in range(2) for trial in json.loads((directory / f'derived-{repetition}.json').read_text())['trials']]
        if len(derived) != 4:
            raise RuntimeError('Expected two repetitions of N=2')
        trials += derived
        for trial in derived:
            calls = store.calls(run_id=trial['run_id'])
            hits = [c for c in calls if c['finish']['cache_hit']]
            fresh = [c for c in calls if not c['finish']['cache_hit']]
            if len(hits) != 2 or len(fresh) != 3 or any(c['finish']['origin_call_id'] not in {x['call_id'] for x in store.calls(run_id=base_run_id)} for c in hits):
                raise RuntimeError('Prefix/downstream partition failed')
        if provider_requests != 17:
            raise RuntimeError('Expected five base plus twelve downstream provider dispatches')
        for trial in trials:
            run = trial['run_id']
            if not store.audit(run, [a['call_id'] for a in trial['attempts']])['coverage_proven']:
                raise RuntimeError('Transport coverage failed')
            if not store.audit_agent_events(run)['valid']:
                raise RuntimeError('Decision/realization pairing failed')
        summary = {'status': 'passed', 'runs': len(trials), 'prefix_cache_hits': 8, 'provider_requests': provider_requests, 'calls': len(store.calls()),
                   'events': sum(len(store.agent_events(t['run_id'])) for t in trials),
                   'provider': 'deterministic local mock', 'cache_mode': 'off',
                   'scope': 'Protocol/orchestration acceptance, not answer quality or MARA compatibility'}
        (directory / 'acceptance.json').write_text(json.dumps(summary, indent=2))
        store.export(directory / 'raw-events.parquet')
        print(json.dumps(summary))
    finally:
        store.close()


if __name__ == '__main__':
    main()
