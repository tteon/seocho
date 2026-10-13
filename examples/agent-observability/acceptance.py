"""Loopback HTTP + real DuckDB acceptance; deterministic backend, no paid APIs."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import yaml

from seocho.recorder.store import RecorderStore


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('outputs/agent-observability') / ('acceptance-' + uuid4().hex))
    args = parser.parse_args()
    directory = args.directory
    if (directory / 'store' / 'recorder.duckdb').exists():
        raise ValueError('Use a fresh acceptance directory; original records are never overwritten')
    directory.mkdir(parents=True, exist_ok=True)
    upstream_port, recorder_port = free_port(), free_port()
    while recorder_port == upstream_port:
        recorder_port = free_port()
    config = directory / 'config.yaml'
    config.write_text(yaml.safe_dump({'upstream_url': f'http://127.0.0.1:{upstream_port}/v1',
        'directory': str(directory / 'store'), 'cache_namespace': 'mock-http-v1', 'cache_mode': 'read_write'}))
    processes: list[Any] = []
    logs: list[Any] = []
    attempts = []
    started = time.monotonic()
    try:
        commands = [[sys.executable, '-m', 'uvicorn', 'seocho.recorder.mock_backend:create_mock_app', '--factory',
                     '--host', '127.0.0.1', '--port', str(upstream_port)],
                    [sys.executable, '-m', 'seocho.recorder', 'serve', '--config', str(config), '--port', str(recorder_port)]]
        for i, command in enumerate(commands):
            log = (directory / f'server-{i}.log').open('w')
            logs.append(log)
            processes.append(subprocess.Popen(command, stdout=log, stderr=log))
        with httpx.Client(timeout=10) as client:
            for _ in range(100):
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError('Local server exited; inspect server logs')
                try:
                    if client.get(f'http://127.0.0.1:{recorder_port}/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError('Local server startup timeout')
            hits = 0
            request = {'model': 'mock-fixed', 'messages': [{'role': 'system', 'content': 'shared system'},
                       {'role': 'user', 'content': 'fixed probe'}], 'seed': 42, 'temperature': 0}
            for i in range(100):
                call_id = uuid4().hex
                headers = {'X-Call-Id': call_id, 'X-Workspace-Id': 'mock-workspace', 'X-Run-Id': 'mock-http-100',
                           'X-Agent-Id': f'agent-{i % 4}', 'X-Step-Id': f'step-{i}',
                           'X-Tier': 'T0_PARAMETRIC', 'X-Template-Version': 'mock-v1'}
                response = client.post(f'http://127.0.0.1:{recorder_port}/v1/chat/completions', headers=headers, json=request)
                response.raise_for_status()
                if response.headers.get('X-Call-Id') != call_id:
                    raise RuntimeError('Call identity mismatch')
                hits += response.headers.get('X-Cache-Hit') == 'true'
                attempts.append({'call_id': call_id, 'run_id': 'mock-http-100'})
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
        calls = store.calls(run_id='mock-http-100')
        audit = store.audit('mock-http-100', [a['call_id'] for a in attempts])
        request_blobs = len({c['start']['request_blob'] for c in calls})
        response_blobs = len({c['finish']['response_blob'] for c in calls})
        if len(calls) != 100 or hits != 99 or request_blobs != 1 or response_blobs != 1 or not audit['coverage_proven']:
            raise RuntimeError('100-call acceptance failed')
        store.export(directory / 'raw-events.parquet')
        result = {'status': 'passed', 'upstream': 'deterministic loopback mock', 'calls': len(calls),
                  'cache_hits': hits, 'upstream_calls': sum(c['finish']['provider_work'] for c in calls),
                  'unique_request_blobs': request_blobs, 'unique_response_blobs': response_blobs,
                  'attempts': attempts, 'audit': audit, 'elapsed_seconds': time.monotonic() - started,
                  'scope': 'Local protocol/storage acceptance, not paid-provider compatibility or throughput evidence'}
        (directory / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
        print(f'PASS: 100 calls, 99 cache hits, deduplicated bodies; {directory}')
    finally:
        store.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
