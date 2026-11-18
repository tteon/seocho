#!/usr/bin/env python3
"""Generate the committed ``seocho.llm_exchange.v1`` golden fixtures.

Every input here is synthetic (some are shortened, synthetic re-creations of response
shapes observed on the MARA endpoint).  No API key, no real Bearer token, no private
prompt text.  The test suite regenerates everything and compares bytes, so a fixture can
only change together with this file.

    python3 rust/crates/seocho-llm-conformance/python/make_exchange_fixtures.py          # write
    python3 rust/crates/seocho-llm-conformance/python/make_exchange_fixtures.py --check  # exit 1 on drift
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exchange_v1 as x  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]  # repository root (python/ -> crate -> crates -> rust -> repo)
SPEC_DIR = HERE.parent / 'spec'
FIXTURE_DIR = SPEC_DIR / 'fixtures'
SOURCE_DIR = SPEC_DIR / 'sources'
EXPECTED = SPEC_DIR / 'expected.json'

RUN_ID = 'golden-v1'
CAPTURED_AT = '2026-10-26T00:00:00Z'
START_NS = 1792972800000000000  # 2026-10-26T00:00:00Z

DOC = ('Acme Robotics acquired Beta Sensors in March 2021. Beta Sensors is headquartered in Austin. '
       'Acme Robotics reported revenue of 120 million dollars in fiscal 2023.')
SYSTEM = 'Extract entities and relationships as JSON with keys "nodes" and "relationships".'
FACTS = {'nodes': [{'id': 'acme', 'label': 'Company', 'name': 'Acme Robotics'},
                   {'id': 'beta', 'label': 'Company', 'name': 'Beta Sensors'}],
         'relationships': [{'source': 'acme', 'target': 'beta', 'type': 'ACQUIRED', 'year': 2021}]}
SUBMIT_FACTS_TOOL = {'type': 'function', 'function': {
    'name': 'submit_facts', 'description': 'Submit extracted facts.',
    'parameters': {'type': 'object', 'properties': {
        'nodes': {'type': 'array', 'items': {'type': 'object'}},
        'relationships': {'type': 'array', 'items': {'type': 'object'}}},
        'required': ['nodes', 'relationships']}}}
FORCED = {'type': 'function', 'function': {'name': 'submit_facts'}}


def compact(value) -> str:
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


def completion(message: dict, *, model: str, finish: str = 'stop', usage: Optional[dict] = None,
               cid: str = 'chatcmpl-golden') -> str:
    """Wire-shaped MARA chat.completion bytes (key order as the provider emits it)."""
    return compact({'choices': [{'finish_reason': finish, 'index': 0, 'logprobs': None, 'message': message}],
                    'created': 1792972800, 'id': cid, 'model': model, 'object': 'chat.completion',
                    'system_fingerprint': 'fastcoe',
                    'usage': usage or {'completion_tokens': 64, 'prompt_tokens': 180, 'total_tokens': 244}})


def request(model: str, *, json_mode: bool = True, tools=None, tool_choice=None, max_tokens: int = 1024) -> dict:
    body = {'model': model,
            'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': DOC}],
            'max_tokens': max_tokens, 'temperature': 0.0}
    if json_mode:
        body['response_format'] = {'type': 'json_object'}
    if tools is not None:
        body['tools'] = tools
    if tool_choice is not None:
        body['tool_choice'] = tool_choice
    return body


def record(name: str, *, model: str, req: dict, status: Optional[int], body: Optional[str] = None,
           body_raw: Optional[bytes] = None, task: str, wire_dialect: Optional[str] = None,
           applied=('auth_header_dropped',), replay_class: str = 'full', attempt: int = 0,
           step_kind: str = 'initial', next_step_of: Optional[str] = None, latency_ms: float = 900.0,
           usage: Optional[dict] = None, kind: str = 'synthetic', transport_error=None,
           headers: Optional[dict] = None, request_id: Optional[str] = None) -> dict:
    raw = body_raw if body_raw is not None else (body.encode('utf-8') if body is not None else None)
    rec = {
        'schema_version': x.SCHEMA_VERSION,
        'rec_id': None,
        'captured_at': CAPTURED_AT,
        'source': {'kind': kind, 'run_id': RUN_ID, 'git_sha': None, 'llm_io_version': None,
                   'wire': False, 'served_from': None, 'origin': None},
        'endpoint': dict(x.MARA_ENDPOINT),
        'model': model,
        'case': {'case_id': f'golden/{name}', 'task': task, 'input_class': 'synthetic',
                 'contract_sha256': None, 'wire_dialect': wire_dialect, 'profile_sha256': None,
                 'enforcement': None},
        'chain': {'logical_call_id': f'G0#{name}', 'attempt': attempt, 'next_step_of': next_step_of,
                  'step_kind': step_kind, 'agent': 'G0'},
        'request': {'body': req, 'body_scope': 'full', 'messages_sha256': None,
                    'req_hash': x.compute_req_hash(req), 'req_hash_scheme': 'llm_exchange.v1',
                    'occurrence': 0},
        'response': {'http_status': status,
                     'headers': headers if headers is not None else (
                         {} if status is None else {'content-type': 'application/json',
                                                    'x-request-id': request_id or f'req-golden-{name}'}),
                     'body_fidelity': 'raw' if raw is not None else None,
                     **(x.encode_body(raw) if raw is not None else {'body_utf8': None, 'body_b64': None}),
                     'body_sha256': x.sha256_tag(raw) if raw is not None else None,
                     'truncated': False, 'transport_error': transport_error},
        'timing': {'latency_ms': latency_ms, 'start_ns': START_NS},
        'usage': usage,
        'live_outcome': None,
        'oracle_outcome': None,
        'redaction': {'policy': x.REDACTION_POLICY, 'applied': list(applied), 'replay_class': replay_class,
                      'replayable': replay_class != 'none'},
    }
    rec['rec_id'] = x.compute_rec_id(rec)
    return x.check(rec)


# --------------------------------------------------------------------------- legacy sources

def legacy_502_row() -> dict:
    """sk-llm/1 row as written by the pre-raw recorder: a non-JSON 502 page kept as unparsed_body[:4000]."""
    page = ('<html><head><title>502 Bad Gateway</title></head><body><h1>502 Bad Gateway</h1>'
            '<p>upstream connect error or disconnect/reset before headers.</p><!-- '
            + ''.join(f'pad{i:04d} ' for i in range(600)) + '--></body></html>')
    unparsed = page[:x.LEGACY_UNPARSED_LIMIT]
    assert len(page) > x.LEGACY_UNPARSED_LIMIT
    return {'schema': 'sk-llm/1', 'run_id': 'golden-legacy', 'arm': 'iso', 'session_id': 'iso',
            'agent': 'A1', 'role': 'extractor', 'task_hint': 'graph_extraction',
            'logical_call_id': 'A1#7', 'attempt': 1,
            'req_hash': x.compute_req_hash(request('gpt-oss-120b')).split(':', 1)[1],
            'occurrence': 0, 'served_from': 'live',
            'request': {'model': 'gpt-oss-120b', 'response_format': {'type': 'json_object'},
                        'max_tokens': 1024, 'temperature': 0.0,
                        'messages_sha256': '0' * 63 + '1'},
            'response': {'http_status': 502, 'body': {'unparsed_body': unparsed}, 'error_class': None,
                         'finish_reason': None, 'usage': {}, 'content_len': 0,
                         'reasoning_fallback': False, 'tool_call_present': False},
            'classification': 'http_5xx', 'latency_ms': 30012.5, 'start_ns': START_NS + 1_000_000_000}


def raw_timeout_row() -> dict:
    """sk-llm/1 row as written by the raw-byte recorder for a transport timeout: no response, no body."""
    row = {'schema': 'sk-llm/1', 'run_id': 'golden-raw', 'arm': 'shared', 'session_id': 'shared',
           'agent': 'S2', 'role': 'extractor', 'task_hint': 'graph_extraction',
           'logical_call_id': 'S2#3', 'attempt': 0,
           'req_hash': x.compute_req_hash(request('gpt-oss-120b')).split(':', 1)[1],
           'occurrence': 0, 'served_from': 'live',
           'request': request('gpt-oss-120b'),
           'response': {'http_status': None, 'body': None, 'error_class': 'timeout', 'finish_reason': None,
                        'usage': {}, 'content_len': 0, 'reasoning_fallback': False,
                        'tool_call_present': False, 'body_fidelity': None, 'headers': {},
                        'body_utf8': None, 'body_b64': None, 'body_sha256': None},
           'redaction': {'policy': 'rec-redact/1', 'applied': ['auth_header_dropped'],
                         'replay_class': 'full', 'replayable': True},
           'classification': 'timeout', 'latency_ms': 900001.0, 'start_ns': START_NS + 2_000_000_000}
    # exactly the raw-byte recorder's rec_id expression (spec §4): run scope, no body
    row['rec_id'] = x.sha256_tag('||'.join([x.sk_llm1_run_scope(row), row['logical_call_id'], str(row['attempt']),
                                            '']).encode())
    return row


SOURCES = {'08-legacy-502-truncated.sk-llm-1.json': legacy_502_row,
           '11-raw-timeout-transport-error.sk-llm-1.json': raw_timeout_row}


def source_origin(name: str) -> dict:
    data = x.dumps(SOURCES[name]()).encode()
    return {'path': f'rust/crates/seocho-llm-conformance/spec/sources/{name}', 'file_sha256': x.sha256_tag(data)}


# --------------------------------------------------------------------------- fixtures

def fixtures() -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    gpt, ds, gemma = 'gpt-oss-120b', 'DeepSeek-V3.1', 'gemma-3-27b-it'

    out['01-content-json'] = record(
        '01', model=gpt, req=request(gpt), status=200, task='graph_extraction', wire_dialect='json_object_prompt',
        body=completion({'content': compact(FACTS), 'reasoning': 'Two companies and one acquisition. Output JSON only.',
                         'role': 'assistant'}, model=gpt,
                        usage={'completion_tokens': 96, 'completion_tokens_details': {'reasoning_tokens': 14},
                               'prompt_tokens': 180, 'total_tokens': 276}),
        usage={'completion_tokens': 96, 'completion_tokens_details': {'reasoning_tokens': 14},
               'prompt_tokens': 180, 'total_tokens': 276})

    fenced = 'Here is the extracted graph for the document.\n\n```json\n' + json.dumps(FACTS, indent=2) + '\n```\n'
    out['02-fenced-json-prose-preamble'] = record(
        '02', model=ds, req=request(ds), status=200, task='graph_extraction', wire_dialect='json_object_prompt',
        body=completion({'content': fenced, 'role': 'assistant'}, model=ds))

    usage3 = {'completion_tokens': 512, 'completion_tokens_details': {'reasoning_tokens': 512},
              'prompt_tokens': 180, 'total_tokens': 692}
    out['03-reasoning-only-length'] = record(
        '03', model=gpt, req=request(gpt, max_tokens=512), status=200, task='graph_extraction',
        wire_dialect='json_object_prompt', usage=usage3,
        body=completion({'content': '', 'reasoning': 'We need nodes for Acme Robotics and Beta Sensors. '
                         'The acquisition happened in 2021. Revenue is a property of Acme. Let us also consider '
                         'whether Austin is a node or a property; the schema says',
                         'role': 'assistant'}, model=gpt, finish='length', usage=usage3))

    out['04-forced-tool-call-string-args'] = record(
        '04', model=gpt, req=request(gpt, json_mode=False, tools=[SUBMIT_FACTS_TOOL], tool_choice=FORCED),
        status=200, task='tool_call(submit_facts)', wire_dialect='tool_parameters',
        body=completion({'content': None, 'reasoning': 'Call submit_facts with the two companies.',
                         'role': 'assistant',
                         'tool_calls': [{'function': {'arguments': json.dumps(FACTS), 'name': 'submit_facts'},
                                         'id': 'call_golden04', 'type': 'function'}]},
                        model=gpt, finish='tool_calls'))

    out['05-tool-call-object-args'] = record(
        '05', model=gemma, req=request(gemma, json_mode=False, tools=[SUBMIT_FACTS_TOOL], tool_choice=FORCED),
        status=200, task='tool_call(submit_facts)', wire_dialect='tool_parameters',
        body=completion({'content': None, 'role': 'assistant',
                         'tool_calls': [{'function': {'arguments': FACTS, 'name': 'submit_facts'},
                                         'id': 'call_golden05', 'type': 'function'}]},
                        model=gemma, finish='tool_calls'))

    out['06-http400-did-not-complete'] = record(
        '06', model=gpt, req=request(gpt, json_mode=False, tools=[SUBMIT_FACTS_TOOL], tool_choice='auto',
                                     max_tokens=256),
        status=400, task='tool_call(submit_facts)', wire_dialect='tool_parameters', latency_ms=4100.0,
        body=compact({'error': 'The model output did not complete a valid function call within max_tokens=256.',
                      'error_code': None, 'error_param': 'max_tokens', 'error_type': 'Invalid function calling output.'}))

    out['07-http402-secret-scrubbed'] = record(
        '07', model=ds, req=request(ds, max_tokens=256), status=402, task='preflight', latency_ms=1150.0,
        applied=('auth_header_dropped', 'body_secret_scrubbed', 'body_billing_redacted'), replay_class='none',
        body=compact({'error': {'balance_units': '[redacted]', 'billing_portal_url': '[redacted]',
                                'code': 'PAYMENT_METHOD_REQUIRED',
                                'message': 'A payment method is required for key sk-[redacted]. '
                                           'Add one in the billing portal to continue.'}}))

    out['08-legacy-502-truncated'] = x.convert_sk_llm1(
        legacy_502_row(), origin=source_origin('08-legacy-502-truncated.sk-llm-1.json'), input_class='synthetic')

    emo = ('<|channel|>analysis<|message|>We need to call submit_facts with nodes and relationships. '
           'Acme Robotics acquired Beta Sensors in 2021, so one ACQUIRED edge. Let us write the call.')
    out['09-http400-tool-choice-error-model-output'] = record(
        '09', model=gpt, req=request(gpt, json_mode=False, tools=[SUBMIT_FACTS_TOOL], tool_choice=FORCED,
                                     max_tokens=512),
        status=400, task='tool_call(submit_facts)', wire_dialect='tool_parameters', latency_ms=2600.0,
        body=compact({'error': 'Specified tool_choice with function name "submit_facts", but the model output '
                               'contains zero valid function call.',
                      'error_code': None, 'error_model_output': emo, 'error_param': None,
                      'error_type': 'Invalid function calling output.'}))

    first = out['09-http400-tool-choice-error-model-output']['rec_id']
    out['10-http400-json-truncation-downgrade'] = record(
        '09', model=gpt, req=request(gpt, max_tokens=512), status=400, task='tool_call(submit_facts)',
        wire_dialect='json_object_prompt', attempt=1, step_kind='downgrade', next_step_of=first, latency_ms=3900.0,
        request_id='req-golden-10',  # same logical call as 09, but a new HTTP attempt has its own request id
        body=compact({'error': {'code': 'json_validate_failed', 'message': 'Generated JSON was truncated: the '
                                'output token limit was reached before the JSON object was closed.',
                                'type': 'invalid_request_error'}}))

    out['11-raw-timeout-transport-error'] = x.convert_sk_llm1(
        raw_timeout_row(), origin=source_origin('11-raw-timeout-transport-error.sk-llm-1.json'),
        input_class='synthetic')

    reasoning_redacted = completion({'content': compact(FACTS), 'reasoning': '[private reasoning omitted]',
                                     'role': 'assistant'}, model=gpt,
                                    usage={'completion_tokens': 96, 'completion_tokens_details': {'reasoning_tokens': 0},
                                           'prompt_tokens': 180, 'total_tokens': 276})
    out['12-carrier-intact-reasoning-redacted'] = record(
        '12', model=gpt, req=request(gpt), status=200, task='graph_extraction', wire_dialect='json_object_prompt',
        kind='synthetic-derived', replay_class='carrier_intact', body=reasoning_redacted,
        applied=('auth_header_dropped', '/choices/0/message/reasoning',
                 '/usage/completion_tokens_details/reasoning_tokens'))

    latin1 = '<html><body>502 Bad Gateway - caf\xe9 proxy</body></html>'.encode('latin-1')
    out['13-non-utf8-body-b64'] = record(
        '13', model=gpt, req=request(gpt), status=502, task='graph_extraction', body_raw=latin1,
        headers={'content-type': 'text/html; charset=iso-8859-1'}, latency_ms=60.0)
    return out


def expected(fx: Dict[str, dict]) -> dict:
    return {'schema_version': x.SCHEMA_VERSION, 'generator': 'rust/crates/seocho-llm-conformance/python/make_exchange_fixtures.py',
            'fixtures': {f'{name}.json': {'rec_id': r['rec_id'], 'replay_class': r['redaction']['replay_class'],
                                          'replayable': r['redaction']['replayable'],
                                          'body_sha256': r['response']['body_sha256'],
                                          'body_fidelity': r['response']['body_fidelity'],
                                          'http_status': r['response']['http_status']}
                         for name, r in sorted(fx.items())}}


def render() -> Dict[Path, str]:
    fx = fixtures()
    files: Dict[Path, str] = {FIXTURE_DIR / f'{n}.json': x.dumps(r) for n, r in fx.items()}
    files.update({SOURCE_DIR / n: x.dumps(make()) for n, make in SOURCES.items()})
    files[EXPECTED] = x.dumps(expected(fx))
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--check', action='store_true', help='compare against the committed files, write nothing')
    args = ap.parse_args(argv)
    drift = []
    for path, text in render().items():
        if args.check:
            if not path.exists() or path.read_text(encoding='utf-8') != text:
                drift.append(str(path.relative_to(REPO)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
    if drift:
        print('fixture drift:\n  ' + '\n  '.join(drift), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
