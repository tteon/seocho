"""Recorder contracts against actual DuckDB files, deterministic HTTP upstream."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest

from seocho.recorder.config import RecorderConfig
from seocho.recorder.mock_backend import create_mock_app
from seocho.recorder.proxy import create_app
from seocho.recorder.store import RecorderStore


def tags(i: int = 0, run: str = 'run') -> dict[str, str]:
    return {'X-Workspace-Id': 'w', 'X-Run-Id': run, 'X-Agent-Id': 'agent', 'X-Step-Id': f's{i}',
            'X-Tier': 'T1_RETRIEVAL', 'X-Template-Version': 'v1', 'X-Call-Id': f'call-{run}-{i}'}


async def exercise(tmp: Path, mode: str, operation: Any, *, transport: Any = None,
                   missing: str = 'reject', namespace: str = 'test') -> Any:
    upstream = create_mock_app()
    app = create_app(RecorderConfig(upstream_url='http://mock/v1', directory=tmp,
        cache_mode=mode, cache_namespace=namespace, missing_tags=missing),
        transport=transport or httpx.ASGITransport(app=upstream))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://recorder') as client:
            return await operation(client, app.state.store, upstream)


BODY = {'model': 'mock-fixed', 'messages': [{'role': 'system', 'content': 'shared system'},
        {'role': 'user', 'content': 'question'}], 'temperature': 0.1, 'seed': 42, 'max_tokens': 100}


def test_100_calls_cache_and_dedup(tmp_path: Path) -> None:
    async def operation(client: Any, store: Any, upstream: Any) -> None:
        for i in range(100):
            response = await client.post('/v1/chat/completions', headers=tags(i), json=BODY)
            assert response.status_code == 200
            assert response.headers['X-Cache-Hit'] == ('false' if i == 0 else 'true')
        calls = store.calls(run_id='run')
        assert len(calls) == 100
        assert upstream.state.requests == 1
        assert len({c['start']['request_blob'] for c in calls}) == 1
        assert len({c['finish']['response_blob'] for c in calls}) == 1
        assert len({c['start']['message_blobs'][0] for c in calls}) == 1
        assert calls[0]['finish']['usage']['reasoning_tokens'] == 3
        assert calls[0]['finish']['cost_usd'] is None
        assert calls[1]['finish']['cost_usd'] == 0
        assert not calls[1]['finish']['provider_work']
        assert store.audit('run', [tags(i)['X-Call-Id'] for i in range(100)])['coverage_proven']
        assert not store.audit('run', ['nonexistent'])['valid']
        raw = store.blobs.get(calls[0]['start']['request_blob'])
        assert json.loads(raw) == BODY
        assert b'Authorization' not in raw
    asyncio.run(exercise(tmp_path, 'read_write', operation))
    db = duckdb.connect(str(tmp_path / 'recorder.duckdb'), read_only=True)
    assert db.execute('SELECT count(*) FROM raw_events').fetchone()[0] == 200
    db.close()


def test_off_mode_independent_sampling_and_semantic_hash(tmp_path: Path) -> None:
    async def operation(client: Any, store: Any, upstream: Any) -> None:
        bodies = [BODY, dict(reversed(list(BODY.items()))), {**BODY, 'seed': 43},
                  {**BODY, 'temperature': 0.2}, {**BODY, 'response_format': {'type': 'json_object'}}]
        for i, body in enumerate(bodies):
            assert (await client.post('/v1/chat/completions', headers=tags(i), json=body)).status_code == 200
        calls = store.calls()
        hashes = [c['start']['request_hash'] for c in calls]
        assert hashes[0] == hashes[1]
        assert len(set(hashes)) == 4
        assert upstream.state.requests == 5
        assert all(not c['finish']['cache_hit'] for c in calls)
    asyncio.run(exercise(tmp_path, 'off', operation))


def test_replay_only_hit_miss_and_domain_namespace_isolation(tmp_path: Path) -> None:
    async def fill(client: Any, store: Any, upstream: Any) -> None:
        await client.post('/v1/chat/completions', headers=tags(), json=BODY)
    asyncio.run(exercise(tmp_path, 'read_write', fill))

    async def replay(client: Any, store: Any, upstream: Any) -> None:
        hit = await client.post('/v1/chat/completions', headers=tags(1), json=BODY)
        assert hit.status_code == 200 and hit.headers['X-Cache-Hit'] == 'true'
        miss = await client.post('/v1/chat/completions', headers=tags(2), json={**BODY, 'seed': 99})
        assert miss.status_code == 409
        gold = await client.post('/v1/chat/completions', headers={**tags(3), 'X-Record-Domain': 'evaluation'}, json=BODY)
        assert gold.status_code == 409
        assert upstream.state.requests == 0
    asyncio.run(exercise(tmp_path, 'replay_only', replay))

    async def other(client: Any, store: Any, upstream: Any) -> None:
        assert (await client.post('/v1/chat/completions', headers=tags(4), json=BODY)).status_code == 409
    asyncio.run(exercise(tmp_path, 'replay_only', other, namespace='other-account'))


def test_missing_tags_reject_and_quarantine(tmp_path: Path) -> None:
    async def reject(client: Any, store: Any, upstream: Any) -> None:
        assert (await client.post('/v1/chat/completions', json=BODY)).status_code == 422
        assert upstream.state.requests == 0
        assert store.calls()[0]['start']['tag_status'] == 'UNTAGGED'
    asyncio.run(exercise(tmp_path / 'reject', 'off', reject))

    async def quarantine(client: Any, store: Any, upstream: Any) -> None:
        assert (await client.post('/v1/chat/completions', headers={'X-Run-Id': 'run'}, json=BODY)).status_code == 200
        assert not store.audit('run')['valid']
    asyncio.run(exercise(tmp_path / 'quarantine', 'off', quarantine, missing='quarantine'))


def test_streaming_and_responses_are_captured(tmp_path: Path) -> None:
    async def operation(client: Any, store: Any, upstream: Any) -> None:
        response = await client.post('/v1/chat/completions', headers=tags(), json={**BODY, 'stream': True})
        call = store.calls()[0]
        assert store.blobs.get(call['finish']['response_blob']) == response.content
        assert call['finish']['usage']['reasoning_tokens'] == 3
        assert not call['finish']['cache_eligible']
        response = await client.post('/v1/responses', headers=tags(1), json={'model': 'mock-fixed', 'input': 'hi'})
        assert response.status_code == 200
        assert store.calls()[1]['finish']['usage']['input_tokens'] == 20
    asyncio.run(exercise(tmp_path, 'read_write', operation))


def test_upstream_failure_recording_and_auth_not_forwarded_from_client(tmp_path: Path) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        assert request.headers.get('Authorization') is None
        assert 'X-Run-Id' not in request.headers
        return httpx.Response(429, json={'error': 'overloaded'})

    async def operation(client: Any, store: Any, upstream: Any) -> None:
        response = await client.post('/v1/chat/completions', headers={**tags(), 'Authorization': 'Bearer client-secret'}, json=BODY)
        assert response.status_code == 429
        call = store.calls()[0]
        assert call['finish']['state'] == 'upstream_error'
        assert call['finish']['usage']['input_tokens'] is None
        assert 'client-secret' not in json.dumps(call)
    asyncio.run(exercise(tmp_path, 'off', operation, transport=httpx.MockTransport(fail)))


def test_single_writer_export_and_corruption(tmp_path: Path) -> None:
    store = RecorderStore(tmp_path)
    with pytest.raises(RuntimeError):
        RecorderStore(tmp_path)
    key = store.blobs.put(b'body')
    store.blobs.path(key).write_bytes(b'corrupt')
    with pytest.raises(ValueError):
        store.blobs.get(key)
    store.export(tmp_path / 'events.parquet')
    assert (tmp_path / 'events.parquet').exists()
    store.close()


def test_concurrent_calls_have_isolated_tags(tmp_path: Path) -> None:
    async def operation(client: Any, store: Any, upstream: Any) -> None:
        responses = await asyncio.gather(*[client.post('/v1/chat/completions',
            headers={**tags(i), 'X-Agent-Id': f'agent-{i}'}, json=BODY) for i in range(20)])
        assert all(r.status_code == 200 for r in responses)
        assert {c['start']['tags']['agent_id'] for c in store.calls()} == {f'agent-{i}' for i in range(20)}
    asyncio.run(exercise(tmp_path, 'off', operation))


def test_actual_agents_sdk_adapter_concurrent_context_and_manifest(tmp_path: Path) -> None:
    from agents import Agent, RunConfig, Runner
    from seocho.agent.collaboration import BudgetedModel, Ledger, Limits
    from seocho.recorder.adapters.openai_agents import recorded_client

    async def operation(client: Any, store: Any, upstream: Any) -> None:
        # Reuse the real Recorder ASGI app through the SDK client's HTTP transport.
        app = client._transport.app
        recorded, sdk_client = recorded_client(recorder_url='http://recorder/v1', model='mock-fixed',
            workspace_id='w', run_id='batch', tier='T1_RETRIEVAL', template_version='test-v1',
            transport=httpx.ASGITransport(app=app))
        try:
            async def solve(role: str, run: str) -> None:
                budgeted = BudgetedModel(recorded.for_run(run, 'test-condition'), Ledger(Limits()), role=role)
                result = await Runner.run(Agent(name=role, instructions='Answer', model=budgeted),
                    'context-' + run, run_config=RunConfig(tracing_disabled=True))
                assert result.final_output.startswith('fixed-')

            await asyncio.gather(solve('Solver', 'alpha'), solve('Specialist_task_1', 'beta'),
                                 solve('Evaluator', 'judge'))
            assert len(recorded.attempts) == 3
            for run, role in [('alpha', 'Solver'), ('beta', 'Specialist_task_1'), ('judge', 'Evaluator')]:
                call = store.calls(run_id=run)[0]
                assert call['start']['tags']['agent_id'] == role
                assert call['start']['tags']['condition_id'] == 'test-condition'
                assert store.audit(run, [a['call_id'] for a in recorded.attempts if a['run_id'] == run])['coverage_proven']
            assert store.calls(run_id='judge')[0]['start']['domain'] == 'evaluation'
            assert store.calls(run_id='beta')[0]['start']['tags']['subtask_id'] == 'task_1'
        finally:
            await sdk_client.close()
    asyncio.run(exercise(tmp_path, 'off', operation))
