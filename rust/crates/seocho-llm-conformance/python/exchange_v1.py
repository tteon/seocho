#!/usr/bin/env python3
"""Reference implementation of the ``seocho.llm_exchange.v1`` record format.

Normative spec: rust/crates/seocho-llm-conformance/spec/llm-exchange-v1.md
JSON Schema:    rust/crates/seocho-llm-conformance/spec/llm-exchange-v1.schema.json

Standard library only, so it runs under any python3.  ``validate`` hand-codes the
JSON Schema's structural rules plus the semantic rules S1-S13 the schema cannot
express (hash recomputation, cross-field consistency).  The tests cross-check the
two against each other with ``jsonschema`` when it is importable.

Public API:

* ``validate(record) -> list[str]``   empty list = valid
* ``check(record)``                   raises ``InvalidRecord`` on the first problem list
* ``compute_rec_id(record)``          run-scoped observation id (spec §4)
* ``replay_key(record)``              run-scoped replay key (spec §5)
* ``body_bytes(record)``              stored response bytes or ``None``
* ``validate(record, tier='committed')``  additionally the committed-tier rules (spec §8)
* ``sk_llm1_run_scope(row)``        v1 ``source.run_id`` of an sk-llm/1 row (spec §5)
* ``convert_sk_llm1(row, origin, input_class=None)``  sk-llm/1 row -> v1 record
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 'seocho.llm_exchange.v1'
REDACTION_POLICY = 'rec-redact/1'
SPEC_PATH = 'rust/crates/seocho-llm-conformance/spec/llm-exchange-v1.md'
SCHEMA_PATH = 'rust/crates/seocho-llm-conformance/spec/llm-exchange-v1.schema.json'

RESPONSE_HEADER_ALLOWLIST = ('content-type', 'x-request-id')
BODY_FIDELITY = ('raw', 'reserialized')
REPLAY_CLASS = ('full', 'carrier_intact', 'none')
STEP_KIND = ('initial', 'retry', 'reprompt', 'downgrade', 'fallback', 'sdk_retry')
SOURCE_KIND = ('canary', 'sweep', 'onboard', 'prod-shadow', 'import:sk-llm/1', 'import:finder-v1',
               'import:room-diagnostics', 'synthetic', 'synthetic-derived')
SERVED_FROM = ('live', 'scripted', 'replay', 'cache')
INPUT_CLASS = ('public', 'synthetic', 'private')
TRANSPORT_ERROR_CLASS = ('timeout', 'transport_error')
REQUEST_BODY_SCOPE = ('full', 'subset', 'none')
REQ_HASH_SCHEME = ('sk-llm/1', 'llm_exchange.v1')

# rec-redact/1 ``redaction.applied`` vocabulary.  Every ``body_*`` token means the stored
# response bytes differ from what the client received, which forces replay_class none.
REDACTION_TOKENS = (
    'auth_header_dropped',          # request Authorization header not stored (always true live)
    'headers_filtered',             # response headers outside the allowlist dropped
    'request_messages_digested',    # request.messages replaced by request.messages_sha256
    'body_secret_scrubbed',         # Bearer / sk- regex replaced inside the body bytes
    'body_sensitive_key_redacted',  # a SENSITIVE key's value replaced in the parsed body
    'body_billing_redacted',        # 402 billing_portal_url / balance_units -> "[redacted]"
    'body_truncated',               # body cut by the recorder (sk-llm/1 unparsed_body[:4000])
)
# JSON pointers (into the parsed response body) that replay_class carrier_intact may replace.
CARRIER_INTACT_POINTERS = (
    re.compile(r'^/choices/\d+/message/reasoning$'),
    re.compile(r'^/choices/\d+/message/reasoning_content$'),
    re.compile(r'^/usage/completion_tokens_details/reasoning_tokens$'),
    re.compile(r'^/error_model_output$'),
)
LEGACY_UNPARSED_LIMIT = 4000  # sk-llm/1: {"unparsed_body": raw.decode("utf-8", "replace")[:4000]}

# rec-redact/1 patterns, kept identical to the sk-llm/1 recorder that produced the imported
# rows (that recording harness is not part of this repository).
SENSITIVE = re.compile(
    r"^(authorization|proxy-authorization|x-api-key|api[_-]?key|.*password.*|.*secret.*|access[_-]?token|refresh[_-]?token|bearer|cookie|set-cookie)$",
    re.I,
)
BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}")
SK_RE = re.compile(r"\bsk-[A-Za-z0-9]{16,}")
SCRUB_MARKS = ('Bearer [redacted]', 'sk-[redacted]')

# 402 billing fields (rec-redact/1 body_billing_redacted), matched on the DECODED member name at
# any depth (a provider may escape characters of the key, e.g. "billing_\\u0070ortal_url").
BILLING_FIELDS = ('billing_portal_url', 'balance_units')
REDACTED = '[redacted]'
REDACTED_JSON = '"[redacted]"'
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_KEY_SEP = re.compile(r'\s*:\s*')
_JSON_DECODER = json.JSONDecoder()

SHA_TAG = re.compile(r'^sha256:[0-9a-f]{64}$')
# rec_id material joins with "||" (spec §4): identity strings must not contain "|" and
# must not be empty, so the join is injective.
ID_PART = re.compile(r'^[^|]+$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')

MARA_ENDPOINT = {'provider': 'mara', 'host': 'api.cloud.mara.com', 'path': '/v1/chat/completions',
                 'catalog_snapshot': None}


class InvalidRecord(ValueError):
    def __init__(self, errors: List[str]):
        super().__init__('; '.join(errors))
        self.errors = errors


# --------------------------------------------------------------------------- hashing

def sha256_tag(data: bytes) -> str:
    return 'sha256:' + hashlib.sha256(data).hexdigest()


# sk-llm/1 recorder HASH_FIELDS + the three fields §7.1 adds (spec §7).  When none of the three is
# present in a body, the v1 hash equals the sk-llm/1 hash byte for byte.
REQ_HASH_FIELDS_SK = ('model', 'messages', 'response_format', 'tools', 'tool_choice', 'max_tokens', 'temperature')
REQ_HASH_FIELDS_V1 = REQ_HASH_FIELDS_SK + ('extra_body', 'chat_template_kwargs', 'reasoning_effort')
# sk-llm/1 recorder SCRUBBERS: applied to the hash input only, never to stored bodies.
HASH_SCRUBBERS = (
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"), '<ts>'),
    (re.compile(r"(\\?\"_writer_ts\\?\"\s*:\s*)[0-9.eE+-]+"), r'\1<wts>'),
)


def compute_req_hash(body: dict, scheme: str = 'llm_exchange.v1') -> str:
    fields = REQ_HASH_FIELDS_V1 if scheme == 'llm_exchange.v1' else REQ_HASH_FIELDS_SK
    material = {k: body.get(k) for k in fields if k in body}
    text = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    for rx, repl in HASH_SCRUBBERS:
        text = rx.sub(repl, text)
    return sha256_tag(text.encode('utf-8'))


def rec_id_material(run_id: Any, logical_call_id: Any, attempt: Any, body_sha256: Any,
                    req_hash: Any = None, occurrence: Any = None, agent: Any = None) -> bytes:
    """Spec §4: ``run_id||logical_call_id||attempt||body_sha256``, null -> "", UTF-8.

    When ``logical_call_id`` is null (imports only) the §5 fallback observation identity is
    appended: ``run_id||""||attempt||body_sha256||req_hash||occurrence||agent``, so ``rec_id`` is
    unique whenever the fallback replay key is.  ``agent`` is last because it is the only
    component that may contain ``|``; every earlier one cannot, so the join stays injective.

    Byte-identical to the raw-generation sk-llm/1 recorder (``RRTransport``) for every row it writes
    with a ``logical_call_id``: there an absent key is ``""`` and ``body_sha256`` keeps its
    ``sha256:`` prefix."""
    fields = [run_id, logical_call_id, attempt, body_sha256]
    if logical_call_id is None:
        fields += [req_hash, occurrence, agent]
    parts = ['' if v is None else str(v) for v in fields]
    return '||'.join(parts).encode('utf-8')


def compute_rec_id(record: dict) -> str:
    src = record.get('source') or {}
    chain = record.get('chain') or {}
    req = record.get('request') or {}
    resp = record.get('response') or {}
    return sha256_tag(rec_id_material(src.get('run_id'), chain.get('logical_call_id'),
                                      chain.get('attempt'), resp.get('body_sha256'),
                                      req.get('req_hash'), req.get('occurrence'), chain.get('agent')))


def sk_llm1_run_scope(row: dict) -> str:
    """Spec §5: the v1 ``source.run_id`` of an sk-llm/1 row.

    sk-llm/1 ``run_id`` spans several arms and sessions (iso/shared/single, coordinator,
    preflight) whose ``logical_call_id`` counters restart per agent session, so the replay
    scope is ``run_id/arm/session_id`` (null or empty parts omitted).  The raw-generation writer
    (``run_scope``) evaluates the identical expression for its ``rec_id``."""
    return '/'.join(str(row[k]) for k in ('run_id', 'arm', 'session_id') if row.get(k) not in (None, ''))


def replay_key(record: dict) -> Tuple:
    """Spec §5.  ``("call", run_id, logical_call_id, attempt)`` when the record has a
    logical call id, else the import fallback ``("occ", run_id, agent, req_hash, occurrence)``."""
    src, chain, req = record['source'], record['chain'], record['request']
    if chain.get('logical_call_id') is not None:
        return ('call', src['run_id'], chain['logical_call_id'], chain['attempt'])
    return ('occ', src['run_id'], chain.get('agent'), req.get('req_hash'), req.get('occurrence'))


def body_store_path(body_sha256: str) -> str:
    """Spec §6: content-addressed body path relative to the corpus root."""
    hexd = body_sha256.split(':', 1)[1]
    return f'bodies/{hexd[:2]}/{hexd}'


def body_bytes(record: dict) -> Optional[bytes]:
    resp = record.get('response') or {}
    if resp.get('body_utf8') is not None:
        return resp['body_utf8'].encode('utf-8')
    if resp.get('body_b64') is not None:
        return base64.b64decode(resp['body_b64'], validate=True)
    return None


def encode_body(raw: bytes) -> Dict[str, Optional[str]]:
    try:
        return {'body_utf8': raw.decode('utf-8'), 'body_b64': None}
    except UnicodeDecodeError:
        return {'body_utf8': None, 'body_b64': base64.b64encode(raw).decode('ascii')}


# --------------------------------------------------------------------------- validation

_TOP = {'schema_version': True, 'rec_id': True, 'captured_at': True, 'source': True, 'endpoint': True,
        'model': True, 'case': True, 'chain': True, 'request': True, 'response': True, 'timing': True,
        'usage': True, 'live_outcome': True, 'oracle_outcome': True, 'redaction': True}
_SOURCE = {'kind': True, 'run_id': True, 'git_sha': True, 'llm_io_version': True, 'wire': True,
           'served_from': True, 'origin': True}
_ORIGIN = {'format': True, 'path': True, 'file_sha256': True, 'line': False, 'source_rec_id': False,
           'source_run_id': False, 'arm': False, 'session_id': False, 'role': False}
_ENDPOINT = {'provider': True, 'host': True, 'path': True, 'catalog_snapshot': True}
_CASE = {'case_id': True, 'task': True, 'input_class': True, 'contract_sha256': True,
         'wire_dialect': True, 'profile_sha256': True, 'enforcement': True}
_CHAIN = {'logical_call_id': True, 'attempt': True, 'next_step_of': True, 'step_kind': True, 'agent': True}
_REQUEST = {'body': True, 'body_scope': True, 'messages_sha256': True, 'req_hash': True,
            'req_hash_scheme': True, 'occurrence': True}
_RESPONSE = {'http_status': True, 'headers': True, 'body_fidelity': True, 'body_utf8': True,
             'body_b64': True, 'body_sha256': True, 'truncated': True, 'transport_error': True}
_TRANSPORT = {'class': True, 'detail': True}
_TIMING = {'latency_ms': True, 'start_ns': True}
_OUTCOME = {'verdict': True, 'codes': True, 'fingerprint': True}
_REDACTION = {'policy': True, 'applied': True, 'replay_class': True, 'replayable': True}


class _V:
    def __init__(self):
        self.errors: List[str] = []

    def err(self, path: str, msg: str) -> None:
        self.errors.append(f'{path}: {msg}')

    def obj(self, value: Any, path: str, fields: Dict[str, bool], nullable: bool = False) -> bool:
        """Object with exactly the declared keys: required (True) must exist, nothing extra."""
        if value is None and nullable:
            return False
        if not isinstance(value, dict):
            self.err(path, 'must be an object' + (' or null' if nullable else ''))
            return False
        for k, required in fields.items():
            if required and k not in value:
                self.err(path, f'missing required field {k!r}')
        for k in value:
            if k not in fields:
                self.err(path, f'unknown field {k!r}')
        return True

    def typ(self, value: Any, path: str, kinds: tuple, nullable: bool = False) -> bool:
        if value is None:
            if not nullable:
                self.err(path, 'must not be null')
            return False
        ok = isinstance(value, kinds) and not (isinstance(value, bool) and bool not in kinds)
        if not ok:
            self.err(path, f'must be {"/".join(k.__name__ for k in kinds)}')
        return ok

    def enum(self, value: Any, path: str, allowed: tuple, nullable: bool = False) -> None:
        if value is None and nullable:
            return
        if value not in allowed:
            self.err(path, f'{value!r} not in {list(allowed)}')

    def pattern(self, value: Any, path: str, rx: re.Pattern, nullable: bool = True) -> None:
        if value is None and nullable:
            return
        if not isinstance(value, str) or not rx.match(value):
            self.err(path, f'{value!r} does not match {rx.pattern}')

    def int_min(self, value: Any, path: str, lo: int, nullable: bool = False) -> None:
        if self.typ(value, path, (int,), nullable) and value < lo:
            self.err(path, f'must be >= {lo}')


def validate(record: Any, tier: Optional[str] = None) -> List[str]:
    """Return every structural (schema) and semantic (S1-S13) violation; [] means valid.

    ``tier='committed'`` adds the committed-tier rules of spec §8 (C1-C3)."""
    if tier not in (None, 'committed'):
        raise ValueError(f'unknown tier {tier!r}')
    v = _V()
    if not v.obj(record, '$', _TOP):
        return v.errors
    g = record.get
    if g('schema_version') != SCHEMA_VERSION:
        v.err('$.schema_version', f'must be {SCHEMA_VERSION!r}')
    v.pattern(g('rec_id'), '$.rec_id', SHA_TAG, nullable=False)
    v.typ(g('captured_at'), '$.captured_at', (str,), nullable=True)
    v.typ(g('model'), '$.model', (str,))

    src = g('source')
    if v.obj(src, '$.source', _SOURCE):
        v.enum(src.get('kind'), '$.source.kind', SOURCE_KIND)
        if v.typ(src.get('run_id'), '$.source.run_id', (str,)) and not ID_PART.match(src['run_id']):
            v.err('$.source.run_id', 'must be non-empty and must not contain "|" (spec §4)')
        v.typ(src.get('git_sha'), '$.source.git_sha', (str,), nullable=True)
        v.typ(src.get('llm_io_version'), '$.source.llm_io_version', (str,), nullable=True)
        v.typ(src.get('wire'), '$.source.wire', (bool,))
        v.enum(src.get('served_from'), '$.source.served_from', SERVED_FROM, nullable=True)
        origin = src.get('origin')
        if v.obj(origin, '$.source.origin', _ORIGIN, nullable=True):
            v.typ(origin.get('format'), '$.source.origin.format', (str,))
            v.typ(origin.get('path'), '$.source.origin.path', (str,), nullable=True)
            v.pattern(origin.get('file_sha256'), '$.source.origin.file_sha256', SHA_TAG)
            if 'line' in origin:
                v.int_min(origin['line'], '$.source.origin.line', 1, nullable=True)
            if 'source_rec_id' in origin:
                v.pattern(origin['source_rec_id'], '$.source.origin.source_rec_id', SHA_TAG)
            for k in ('source_run_id', 'arm', 'session_id', 'role'):
                if k in origin:
                    v.typ(origin[k], f'$.source.origin.{k}', (str,), nullable=True)
        # S10: imports name their origin file; a served_from replay/cache row is never a wire observation
        if str(src.get('kind', '')).startswith('import:'):
            if not isinstance(origin, dict):
                v.err('$.source.origin', 'S10: import:* records must carry source.origin')
            elif origin.get('path') is None or origin.get('file_sha256') is None:
                v.err('$.source.origin', 'S10: import:* records must name origin.path and origin.file_sha256')
        if src.get('served_from') in ('replay', 'cache') and src.get('wire') is True:
            v.err('$.source.wire', 'S10: replay/cache-served rows are not wire observations')

    ep = g('endpoint')
    if v.obj(ep, '$.endpoint', _ENDPOINT):
        v.typ(ep.get('provider'), '$.endpoint.provider', (str,))
        v.typ(ep.get('host'), '$.endpoint.host', (str,), nullable=True)
        v.typ(ep.get('path'), '$.endpoint.path', (str,))
        v.pattern(ep.get('catalog_snapshot'), '$.endpoint.catalog_snapshot', SHA_TAG)

    case = g('case')
    if v.obj(case, '$.case', _CASE, nullable=True):
        v.typ(case.get('case_id'), '$.case.case_id', (str,), nullable=True)
        v.typ(case.get('task'), '$.case.task', (str,), nullable=True)
        v.enum(case.get('input_class'), '$.case.input_class', INPUT_CLASS, nullable=True)
        v.pattern(case.get('contract_sha256'), '$.case.contract_sha256', HEX64)
        v.typ(case.get('wire_dialect'), '$.case.wire_dialect', (str,), nullable=True)
        v.pattern(case.get('profile_sha256'), '$.case.profile_sha256', HEX64)
        v.typ(case.get('enforcement'), '$.case.enforcement', (str,), nullable=True)

    chain = g('chain')
    if v.obj(chain, '$.chain', _CHAIN):
        if v.typ(chain.get('logical_call_id'), '$.chain.logical_call_id', (str,), nullable=True) \
                and not ID_PART.match(chain['logical_call_id']):
            v.err('$.chain.logical_call_id', 'must be non-empty and must not contain "|" (spec §4)')
        v.int_min(chain.get('attempt'), '$.chain.attempt', 0)
        v.pattern(chain.get('next_step_of'), '$.chain.next_step_of', SHA_TAG)
        v.enum(chain.get('step_kind'), '$.chain.step_kind', STEP_KIND)
        if v.typ(chain.get('agent'), '$.chain.agent', (str,), nullable=True) and chain['agent'] == '':
            v.err('$.chain.agent', 'must be non-empty or null (spec §4)')
        # S9: attempt 0 is the initial attempt; later attempts are never "initial"
        if chain.get('attempt') == 0 and (chain.get('step_kind') != 'initial' or chain.get('next_step_of')):
            v.err('$.chain', 'S9: attempt 0 must be step_kind initial with next_step_of null')
        if isinstance(chain.get('attempt'), int) and chain['attempt'] > 0 and chain.get('step_kind') == 'initial':
            v.err('$.chain', 'S9: attempt > 0 cannot be step_kind initial')

    req = g('request')
    if v.obj(req, '$.request', _REQUEST):
        v.typ(req.get('body'), '$.request.body', (dict,), nullable=True)
        v.enum(req.get('body_scope'), '$.request.body_scope', REQUEST_BODY_SCOPE)
        v.pattern(req.get('messages_sha256'), '$.request.messages_sha256', HEX64)
        v.pattern(req.get('req_hash'), '$.request.req_hash', SHA_TAG)
        v.enum(req.get('req_hash_scheme'), '$.request.req_hash_scheme', REQ_HASH_SCHEME, nullable=True)
        v.int_min(req.get('occurrence'), '$.request.occurrence', 0, nullable=True)
        if (req.get('body') is None) != (req.get('body_scope') == 'none'):
            v.err('$.request', 'S11: body is null exactly when body_scope is none')
        if (req.get('req_hash') is None) != (req.get('req_hash_scheme') is None):
            v.err('$.request', 'S11: req_hash and req_hash_scheme are both set or both null')
        body = req.get('body')
        if isinstance(body, dict) and any(str(k).lower() in ('authorization', 'api_key', 'api-key') for k in body):
            v.err('$.request.body', 'credential-bearing key in request body')
        if isinstance(body, dict):
            for path in _sensitive_values(body, '$.request.body'):
                v.err(path, 'S13: credential-bearing key with an unredacted value')

    resp = g('response')
    if v.obj(resp, '$.response', _RESPONSE):
        _validate_response(v, resp)

    timing = g('timing')
    if v.obj(timing, '$.timing', _TIMING):
        if v.typ(timing.get('latency_ms'), '$.timing.latency_ms', (int, float), nullable=True) \
                and timing['latency_ms'] < 0:
            v.err('$.timing.latency_ms', 'must be >= 0')
        v.int_min(timing.get('start_ns'), '$.timing.start_ns', 0, nullable=True)
    if v.typ(g('usage'), '$.usage', (dict,), nullable=True):
        for path in _sensitive_values(g('usage'), '$.usage'):
            v.err(path, 'S13: credential-bearing key with an unredacted value')
    for name in ('live_outcome', 'oracle_outcome'):
        out = g(name)
        if v.obj(out, f'$.{name}', _OUTCOME, nullable=True):
            v.typ(out.get('verdict'), f'$.{name}.verdict', (str,))
            if v.typ(out.get('codes'), f'$.{name}.codes', (list,)):
                for i, c in enumerate(out['codes']):
                    v.typ(c, f'$.{name}.codes[{i}]', (str,))
            v.pattern(out.get('fingerprint'), f'$.{name}.fingerprint', SHA_TAG)

    red = g('redaction')
    if v.obj(red, '$.redaction', _REDACTION):
        _validate_redaction(v, red, resp if isinstance(resp, dict) else {})
    _validate_secrets(v, record)
    if tier == 'committed':
        _validate_committed(v, record)

    # S1: rec_id recomputes (only meaningful once the identity fields are well-formed)
    if not v.errors or not any(e.startswith(('$.rec_id', '$.source', '$.chain', '$.response.body'))
                               for e in v.errors):
        try:
            want = compute_rec_id(record)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            v.err('$.rec_id', f'S1: cannot recompute ({exc})')
        else:
            if g('rec_id') != want:
                v.err('$.rec_id', f'S1: {g("rec_id")!r} != recomputed {want!r}')
    return v.errors


def _validate_response(v: _V, resp: dict) -> None:
    status = resp.get('http_status')
    if v.typ(status, '$.response.http_status', (int,), nullable=True) and not 100 <= status <= 599:
        v.err('$.response.http_status', 'must be 100..599')
    headers = resp.get('headers')
    if v.typ(headers, '$.response.headers', (dict,)):
        for k, val in headers.items():
            if k not in RESPONSE_HEADER_ALLOWLIST:  # S6
                v.err('$.response.headers', f'S6: header {k!r} outside allowlist {list(RESPONSE_HEADER_ALLOWLIST)}')
            if not isinstance(val, str):
                v.err(f'$.response.headers.{k}', 'must be string')
    v.enum(resp.get('body_fidelity'), '$.response.body_fidelity', BODY_FIDELITY, nullable=True)
    v.typ(resp.get('body_utf8'), '$.response.body_utf8', (str,), nullable=True)
    v.typ(resp.get('body_b64'), '$.response.body_b64', (str,), nullable=True)
    v.pattern(resp.get('body_sha256'), '$.response.body_sha256', SHA_TAG)
    v.typ(resp.get('truncated'), '$.response.truncated', (bool,))
    te = resp.get('transport_error')
    if v.obj(te, '$.response.transport_error', _TRANSPORT, nullable=True):
        v.enum(te.get('class'), '$.response.transport_error.class', TRANSPORT_ERROR_CLASS)
        v.typ(te.get('detail'), '$.response.transport_error.detail', (str,), nullable=True)

    has_utf8, has_b64 = resp.get('body_utf8') is not None, resp.get('body_b64') is not None
    if has_utf8 and has_b64:  # S2
        v.err('$.response.body', 'S2: body_utf8 and body_b64 are mutually exclusive')
    has_body = has_utf8 or has_b64
    if has_body != (resp.get('body_sha256') is not None):
        v.err('$.response.body_sha256', 'S2: body_sha256 is set exactly when a body is stored')
    if has_body != (resp.get('body_fidelity') is not None):
        v.err('$.response.body_fidelity', 'S2: body_fidelity is set exactly when a body is stored')
    if has_body and resp.get('body_sha256') is not None:  # S3
        try:
            data = (resp['body_utf8'].encode('utf-8') if has_utf8
                    else base64.b64decode(resp['body_b64'], validate=True))
        except (binascii.Error, ValueError, AttributeError) as exc:
            v.err('$.response.body_b64', f'S3: not valid base64 ({exc})')
        else:
            if sha256_tag(data) != resp['body_sha256']:
                v.err('$.response.body_sha256', 'S3: does not match the stored body bytes')
            if has_b64:
                try:
                    data.decode('utf-8')
                    v.err('$.response.body_b64', 'S2: valid UTF-8 bodies must use body_utf8')
                except UnicodeDecodeError:
                    pass
    # S4: transport errors have no HTTP response; an HTTP response has a status
    if te is not None and (status is not None or has_body):
        v.err('$.response', 'S4: transport_error excludes http_status and body')
    if te is None and status is None:
        v.err('$.response', 'S4: http_status null requires transport_error')
    # S5: only re-serialized bodies may be truncated
    if resp.get('truncated') is True and resp.get('body_fidelity') != 'reserialized':
        v.err('$.response.truncated', 'S5: truncated:true requires body_fidelity reserialized')


def _validate_redaction(v: _V, red: dict, resp: dict) -> None:
    if red.get('policy') != REDACTION_POLICY:
        v.err('$.redaction.policy', f'must be {REDACTION_POLICY!r}')
    v.enum(red.get('replay_class'), '$.redaction.replay_class', REPLAY_CLASS)
    v.typ(red.get('replayable'), '$.redaction.replayable', (bool,))
    applied = red.get('applied')
    tokens: List[str] = []
    pointers: List[str] = []
    if v.typ(applied, '$.redaction.applied', (list,)):
        for i, a in enumerate(applied):
            if not isinstance(a, str):
                v.err(f'$.redaction.applied[{i}]', 'must be string')
            elif a.startswith('/'):
                pointers.append(a)
            elif a in REDACTION_TOKENS:
                tokens.append(a)
            else:
                v.err(f'$.redaction.applied[{i}]', f'unknown token {a!r}')
        strings = [a for a in applied if isinstance(a, str)]
        if len(set(strings)) != len(strings):
            v.err('$.redaction.applied', 'duplicate entries')
    rc, replayable = red.get('replay_class'), red.get('replayable')
    body_tokens = [t for t in tokens if t.startswith('body_')]
    # S7: replayable is exactly replay_class != none
    if isinstance(replayable, bool) and rc in REPLAY_CLASS and replayable != (rc != 'none'):
        v.err('$.redaction', f'S7: replayable={replayable} contradicts replay_class={rc!r}')
    # S8: body-altering redaction forces replay_class none; pointers only for carrier_intact/none
    if body_tokens and rc != 'none':
        v.err('$.redaction', f'S8: {body_tokens} require replay_class none')
    if rc == 'full' and pointers:
        v.err('$.redaction', 'S8: replay_class full cannot list replaced JSON pointers')
    if rc == 'carrier_intact':
        if not pointers:
            v.err('$.redaction', 'S8: carrier_intact must list the replaced JSON pointers')
        for p in pointers:
            if not any(rx.match(p) for rx in CARRIER_INTACT_POINTERS):
                v.err('$.redaction', f'S8: pointer {p!r} is a carrier field; class must be none')
    if resp.get('truncated') is True and 'body_truncated' not in tokens:
        v.err('$.redaction', 'S5: truncated:true requires applied body_truncated')
    if 'body_truncated' in tokens and resp.get('truncated') is not True:
        v.err('$.redaction', 'S5: body_truncated requires response.truncated true')
    # S12: no body means nothing to replay
    if resp.get('body_sha256') is None and rc not in (None, 'none') and resp.get('transport_error') is None:
        v.err('$.redaction', 'S12: an HTTP response without stored body cannot be replayable')


class _Obj(list):
    """A parsed JSON object kept as its ``(key, value)`` members in document order.

    Secret and redaction scans parse stored bodies with ``object_pairs_hook=_Obj`` so a duplicate
    key cannot hide an earlier member (``json.loads`` keeps only the last one)."""


_NOT_JSON = object()


def parse_body_members(data: bytes) -> Any:
    """Stored body parsed with every member kept (``_Obj``), or ``_NOT_JSON``.

    Parses the bytes, so ``json`` detects UTF-8 (with or without BOM), UTF-16 and UTF-32."""
    try:
        return json.loads(data, object_pairs_hook=_Obj)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return _NOT_JSON


def _members(value: Any, path: str):
    """Yield ``(path, key, value)`` for every object member at any depth (duplicate keys kept)."""
    if isinstance(value, _Obj):
        items = iter(value)
    elif isinstance(value, dict):
        items = iter(value.items())
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _members(item, f'{path}[{i}]')
        return
    else:
        return
    for k, val in items:
        yield f'{path}.{k}', k, val
        yield from _members(val, f'{path}.{k}')


def _walk_strings(value: Any, path: str):
    """Yield (path, string) for every decoded key and string value in a JSON value, any depth."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, (_Obj, dict)):
        for k, val in (value if isinstance(value, _Obj) else value.items()):
            yield f'{path}.{k}', str(k)
            yield from _walk_strings(val, f'{path}.{k}')
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk_strings(item, f'{path}[{i}]')


