from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .blobs import digest
from .store import canonical


def create_mock_app() -> FastAPI:
    app = FastAPI()
    app.state.requests = 0

    @app.get('/stats')
    async def stats() -> dict[str, int]:
        return {'requests': app.state.requests}

    @app.get('/v1/models')
    async def models() -> dict[str, Any]:
        return {'object': 'list', 'data': [{'id': 'mock-fixed', 'object': 'model'}]}

    @app.post('/v1/{endpoint:path}')
    async def completion(endpoint: str, request: Request) -> Response:
        body = await request.json()
        app.state.requests += 1
        key = digest(canonical(body))
        text = 'fixed-' + key[:16]
        messages = body.get('messages', [])
        instructions = ' '.join(str(m.get('content', '')) for m in messages if m.get('role') in ('system', 'developer'))
        if 'ROLE=' in instructions:
            user = next((m.get('content', '{}') for m in reversed(messages) if m.get('role') == 'user'), '{}')
            payload = json.loads(user)
            properties = body.get('response_format',{}).get('json_schema',{}).get('schema',{}).get('properties',{})
            if not properties and 'Schema: ' in instructions:
                properties = json.loads(instructions.split('Schema: ',1)[1]).get('properties',{})
            if 'decompose' in properties:
                text = json.dumps({'decompose':True,'decision_summary':'Contract fixture only','steps':[
                    {'step_id':'facts','question':payload['question'],'required_slots':['facts'],'depends_on':[], 'completion_criteria':'Source facts identified','requested_capabilities':[]},
                    {'step_id':'check','question':payload['question'],'required_slots':['answer'],'depends_on':['facts'],'completion_criteria':'Check answer','requested_capabilities':[]}]})
            elif 'decomposition_coverage' in properties:
                dimension = {'verdict':'unknown','rationale':'Contract fixture only'}
                text = json.dumps({**{k:dimension for k in ('decomposition_coverage','dependency_validity','unnecessary_work','missing_evidence_handling','synthesis_faithfulness')},'rationale':'Contract fixture only'})
            elif 'completion_claim' in properties:
                text = json.dumps({'answer':'Fixture finding','evidence_refs':[r['evidence_id'] for r in payload['evidence']], 'missing_slots':[], 'unsupported_assumptions':[], 'completion_claim':'Fixture only'})
            elif 'ROLE=planner' in instructions:
                text = json.dumps({'tasks': [{'task_id': 'task_1', 'question': payload['question'], 'difficulty': 1},
                    {'task_id': 'task_2', 'question': payload['question'], 'difficulty': 2}]})
            elif 'available_tiers' in payload:
                text = json.dumps({'tier': payload['available_tiers'][-1], 'model_key': payload.get('available_models', ['solver'])[-1]})
            elif 'task_ids' in body.get('response_format',{}).get('json_schema',{}).get('schema',{}).get('properties',{}):
                text = json.dumps({'task_ids':[r['task_id'] for r in payload['findings']],'rationale':'Fixture selects existing tasks'})
            elif 'required_slots' in body.get('response_format',{}).get('json_schema',{}).get('schema',{}).get('properties',{}):
                text = json.dumps({'answer':'Initial fixture answer', 'evidence_refs': [r['evidence_id'] for r in payload['evidence']], 'answerable':False, 'confidence':0.2, 'required_slots':[], 'unsupported_assumptions':[], 'requested_capabilities':['calculator']})
            elif 'ROLE=evaluator' in instructions:
                if payload.get('rubric_version', '').startswith('finder-evaluator-v2'):
                    dimension = {'verdict': 'unknown', 'rationale': 'Mock contract only'}
                    text = json.dumps({'verdict': 'unknown', 'rationale': 'Mock contract only',
                        **{key: dimension for key in ('answer_equivalence', 'evidence_support',
                        'citation_validity', 'reasoning_validity', 'units_and_periods', 'abstention')},
                        'claims': [], 'calculations': [],
                        **({k: dimension for k in ('context_use','contradiction_handling','synthesis_faithfulness')} if payload.get('rubric_version') == 'finder-evaluator-v2-coordination-v1' else {}),
                        **({k: dimension for k in ('tool_relevance','dependency_handling','handoff_quality')} if payload.get('rubric_version') == 'finder-evaluator-v2-tools-v1' else {})})
                else:
                    text = json.dumps({'verdict': 'mock_contract_only', 'rationale': 'Deterministic fixture, not quality evidence'})
            else:
                rows = payload.get('evidence', [])
                refs = [r['evidence_id'] for r in rows]
                if 'returned_contexts' in payload:
                    refs = sorted({ref for row in payload['returned_contexts'] for ref in row['evidence_refs']})
                text = json.dumps({'answer': 'fixed-' + key[:16], 'evidence_refs': refs})
        if body.get('tools'):
            user = next(m['content'] for m in reversed(messages) if m['role']=='user')
            payload = json.loads(user)
            text = json.dumps({'answer':'Fixture tool answer','evidence_refs':[r['evidence_id'] for r in payload.get('initial_evidence',payload.get('evidence',[]))], 'missing_slots':[], 'unsupported_assumptions':[], 'completion_claim':'Fixture only'})
        usage = {'prompt_tokens': 20, 'completion_tokens': 8, 'total_tokens': 28,
                 'completion_tokens_details': {'reasoning_tokens': 3}}
        if endpoint == 'responses':
            result = {'id': 'resp_' + key[:12], 'object': 'response', 'model': body.get('model'),
                      'output': [{'type': 'message', 'role': 'assistant', 'content': [
                          {'type': 'output_text', 'text': text}]}],
                      'usage': {'input_tokens': 20, 'output_tokens': 8,
                                'output_tokens_details': {'reasoning_tokens': 3}}}
        else:
            result = {'id': 'chatcmpl-' + key[:12], 'object': 'chat.completion', 'created': 0,
                      'model': body.get('model'), 'choices': [{'index': 0, 'finish_reason': 'stop',
                      'message': {'role': 'assistant', 'content': text}}], 'usage': usage}
        if body.get('stream'):
            # Deterministic SSE transport fixture; not a performance/service claim.
            raw = b'data: ' + json.dumps(result).encode() + b'\n\ndata: [DONE]\n\n'
            return Response(raw, media_type='text/event-stream')
        return JSONResponse(result)

    return app
