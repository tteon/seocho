from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .blobs import digest
from .config import RecorderConfig
from .store import RecorderStore, canonical
from .usage import extract_usage

HEADERS = {'condition_id': 'X-Condition-Id', 'workspace_id': 'X-Workspace-Id', 'run_id': 'X-Run-Id', 'agent_id': 'X-Agent-Id',
           'step_id': 'X-Step-Id', 'subtask_id': 'X-Subtask-Id', 'parent_step': 'X-Parent-Step',
           'tier': 'X-Tier', 'template_version': 'X-Template-Version', 'budget_soft': 'X-Budget-Soft'}
REQUIRED = ('workspace_id', 'run_id', 'agent_id', 'step_id', 'tier', 'template_version')


def create_app(config: RecorderConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    store = RecorderStore(config.directory)
    client = httpx.AsyncClient(timeout=config.timeout_seconds, transport=transport)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await client.aclose()
        store.close()

    app = FastAPI(lifespan=lifespan)
    app.state.store = store
    app.state.client = client

    def auth() -> dict[str, str]:
        return {'Authorization': 'Bearer ' + config.upstream_api_key.get_secret_value()} if config.upstream_api_key else {}

    @app.get('/calls/{call_id}')
    async def call_facts(call_id: str, run_id: str, workspace_id: str) -> Response:
        record = next((c for c in store.calls(run_id=run_id) if c['call_id'] == call_id
                       and c['start']['tags']['workspace_id'] == workspace_id), None)
        if not record:
            return JSONResponse({'error': 'Call not found'}, status_code=404)
        return JSONResponse({'call_id': call_id, 'finish': record.get('finish'),
                             'tags': record['start']['tags']})

    @app.get('/runs/{run_id}/snapshot')
    async def run_snapshot(run_id: str, workspace_id: str) -> Response:
        events = store.agent_events(run_id)
        if not events or any(e['workspace_id'] != workspace_id for e in events):
            return JSONResponse({'error': 'Run not found'}, status_code=404)
        if not store.audit_agent_events(run_id)['valid'] or not store.audit(run_id)['valid']:
            return JSONResponse({'error': 'Invalid base run'}, status_code=409)
        public_events = [dict(e, payload=json.loads(store.blobs.get(e['payload_blob'], 'inference')))
                         for e in events if e['domain'] == 'inference']
        terminals = [e for e in public_events if e['action'] == 'run' and e['kind'] == 'realization']
        if not terminals or terminals[-1]['payload'].get('status') != 'completed':
            return JSONResponse({'error': 'Base run incomplete'}, status_code=409)
        calls = [dict(c, request=store.blobs.get(c['start']['request_blob'], 'inference').decode())
                 for c in store.calls(run_id=run_id, domain='inference')]
        return JSONResponse({'run_id': run_id, 'events': public_events, 'calls': calls})

    @app.post('/events')
    async def agent_event(request: Request) -> Response:
        from .events import AgentEvent
        from pydantic import ValidationError
        try:
            event = AgentEvent.model_validate(await request.json())
        except (ValueError, ValidationError):
            return JSONResponse({'error': 'Invalid agent event'}, status_code=422)
        try:
            store.append_agent_event(event)
        except Exception:
            return JSONResponse({'error': 'Event storage failure or duplicate event ID'}, status_code=503)
        return JSONResponse({'event_id': event.event_id}, status_code=201)

    @app.get('/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok', 'cache_mode': config.cache_mode}

    @app.get('/v1/models')
    async def models() -> Response:
        response = await client.get(config.upstream_url.rstrip('/') + '/models', headers=auth())
        return Response(response.content, response.status_code, media_type='application/json')

    @app.post('/v1/{endpoint:path}')
    async def forward(endpoint: str, request: Request) -> Response:
        if endpoint not in ('chat/completions', 'responses'):
            return JSONResponse({'error': 'Unsupported inference route'}, status_code=404)
        raw = await request.body()
        if len(raw) > config.max_request_bytes:
            return JSONResponse({'error': 'Request too large'}, status_code=413)
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError('JSON object required')
            normalized = canonical(body)
        except (ValueError, TypeError):
            return JSONResponse({'error': 'Valid JSON object required'}, status_code=400)
        tags = {key: request.headers.get(header, '') for key, header in HEADERS.items()}
        missing = [key for key in REQUIRED if not tags[key]]
        if tags['tier'] not in ('T0_PARAMETRIC', 'T1_RETRIEVAL', 'T2_REFERENCE'):
            missing.append('valid_tier')
        domain = request.headers.get('X-Record-Domain', 'inference')
        if domain not in ('inference', 'evaluation'):
            return JSONResponse({'error': 'Invalid record domain'}, status_code=400)
        call_id = request.headers.get('X-Call-Id') or uuid4().hex
        # Semantic fields are never discarded. Tags, IDs and Authorization are not hash inputs.
        namespace = config.cache_namespace
        req_hash = digest(canonical([config.upstream_url, endpoint, namespace, domain, body]))
        started = time.monotonic()
        status = 'UNTAGGED' if missing else 'TAGGED'
        try:
            components = [store.blobs.put(canonical(m), domain) for m in body.get('messages', [])]
            request_blob = store.blobs.put(raw, domain)
            store.append(call_id, 'start', {'tags': tags, 'domain': domain, 'tag_status': status,
                'missing_tags': missing, 'request_hash': req_hash, 'request_blob': request_blob,
                'normalized_request_blob': store.blobs.put(normalized, domain), 'message_blobs': components,
                'namespace': namespace, 'upstream_url': config.upstream_url, 'model_requested': body.get('model'),
                'soft_budget': tags.get('budget_soft') or None,
                'endpoint': endpoint, 'hard_budget': body.get('max_completion_tokens', body.get('max_tokens', body.get('max_output_tokens'))),
                'timestamp': datetime.now(timezone.utc).isoformat(), 'cache_mode': config.cache_mode})
        except Exception:
            return JSONResponse({'error': 'Recorder storage failure or duplicate call ID'}, status_code=503)

        def finish(response_body: bytes, *, http_status: int, state: str = 'completed',
                   cache_hit: bool = False, origin: str | None = None, stream: bool = False) -> None:
            usage: dict[str, Any] = {}
            observed_model = None
            if not stream:
                try:
                    value = json.loads(response_body)
                    usage = extract_usage(value)
                    observed_model = value.get('model')
                except (ValueError, TypeError, AttributeError):
                    pass
            else:
                for line in response_body.splitlines():
                    if line.startswith(b'data:'):
                        try:
                            value = json.loads(line[5:].strip())
                            observed_model = value.get('model') or (value.get('response') or {}).get('model') or observed_model
                            extracted = extract_usage(value)
                            if extracted.get('raw'):
                                usage = extracted
                        except (ValueError, AttributeError):
                            pass
            response_blob = store.blobs.put(response_body, domain)
            store.append(call_id, 'finish', {'request_hash': req_hash, 'namespace': namespace,
                'domain': domain, 'response_blob': response_blob, 'http_status': http_status,
                'state': state, 'usage': usage, 'cost_usd': 0 if cache_hit else None,
                'provider_work': not cache_hit and state not in ('rejected', 'cache_miss'),
                'model_observed': observed_model, 'cache_hit': cache_hit, 'origin_call_id': origin,
                'latency_ms': (time.monotonic() - started) * 1000,
                'cache_eligible': config.cache_mode == 'read_write' and state == 'completed'
                                  and http_status == 200 and not stream and not cache_hit,
                'call_id': call_id, 'stream': stream, 'timestamp': datetime.now(timezone.utc).isoformat()})

        if missing and config.missing_tags == 'reject':
            data = canonical({'error': 'Missing/invalid Recorder tags', 'fields': missing})
            finish(data, http_status=422, state='rejected')
            return Response(data, status_code=422, media_type='application/json', headers={'X-Call-Id': call_id})
        pinned_origin = request.headers.get('X-Replay-Origin')
        if pinned_origin:
            source = next((c for c in store.calls() if c['call_id'] == pinned_origin), None)
            if not source or source['start']['request_hash'] != req_hash or source['start']['domain'] != domain or source['start']['tags']['workspace_id'] != tags['workspace_id'] or source.get('finish', {}).get('state') != 'completed' or source['finish'].get('http_status') != 200 or source['finish'].get('stream'):
                data = canonical({'error': 'Pinned replay origin mismatch or unavailable'})
                finish(data, http_status=409, state='cache_miss')
                return Response(data, status_code=409, media_type='application/json', headers={'X-Call-Id': call_id})
            data = store.blobs.get(source['finish']['response_blob'], domain)
            finish(data, http_status=200, cache_hit=True, origin=pinned_origin)
            return Response(data, media_type='application/json', headers={'X-Call-Id': call_id, 'X-Cache-Hit': 'true'})
        cached = None
        if config.cache_mode != 'off' and not body.get('stream'):
            cached = store.cached(req_hash, namespace, domain)
        if cached:
            data = store.blobs.get(cached['response_blob'], domain)
            finish(data, http_status=200, cache_hit=True, origin=cached['call_id'])
            return Response(data, media_type='application/json', headers={'X-Call-Id': call_id, 'X-Cache-Hit': 'true'})
        if config.cache_mode == 'replay_only':
            data = canonical({'error': 'Replay cache miss'})
            finish(data, http_status=409, state='cache_miss')
            return Response(data, status_code=409, media_type='application/json', headers={'X-Call-Id': call_id})
        try:
            upstream_request = client.build_request('POST', config.upstream_url.rstrip('/') + '/' + endpoint,
                                                   content=raw, headers={**auth(), 'Content-Type': 'application/json'})
            upstream = await client.send(upstream_request, stream=True)
        except asyncio.CancelledError:
            finish(b'', http_status=499, state='cancelled')
            raise
        except httpx.HTTPError:
            data = canonical({'error': 'Upstream transport failure'})
            finish(data, http_status=502, state='upstream_error')
            return Response(data, status_code=502, media_type='application/json', headers={'X-Call-Id': call_id})
        if body.get('stream') and upstream.status_code == 200:
            async def stream_response() -> AsyncIterator[bytes]:
                chunks = []
                state = 'completed'
                try:
                    async for chunk in upstream.aiter_bytes():
                        chunks.append(chunk)
                        yield chunk
                except BaseException:
                    state = 'interrupted'
                    raise
                finally:
                    raw_stream = b''.join(chunks)
                    if state == 'completed' and b'[DONE]' not in raw_stream and b'response.completed' not in raw_stream:
                        state = 'incomplete_stream'
                    finish(raw_stream, http_status=upstream.status_code, state=state, stream=True)
                    await upstream.aclose()
            return StreamingResponse(stream_response(), status_code=upstream.status_code,
                media_type=upstream.headers.get('content-type', 'text/event-stream'), headers={'X-Call-Id': call_id})
        try:
            data = await upstream.aread()
        except BaseException:
            finish(b'', http_status=upstream.status_code, state='interrupted')
            raise
        finally:
            await upstream.aclose()
        finish(data, http_status=upstream.status_code,
               state='completed' if upstream.status_code < 400 else 'upstream_error')
        return Response(data, status_code=upstream.status_code,
                        media_type=upstream.headers.get('content-type', 'application/json'),
                        headers={'X-Call-Id': call_id, 'X-Cache-Hit': 'false'})

    return app