EMBEDDED_JSON_DEPTH = 3


def _scan_strings(value: Any, path: str, skip: Tuple[str, ...] = (), depth: int = 0):
    """``_walk_strings`` plus the strings of JSON documents embedded in string values (tool-call
    ``arguments``, stringified error payloads), up to ``EMBEDDED_JSON_DEPTH`` levels, so an escape
    inside an embedded document is decoded too.  ``skip`` paths are yielded but not expanded."""
    for p, s in _walk_strings(value, path):
        yield p, s
        if depth < EMBEDDED_JSON_DEPTH and p not in skip and s.lstrip()[:1] in ('{', '['):
            try:
                inner = json.loads(s, object_pairs_hook=_Obj)
            except (ValueError, RecursionError):
                continue
            yield from _scan_strings(inner, f'{p}<json>', skip, depth + 1)


def _carries_value(val: Any) -> bool:
    """True when a value under a SENSITIVE key could hold credential material: anything except
    ``"[redacted]"``, ``""``, ``null``, a boolean, or an empty array/object (rec-redact/1, S13).
    Numbers, non-empty arrays and non-empty objects count, not only strings."""
    if val is None or isinstance(val, bool):
        return False
    if isinstance(val, str):
        return val not in ('', REDACTED)
    if isinstance(val, (list, dict)):  # _Obj is a list
        return len(val) > 0
    return True  # numbers


def _sensitive_values(value: Any, path: str) -> List[str]:
    """Paths of SENSITIVE keys (decoded names, any depth) whose value may carry a credential."""
    return [p for p, k, val in _members(value, path)
            if SENSITIVE.search(str(k)) and _carries_value(val)]


def body_text(data: bytes) -> str:
    """Stored body as text for scanning: UTF-8, else latin-1 (lossless byte<->char map)."""
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('latin-1')


def body_texts(data: bytes) -> List[str]:
    """Every text view the raw-text scans run on: ``body_text`` plus, when JSON encoding detection
    finds UTF-16/UTF-32, that decoding (a latin-1 view interleaves NULs and hides every token)."""
    views = [body_text(data)]
    enc = json.detect_encoding(data)
    if enc not in ('utf-8', 'utf-8-sig'):
        try:
            views.append(data.decode(enc))
        except UnicodeDecodeError:
            pass
    return views


def _billing_spans(text: str) -> List[Tuple[int, int, Any, bool]]:
    """Every billing member of a JSON (or truncated-JSON) text as ``(start, end, value, decoded)``.

    Walks the string tokens left to right (so text inside a string value is never taken for a key),
    decodes each member name, and for a billing name decodes the value of any JSON type with
    ``raw_decode``; ``decoded`` is False when the value cannot be decoded (truncated body)."""
    out: List[Tuple[int, int, Any, bool]] = []
    pos = 0
    while True:
        m = _JSON_STRING.search(text, pos)
        if m is None:
            return out
        pos = m.end()
        sep = _KEY_SEP.match(text, pos)
        if sep is None:
            continue
        try:
            key = json.loads(m.group())
        except ValueError:
            continue
        if key not in BILLING_FIELDS:
            continue
        start = sep.end()
        try:
            val, end = _JSON_DECODER.raw_decode(text, start)
        except ValueError:
            out.append((start, start, None, False))
            continue
        out.append((start, end, val, True))
        pos = end


def _billing_values(data: bytes) -> List[Tuple[Any, bool]]:
    """``(value, decoded)`` of every billing member of the stored body, from every text view."""
    return [(val, ok) for text in body_texts(data) for _, _, val, ok in _billing_spans(text)]


def body_redaction_marks(data: bytes) -> List[str]:
    """rec-redact/1 body tokens whose footprints are visible in stored bytes (spec S13).

    Raw text views and the decoded members are both checked, so JSON escapes do not hide one."""
    found = set()
    if any(m in text for text in body_texts(data) for m in SCRUB_MARKS):
        found.add('body_secret_scrubbed')
    parsed = parse_body_members(data)
    if parsed is not _NOT_JSON:
        _detect_cleaned(parsed, found)
    if any(ok and val == REDACTED for val, ok in _billing_values(data)):
        found.add('body_billing_redacted')
    return sorted(found)


def _validate_secrets(v: _V, record: dict) -> None:
    """S13: no credential-shaped string anywhere in the record or its stored body, and visible
    scrub footprints in the body are declared in redaction.applied."""
    # body_utf8 is scanned as raw text here and decoded (parsed) below
    for path, s in _scan_strings(record, '$', skip=('$.response.body_utf8',)):
        if SK_RE.search(s) or BEARER_RE.search(s):
            v.err(path, 'S13: credential-shaped string (sk- / Bearer token)')
    resp = record.get('response')
    if not isinstance(resp, dict):
        return
    try:
        data = body_bytes(record)
    except (binascii.Error, ValueError, AttributeError, TypeError):
        return  # S3 already reports undecodable bodies
    if data is None:
        return
    # body_utf8 is already scanned as a record string above; body_b64 and UTF-16/32 views here
    for text in body_texts(data)[(0 if resp.get('body_b64') is not None else 1):]:
        if SK_RE.search(text) or BEARER_RE.search(text):
            v.err('$.response.body', 'S13: credential-shaped string (sk- / Bearer token) in body')
    # JSON escapes (a backslash-u sequence spelling "s" or "B") hide a credential from the raw-text
    # scan: every decoded key and string value of the parsed body, at any depth and including
    # duplicate keys, is scanned with the same patterns.  request.body and usage are stored as
    # parsed objects, so the record walk above already sees them decoded.
    parsed = parse_body_members(data)
    if parsed is not _NOT_JSON:
        for path, s in _scan_strings(parsed, '$.response.body'):
            if SK_RE.search(s) or BEARER_RE.search(s):
                v.err(path, 'S13: credential-shaped string (sk- / Bearer token) in decoded body')
        for path in _sensitive_values(parsed, '$.response.body'):
            v.err(path, 'S13: credential-bearing key with an unredacted value')
    applied = (record.get('redaction') or {}).get('applied')
    applied = applied if isinstance(applied, list) else []
    for token in body_redaction_marks(data):
        if token not in applied:
            v.err('$.redaction.applied', f'S13: body shows {token} footprints but applied lacks {token!r}')


def _validate_committed(v: _V, record: dict) -> None:
    """Spec §8 committed tier (C1-C3)."""
    case = record.get('case') or {}
    req = record.get('request') or {}
    src = record.get('source') or {}
    body = req.get('body')
    if isinstance(body, dict) and 'messages' in body and case.get('input_class') not in ('public', 'synthetic'):
        v.err('$.request.body.messages', 'C1: prompts are committed only for input_class public/synthetic')
    try:
        data = body_bytes(record)
    except (binascii.Error, ValueError, AttributeError, TypeError):
        data = None
    if data is not None and any(not ok or val != REDACTED for val, ok in _billing_values(data)):
        v.err('$.response.body', 'C2: billing_portal_url / balance_units must be redacted')
    applied = (record.get('redaction') or {}).get('applied') or []
    if src.get('kind') not in ('synthetic', 'synthetic-derived'):
        bad = [t for t in applied if t in ('body_secret_scrubbed', 'body_sensitive_key_redacted')]
        if bad:
            v.err('$.redaction.applied', f'C3: {bad} records from real runs stay in the private tier')


def check(record: dict, tier: Optional[str] = None) -> dict:
    errors = validate(record, tier)
    if errors:
        raise InvalidRecord(errors)
    return record


# --------------------------------------------------------------------------- sk-llm/1 conversion

def _iso(start_ns: Any) -> Optional[str]:
    if not isinstance(start_ns, int) or isinstance(start_ns, bool) or start_ns <= 0:
        return None
    dt = datetime.fromtimestamp(start_ns // 1_000_000_000, tz=timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _detect_cleaned(value: Any, found: set) -> None:
    """Find sk-llm/1 recorder ``clean()`` footprints in a parsed (already cleaned) body: a decoded
    SENSITIVE member name with value ``"[redacted]"``, or a decoded key/string with a scrub mark."""
    if any(SENSITIVE.search(str(k)) and val == REDACTED for _, k, val in _members(value, '$')):
        found.add('body_sensitive_key_redacted')
    if any(m in s for _, s in _walk_strings(value, '$') for m in SCRUB_MARKS):
        found.add('body_secret_scrubbed')


def redact_billing(data: bytes) -> Tuple[bytes, bool]:
    """rec-redact/1 ``body_billing_redacted``: replace the JSON value (any type) of every member whose
    decoded name is ``billing_portal_url`` or ``balance_units`` by ``"[redacted]"`` at the byte level
    (key spelling, key order and every other byte kept).  A value that cannot be decoded (truncated
    body) is left as is; C2 then keeps the record out of the committed tier."""
    try:
        text, enc = data.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        text, enc = data.decode('latin-1'), 'latin-1'
    parts, last = [], 0
    for start, end, val, ok in _billing_spans(text):
        if ok and val != REDACTED:
            parts += [text[last:start], REDACTED_JSON]
            last = end
    out = ''.join(parts) + text[last:]
    return out.encode(enc), out != text


def _unparsed_text(body: Any) -> Optional[str]:
    if isinstance(body, dict) and set(body) == {'unparsed_body'} and isinstance(body['unparsed_body'], str):
        return body['unparsed_body']
    return None


def _legacy_body(resp: dict) -> Tuple[Optional[bytes], bool]:
    """(bytes, truncated) for an sk-llm/1 HTTP response row without raw fields."""
    body = resp.get('body')
    if body is None:
        # The pre-raw recorder stored ``None`` for an empty upstream body (``json.loads(raw) if raw
        # else None``) and RRTransport replays such a row as b''; the raw generation stores body_utf8 "".
        return (b'' if resp.get('http_status') is not None else None), False
    text = _unparsed_text(body)
    if text is not None:
        # Non-JSON upstream body: the recorder kept only a decoded prefix.  Store that text
        # (not a JSON wrapper the wire never carried).
        return text.encode('utf-8'), len(text) >= LEGACY_UNPARSED_LIMIT
    # Same bytes RRTransport serves when replaying a pre-raw row: json.dumps(body).encode()
    return json.dumps(body).encode('utf-8'), False


def _request_block(row: dict) -> Dict[str, Any]:
    req = dict(row.get('request') or {})
    digest = req.pop('messages_sha256', None)
    body: Optional[dict] = req or None
    if body is None:
        scope = 'none'
    else:
        # sk-llm/1 keeps only the hashed fields; a messages digest means messages were dropped
        scope = 'subset'
    h = row.get('req_hash')
    return {'body': body, 'body_scope': scope,
            'messages_sha256': digest if isinstance(digest, str) and HEX64.match(digest) else None,
            'req_hash': ('sha256:' + h) if isinstance(h, str) and HEX64.match(h) else None,
            'req_hash_scheme': 'sk-llm/1' if isinstance(h, str) and HEX64.match(h) else None,
            'occurrence': row.get('occurrence')}


def convert_sk_llm1(row: dict, origin: Optional[dict], input_class: Optional[str] = None) -> dict:
    """Upgrade one sk-llm/1 row (legacy parsed-dict rows and raw-generation rows) to v1 (spec §10).

    ``origin`` is ``{"path", "file_sha256", "line"?}`` of the source file; ``path`` and
    ``file_sha256`` are required (S10).  The converter adds ``format``, the row's ``rec_id``
    as ``source_rec_id`` and the sk-llm/1 ``run_id``/``arm``/``session_id``/``role``.
    The result is validated; ``InvalidRecord`` is raised when the row cannot be expressed.
    """
    if row.get('schema') != 'sk-llm/1':
        raise ValueError(f"not an sk-llm/1 row: schema={row.get('schema')!r}")
    if not isinstance(origin, dict) or not origin.get('path') or not origin.get('file_sha256'):
        raise ValueError('convert_sk_llm1 needs origin {"path", "file_sha256"} of the source file (S10)')
    run_scope = sk_llm1_run_scope(row)
    if not run_scope:
        raise ValueError('sk-llm/1 row without run_id')
    resp = row.get('response') or {}
    error_class = resp.get('error_class')
    served_from = row.get('served_from')
    red_src = row.get('redaction')
    raw_row = 'body_fidelity' in resp or 'body_sha256' in resp  # written by the raw-generation recorder or later

    stored: Optional[bytes] = None
    fidelity: Optional[str] = None
    truncated = False
    if raw_row:
        applied = list((red_src or {}).get('applied') or [])
    else:
        applied = ['auth_header_dropped'] if served_from == 'live' else []
    if error_class:
        pass
    elif raw_row:
        if resp.get('body_utf8') is not None:
            stored = resp['body_utf8'].encode('utf-8')
        elif resp.get('body_b64') is not None:
            stored = base64.b64decode(resp['body_b64'])
        elif resp.get('body_sha256') and resp.get('body') is not None:
            # replay/cache row served from a pre-raw source row: RRTransport served (and hashed)
            # json.dumps(body) but copied the source's null body_utf8/body_b64.
            rebuilt = json.dumps(resp['body']).encode('utf-8')
            if sha256_tag(rebuilt) == resp['body_sha256']:
                stored = rebuilt
                text = _unparsed_text(resp['body'])
                truncated = text is not None and len(text) >= LEGACY_UNPARSED_LIMIT
        if stored is not None:
            fidelity = resp.get('body_fidelity') or 'raw'
    else:
        stored, truncated = _legacy_body(resp)
        fidelity = 'reserialized' if stored is not None else None

    if stored is not None:
        stored, _ = redact_billing(stored)  # spec §8; footprint detected below
        body_tokens = body_redaction_marks(stored)
        if truncated:
            body_tokens.append('body_truncated')
        applied += [t for t in sorted(body_tokens) if t not in applied]
    src_headers = resp.get('headers') or {}
    headers = {k: v for k, v in src_headers.items() if k in RESPONSE_HEADER_ALLOWLIST}
    if len(headers) < len(src_headers) and 'headers_filtered' not in applied:
        applied.append('headers_filtered')
    if (row.get('request') or {}).get('messages_sha256') and 'request_messages_digested' not in applied:
        applied.append('request_messages_digested')

    replayable = not any(a.startswith('body_') or a.startswith('/') for a in applied)
    if raw_row and red_src and red_src.get('replay_class') in REPLAY_CLASS:
        replay_class = red_src['replay_class']
        if any(a.startswith('body_') for a in applied) or (not replayable and replay_class == 'full'):
            replay_class = 'none'
    else:
        replay_class = 'full' if replayable else 'none'
    if stored is None and not error_class:
        replay_class = 'none'  # S12: an HTTP response with no stored body cannot be replayed
    replayable = replay_class != 'none'

    transport_error = None
    if error_class:
        detail = resp.get('body', {}).get('error') if isinstance(resp.get('body'), dict) else None
        transport_error = {'class': error_class if error_class in TRANSPORT_ERROR_CLASS else 'transport_error',
                           'detail': detail if isinstance(detail, str) else None}

    attempt = row.get('attempt')
    if not isinstance(attempt, int) or isinstance(attempt, bool):
        attempt = 0
    origin_block: Dict[str, Any] = {'format': 'sk-llm/1', 'path': origin['path'],
                                    'file_sha256': origin['file_sha256']}
    if origin.get('line') is not None:
        origin_block['line'] = origin['line']
    if row.get('rec_id'):
        origin_block['source_rec_id'] = row['rec_id']
    for k, dst in (('run_id', 'source_run_id'), ('arm', 'arm'), ('session_id', 'session_id'), ('role', 'role')):
        origin_block[dst] = row.get(k) if isinstance(row.get(k), str) else None

    record = {
        'schema_version': SCHEMA_VERSION,
        'rec_id': None,
        'captured_at': _iso(row.get('start_ns')),
        'source': {'kind': 'import:sk-llm/1', 'run_id': run_scope, 'git_sha': None,
                   'llm_io_version': None, 'wire': served_from == 'live',
                   'served_from': served_from if served_from in SERVED_FROM else None,
                   'origin': origin_block},
        'endpoint': dict(MARA_ENDPOINT),
        'model': (row.get('request') or {}).get('model') or '',
        'case': {'case_id': None, 'task': row.get('task_hint'), 'input_class': input_class,
                 'contract_sha256': None, 'wire_dialect': None, 'profile_sha256': None,
                 'enforcement': None},
        'chain': {'logical_call_id': row.get('logical_call_id'), 'attempt': attempt,
                  'next_step_of': None, 'step_kind': 'initial' if attempt == 0 else 'retry',
                  'agent': row.get('agent') or None},
        'request': _request_block(row),
        'response': {'http_status': None if error_class else resp.get('http_status'),
                     'headers': headers, 'body_fidelity': fidelity,
                     **(encode_body(stored) if stored is not None else {'body_utf8': None, 'body_b64': None}),
                     'body_sha256': sha256_tag(stored) if stored is not None else None,
                     'truncated': truncated, 'transport_error': transport_error},
        'timing': {'latency_ms': row.get('latency_ms'), 'start_ns': row.get('start_ns')},
        'usage': resp.get('usage') or None,
        'live_outcome': None,
        'oracle_outcome': None,
        'redaction': {'policy': REDACTION_POLICY, 'applied': applied, 'replay_class': replay_class,
                      'replayable': replayable},
    }
    record['rec_id'] = compute_rec_id(record)
    return check(record)


def dumps(record: dict) -> str:
    """Committed fixture serialization: sorted keys, 2-space indent, ASCII, trailing newline."""
    return json.dumps(record, sort_keys=True, indent=2, ensure_ascii=True) + '\n'
