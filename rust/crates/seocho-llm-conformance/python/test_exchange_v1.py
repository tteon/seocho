"""seocho.llm_exchange.v1: golden fixtures, rec_id, sk-llm/1 conversion, negative cases.

Network-free and stdlib-only.  The JSON Schema cross-check runs when ``jsonschema`` is
importable.

    python3 -m pytest rust/crates/seocho-llm-conformance/python/test_exchange_v1.py
"""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import exchange_v1 as x  # noqa: E402
import make_exchange_fixtures as mk  # noqa: E402

REPO = mk.REPO
SPEC = mk.SPEC_DIR
FIXTURES = sorted((SPEC / 'fixtures').glob('*.json'))

try:
    import jsonschema
    SCHEMA = json.loads((SPEC / 'llm-exchange-v1.schema.json').read_text())
    SCHEMA_VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
except ImportError:  # stdlib-only environments still run the hand-coded validator
    SCHEMA_VALIDATOR = None



# origin of synthetic rows converted in tests (S10 requires path + file_sha256)
ORIGIN = {'path': 'tests/synthetic.sk-llm-1.jsonl', 'file_sha256': 'sha256:' + 'ab' * 32}


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def fixture(name: str) -> dict:
    return load(SPEC / 'fixtures' / f'{name}.json')


def schema_errors(record) -> list:
    return [e.message for e in SCHEMA_VALIDATOR.iter_errors(record)] if SCHEMA_VALIDATOR else []


class FixtureTests(unittest.TestCase):
    def test_at_least_eight_fixtures_with_required_shapes(self):
        names = {p.stem for p in FIXTURES}
        self.assertGreaterEqual(len(names), 8)
        for prefix in ('01-content-json', '02-fenced-json-prose-preamble', '03-reasoning-only-length',
                       '04-forced-tool-call-string-args', '05-tool-call-object-args', '06-http400-did-not-complete',
                       '07-http402-secret-scrubbed', '08-legacy-502-truncated'):
            self.assertIn(prefix, names)

    def test_every_fixture_validates(self):
        for path in FIXTURES:
            with self.subTest(path.name):
                rec = load(path)
                self.assertEqual(x.validate(rec), [])
                self.assertEqual(x.validate(rec, tier='committed'), [])
                self.assertEqual(schema_errors(rec), [])

    @unittest.skipIf(SCHEMA_VALIDATOR is None, 'jsonschema not installed')
    def test_schema_is_valid_draft_2020_12(self):
        self.assertEqual(SCHEMA['$schema'], 'https://json-schema.org/draft/2020-12/schema')
        jsonschema.Draft202012Validator.check_schema(SCHEMA)

    def test_expected_manifest_matches_recomputation(self):
        exp = load(SPEC / 'expected.json')['fixtures']
        self.assertEqual(sorted(exp), [p.name for p in FIXTURES])
        for path in FIXTURES:
            rec = load(path)
            want = exp[path.name]
            with self.subTest(path.name):
                self.assertEqual(x.compute_rec_id(rec), want['rec_id'])
                self.assertEqual(rec['rec_id'], want['rec_id'])
                body = x.body_bytes(rec)
                self.assertEqual(x.sha256_tag(body) if body is not None else None, want['body_sha256'])
                self.assertEqual(rec['redaction']['replay_class'], want['replay_class'])
                self.assertEqual(rec['redaction']['replayable'], want['replayable'])

    def test_generator_reproduces_committed_files(self):
        for path, text in mk.render().items():
            with self.subTest(str(path.relative_to(REPO))):
                self.assertTrue(path.exists(), 'run make_exchange_fixtures.py')
                self.assertEqual(path.read_text(encoding='utf-8'), text)

    def test_fixture_shapes(self):
        msg = lambda r: json.loads(x.body_bytes(r))['choices'][0]['message']  # noqa: E731
        r3 = fixture('03-reasoning-only-length')
        self.assertEqual(json.loads(x.body_bytes(r3))['choices'][0]['finish_reason'], 'length')
        self.assertEqual(msg(r3)['content'], '')
        self.assertTrue(msg(r3)['reasoning'])
        self.assertIsInstance(msg(fixture('04-forced-tool-call-string-args'))['tool_calls'][0]['function']['arguments'], str)
        self.assertIsInstance(msg(fixture('05-tool-call-object-args'))['tool_calls'][0]['function']['arguments'], dict)
        self.assertIn('```json', msg(fixture('02-fenced-json-prose-preamble'))['content'])
        r6 = fixture('06-http400-did-not-complete')
        self.assertEqual(r6['response']['http_status'], 400)
        self.assertIn(b'did not complete', x.body_bytes(r6))
        self.assertIn(b'max_tokens', x.body_bytes(r6))
        r7 = fixture('07-http402-secret-scrubbed')
        self.assertEqual((r7['response']['http_status'], r7['redaction']['replay_class'], r7['redaction']['replayable']),
                         (402, 'none', False))
        self.assertIn('body_secret_scrubbed', r7['redaction']['applied'])
        r8 = fixture('08-legacy-502-truncated')
        self.assertEqual((r8['response']['body_fidelity'], r8['response']['truncated'], r8['source']['kind']),
                         ('reserialized', True, 'import:sk-llm/1'))
        self.assertEqual(len(x.body_bytes(r8).decode()), x.LEGACY_UNPARSED_LIMIT)
        r13 = fixture('13-non-utf8-body-b64')
        self.assertIsNone(r13['response']['body_utf8'])
        self.assertIsNotNone(r13['response']['body_b64'])
        r10 = fixture('10-http400-json-truncation-downgrade')
        self.assertEqual(r10['chain']['next_step_of'], fixture('09-http400-tool-choice-error-model-output')['rec_id'])
        self.assertEqual(r10['chain']['logical_call_id'], 'G0#09')
        r9 = fixture('09-http400-tool-choice-error-model-output')
        self.assertNotEqual(r10['response']['headers']['x-request-id'], r9['response']['headers']['x-request-id'])

    def test_fixture_identities_are_unique(self):
        recs = [load(p) for p in FIXTURES]
        self.assertEqual(len({r['rec_id'] for r in recs}), len(recs))
        self.assertEqual(len({x.replay_key(r) for r in recs}), len(recs))
        ids = [r['response']['headers'].get('x-request-id') for r in recs]
        ids = [i for i in ids if i]
        self.assertEqual(len(set(ids)), len(ids))

    def test_spec_files_carry_no_credentials(self):
        """Every committed spec file (fixtures, sources, expected.json) is free of sk-/Bearer tokens."""
        for path in sorted(SPEC.rglob('*.json')):
            text = path.read_text(encoding='utf-8')
            with self.subTest(path.name):
                self.assertIsNone(x.SK_RE.search(text))
                self.assertIsNone(x.BEARER_RE.search(text))


class RecIdTests(unittest.TestCase):
    def test_formula(self):
        rec = fixture('01-content-json')
        material = '||'.join([rec['source']['run_id'], rec['chain']['logical_call_id'], str(rec['chain']['attempt']),
                              rec['response']['body_sha256']]).encode()
        self.assertEqual(x.compute_rec_id(rec), x.sha256_tag(material))

    def test_every_component_changes_the_id(self):
        base = fixture('01-content-json')
        for path, value in ((('source', 'run_id'), 'other-run'), (('chain', 'logical_call_id'), 'G0#x'),
                            (('chain', 'attempt'), 1), (('response', 'body_sha256'), 'sha256:' + '0' * 64)):
            rec = copy.deepcopy(base)
            rec[path[0]][path[1]] = value
            self.assertNotEqual(x.compute_rec_id(rec), base['rec_id'], path)

    def test_null_components_are_empty_strings(self):
        rec = fixture('11-raw-timeout-transport-error')
        self.assertIsNone(rec['response']['body_sha256'])
        self.assertEqual(rec['rec_id'], x.sha256_tag(b'golden-raw/shared/shared||S2#3||0||'))
        self.assertEqual(rec['rec_id'], rec['source']['origin']['source_rec_id'])

    def test_replay_key_is_run_scoped(self):
        rec = fixture('01-content-json')
        self.assertEqual(x.replay_key(rec), ('call', 'golden-v1', 'G0#01', 0))
        rec['chain']['logical_call_id'] = None
        self.assertEqual(x.replay_key(rec), ('occ', 'golden-v1', 'G0', rec['request']['req_hash'], 0))
        self.assertEqual(x.body_store_path('sha256:ab' + '0' * 62), 'bodies/ab/ab' + '0' * 62)

    def test_null_logical_call_id_uses_fallback_identity(self):
        """Imports without logical_call_id: rec_id carries the §5 fallback identity, so two
        observations that differ only in request.occurrence get distinct rec_ids."""
        recs = []
        for occ in (0, 1):
            row = mk.legacy_502_row()
            row.update({'logical_call_id': None, 'occurrence': occ})
            recs.append(x.convert_sk_llm1(row, ORIGIN))
        a, b = recs
        self.assertEqual(a['response']['body_sha256'], b['response']['body_sha256'])
        self.assertNotEqual(x.replay_key(a), x.replay_key(b))
        self.assertNotEqual(a['rec_id'], b['rec_id'])
        material = '||'.join(['golden-legacy/iso/iso', '', '1', a['response']['body_sha256'],
                              a['request']['req_hash'], '0', 'A1']).encode()
        self.assertEqual(a['rec_id'], x.sha256_tag(material))
        # repeated transport errors (no body, null body_sha256) stay distinct as well
        recs = []
        for occ in (0, 1):
            row = mk.legacy_502_row()
            row.update({'logical_call_id': None, 'occurrence': occ})
            row['response'].update({'http_status': None, 'error_class': 'timeout', 'body': None})
            recs.append(x.convert_sk_llm1(row, ORIGIN))
        self.assertNotEqual(recs[0]['rec_id'], recs[1]['rec_id'])
        self.assertEqual([x.validate(r) for r in recs], [[], []])

    def test_null_logical_call_id_agent_must_be_non_empty(self):
        row = mk.legacy_502_row()
        row.update({'logical_call_id': None, 'agent': ''})
        rec = x.convert_sk_llm1(row, ORIGIN)
        self.assertIsNone(rec['chain']['agent'])  # converter normalizes "" to null
        rec['chain']['agent'] = ''
        rec['rec_id'] = x.compute_rec_id(rec)
        self.assertTrue(any(e.startswith('$.chain.agent') for e in x.validate(rec)))
        self.assertNotEqual(schema_errors(rec), []) if SCHEMA_VALIDATOR else None

    def test_sk_llm1_run_scope(self):
        self.assertEqual(x.sk_llm1_run_scope({'run_id': 'r', 'arm': 'iso', 'session_id': 'r:iso:A1'}), 'r/iso/r:iso:A1')
        self.assertEqual(x.sk_llm1_run_scope({'run_id': 'r', 'arm': None, 'session_id': ''}), 'r')

    def test_delimiter_cannot_forge_a_collision(self):
        a = fixture('01-content-json')
        b = copy.deepcopy(a)
        a['source']['run_id'], a['chain']['logical_call_id'] = 'runA||c1', 'x'
        b['source']['run_id'], b['chain']['logical_call_id'] = 'runA', 'c1||x'
        self.assertEqual(x.compute_rec_id(a), x.compute_rec_id(b))  # why "|" is forbidden in both
        for rec, field in ((a, '$.source.run_id'), (b, '$.chain.logical_call_id')):
            rec['rec_id'] = x.compute_rec_id(rec)
            self.assertTrue(any(e.startswith(field) for e in x.validate(rec)), field)
            self.assertNotEqual(schema_errors(rec), []) if SCHEMA_VALIDATOR else None
        c = fixture('01-content-json')
        c['chain']['logical_call_id'] = ''
        c['rec_id'] = x.compute_rec_id(c)
        self.assertTrue(any(e.startswith('$.chain.logical_call_id') for e in x.validate(c)))


class ConvertTests(unittest.TestCase):
    def test_sources_convert_to_committed_fixtures(self):
        for src_name, fx_name in (('08-legacy-502-truncated.sk-llm-1.json', '08-legacy-502-truncated'),
                                  ('11-raw-timeout-transport-error.sk-llm-1.json', '11-raw-timeout-transport-error')):
            src_path = SPEC / 'sources' / src_name
            origin = {'path': str(src_path.relative_to(REPO)), 'file_sha256': x.sha256_tag(src_path.read_bytes())}
            got = x.convert_sk_llm1(load(src_path), origin=origin, input_class='synthetic')
            self.assertEqual(got, fixture(fx_name))

    def test_legacy_truncation_threshold(self):
        row = mk.legacy_502_row()
        rec = x.convert_sk_llm1(row, ORIGIN)
        self.assertTrue(rec['response']['truncated'])
        self.assertEqual(rec['redaction']['replay_class'], 'none')
        row['response']['body'] = {'unparsed_body': 'Bad Gateway'}
        rec = x.convert_sk_llm1(row, ORIGIN)
        self.assertFalse(rec['response']['truncated'])
        self.assertEqual(x.body_bytes(rec), b'Bad Gateway')
        self.assertEqual(rec['response']['body_fidelity'], 'reserialized')

    def test_legacy_transport_error_rows(self):
        row = mk.legacy_502_row()
        row['response'].update({'http_status': None, 'error_class': 'transport_error',
                                'body': {'error': 'ConnectError'}})
        rec = x.convert_sk_llm1(row, ORIGIN)
        resp = rec['response']
        self.assertEqual(resp['transport_error'], {'class': 'transport_error', 'detail': 'ConnectError'})
        self.assertEqual((resp['http_status'], resp['body_sha256'], resp['body_fidelity']), (None, None, None))
        row['response'].update({'error_class': 'timeout', 'body': None})
        rec = x.convert_sk_llm1(row, ORIGIN)
        self.assertEqual(rec['response']['transport_error'], {'class': 'timeout', 'detail': None})
        self.assertEqual(rec['rec_id'], x.sha256_tag(b'golden-legacy/iso/iso||A1#7||1||'))

    def test_legacy_cleaned_bodies_are_not_replayable(self):
        row = mk.legacy_502_row()
        row['response'].update({'http_status': 401, 'body': {'error': {'message': 'bad key sk-[redacted]'}}})
        rec = x.convert_sk_llm1(row, ORIGIN)
        self.assertIn('body_secret_scrubbed', rec['redaction']['applied'])
        self.assertEqual((rec['redaction']['replay_class'], rec['redaction']['replayable']), ('none', False))
        row['response']['body'] = {'error': {'api_key': '[redacted]'}}
        rec = x.convert_sk_llm1(row, ORIGIN)
        self.assertIn('body_sensitive_key_redacted', rec['redaction']['applied'])

    def test_legacy_empty_body_matches_raw_generation(self):
        row = mk.legacy_502_row()
        row['response'].update({'http_status': 200, 'body': None})
        rec = x.convert_sk_llm1(row, ORIGIN)
        # RRTransport replays this row as b''; the raw-generation recorder stores body_utf8 "" for the same wire bytes
        self.assertEqual(x.body_bytes(rec), b'')
        self.assertEqual(rec['response']['body_sha256'], x.sha256_tag(b''))
        self.assertEqual(rec['redaction']['replay_class'], 'full')

    def test_billing_fields_are_redacted(self):
        row = mk.legacy_502_row()
        row['response'].update({'http_status': 402, 'body': {'error': {
            'balance_units': 0, 'billing_portal_url': 'https://billing.invalid/p', 'code': 'PAYMENT_METHOD_REQUIRED'}}})
        rec = x.convert_sk_llm1(row, ORIGIN)
        body = json.loads(x.body_bytes(rec))
        self.assertEqual((body['error']['balance_units'], body['error']['billing_portal_url']),
                         ('[redacted]', '[redacted]'))
        self.assertIn('body_billing_redacted', rec['redaction']['applied'])
        self.assertEqual((rec['redaction']['replay_class'], rec['redaction']['replayable']), ('none', False))
        self.assertEqual(x.validate(rec, tier='committed'), [])

    def test_headers_outside_allowlist_are_recorded_as_filtered(self):
        row = mk.legacy_502_row()
        row['response']['headers'] = {'content-type': 'text/html', 'set-cookie': 's=1'}
        rec = x.convert_sk_llm1(row, ORIGIN)
        self.assertEqual(rec['response']['headers'], {'content-type': 'text/html'})
        self.assertIn('headers_filtered', rec['redaction']['applied'])

    def test_origin_is_required(self):
        for origin in (None, {'path': 'a.jsonl', 'file_sha256': None}, {'path': None, 'file_sha256': ORIGIN['file_sha256']}):
            with self.assertRaises(ValueError):
                x.convert_sk_llm1(mk.legacy_502_row(), origin)

    def test_rejects_other_schemas(self):
        with self.assertRaises(ValueError):
            x.convert_sk_llm1({'schema': 'finder-v1'}, ORIGIN)


class SecretScanTests(unittest.TestCase):
    """S13: credential-shaped strings anywhere in a record are rejected (synthetic values)."""
    FAKE_SK = 'sk-' + 'Q7w' * 10
    FAKE_BEARER = 'Bearer ' + 'abcDEF123456' * 2

    def rejected(self, rec, needle='S13'):
        rec['rec_id'] = x.compute_rec_id(rec)
        errors = x.validate(rec)
        self.assertTrue(any(needle in e for e in errors), errors)

    def with_body(self, text):
        rec = fixture('01-content-json')
        rec['response']['body_utf8'] = text
        rec['response']['body_sha256'] = x.sha256_tag(text.encode())
        return rec

    def test_secret_in_replayable_body(self):
        self.rejected(self.with_body('{"note": "' + self.FAKE_SK + '"}'))
        self.rejected(self.with_body('{"note": "' + self.FAKE_BEARER + '"}'))

    def test_secret_hidden_by_json_unicode_escapes(self):
        # JSON escape u0073 decodes to "s", u0042 to "B": the raw text holds no sk-/Bearer match
        sk = '{"message":"\\u0073k-QQQQQQQQQQQQQQQQQQQQQQQQ"}'
        bearer = '{"message":"\\u0042earer QQQQQQQQQQQQQQQQQQQQQQQQ"}'
        key = '{"\\u0073k-QQQQQQQQQQQQQQQQQQQQQQQQ":1}'
        nested = '{"error":{"details":[{"note":"\\u0073k-QQQQQQQQQQQQQQQQQQQQQQQQ"}]}}'
        for text in (sk, bearer, key, nested):
            with self.subTest(text):
                self.assertIsNone(x.SK_RE.search(text))
                self.assertIsNone(x.BEARER_RE.search(text))
                self.rejected(self.with_body(text), 'decoded body')
        self.assertEqual(json.loads(sk), {'message': 'sk' + '-' + 'Q' * 24})

    def test_secret_in_b64_body(self):
        rec = fixture('13-non-utf8-body-b64')
        raw = b'\xff ' + self.FAKE_SK.encode()
        rec['response']['body_b64'] = base64.b64encode(raw).decode()
        rec['response']['body_sha256'] = x.sha256_tag(raw)
        self.rejected(rec)

    def test_secret_in_request_messages_usage_headers_detail(self):
        rec = fixture('01-content-json')
        rec['request']['body']['messages'][1]['content'] += ' ' + self.FAKE_SK
        self.rejected(rec)
        rec = fixture('01-content-json')
        rec['usage']['note'] = self.FAKE_BEARER
        self.rejected(rec)
        rec = fixture('01-content-json')
        rec['response']['headers']['x-request-id'] = self.FAKE_SK
        self.rejected(rec)
        rec = fixture('11-raw-timeout-transport-error')
        rec['response']['transport_error']['detail'] = self.FAKE_BEARER
        self.rejected(rec)

    def test_nested_credential_key_in_request(self):
        rec = fixture('01-content-json')
        rec['request']['body']['extra_body'] = {'api_key': 'not-a-real-key-value'}
        self.rejected(rec)
        rec['request']['body']['extra_body'] = {'api_key': '[redacted]'}
        rec['rec_id'] = x.compute_rec_id(rec)
        self.assertEqual(x.validate(rec), [])

    def test_scrub_marks_require_token(self):
        rec = self.with_body('{"error": "bad key sk-[redacted]"}')
        self.rejected(rec, 'body_secret_scrubbed')
        rec = self.with_body('{"error": {"api_key": "[redacted]"}}')
        self.rejected(rec, 'body_sensitive_key_redacted')
        rec = self.with_body('{"error": {"api_key": "live-looking-value"}}')
        self.rejected(rec)

    def test_committed_tier(self):
        rec = fixture('01-content-json')
        rec['case']['input_class'] = 'private'
        self.assertEqual(x.validate(rec), [])
        self.assertTrue(any('C1' in e for e in x.validate(rec, tier='committed')))
        rec = fixture('07-http402-secret-scrubbed')
        text = rec['response']['body_utf8'].replace('"billing_portal_url":"[redacted]"',
                                                    '"billing_portal_url":"https://billing.invalid/p"')
        rec['response']['body_utf8'] = text
        rec['response']['body_sha256'] = x.sha256_tag(text.encode())
        rec['rec_id'] = x.compute_rec_id(rec)
        self.assertTrue(any('C2' in e for e in x.validate(rec, tier='committed')))
        rec = fixture('07-http402-secret-scrubbed')
        rec['source']['kind'] = 'canary'
        self.assertTrue(any('C3' in e for e in x.validate(rec, tier='committed')))


class NegativeTests(unittest.TestCase):
    def assert_rejected(self, rec, needle, schema_too=True):
        errors = x.validate(rec)
        self.assertTrue(any(needle in e for e in errors), errors)
        if schema_too and SCHEMA_VALIDATOR is not None:
            self.assertNotEqual(schema_errors(rec), [], 'JSON Schema accepted an invalid record')
        with self.assertRaises(x.InvalidRecord):
            x.check(rec)

    def test_bad_fidelity(self):
        rec = fixture('01-content-json')
        rec['response']['body_fidelity'] = 'parsed'
        self.assert_rejected(rec, 'body_fidelity')

    def test_missing_rec_id(self):
        rec = fixture('01-content-json')
        del rec['rec_id']
        self.assert_rejected(rec, "missing required field 'rec_id'")

    def test_header_outside_allowlist(self):
        rec = fixture('01-content-json')
        rec['response']['headers']['set-cookie'] = 'session=1'
        self.assert_rejected(rec, 'S6')

    def test_replayable_with_secret_scrubbed(self):
        rec = fixture('07-http402-secret-scrubbed')
        rec['redaction'].update({'replay_class': 'full', 'replayable': True})
        self.assert_rejected(rec, 'S8')
        rec['redaction']['replay_class'] = 'none'
        self.assert_rejected(rec, 'S7')

    def test_rec_id_mismatch(self):
        rec = fixture('01-content-json')
        rec['chain']['attempt'] = 3
        rec['chain']['step_kind'] = 'retry'
        self.assert_rejected(rec, 'S1', schema_too=False)

    def test_tampered_body(self):
        rec = fixture('02-fenced-json-prose-preamble')
        rec['response']['body_utf8'] = rec['response']['body_utf8'].replace('Acme', 'Acne')
        self.assert_rejected(rec, 'S3', schema_too=False)

    def test_both_bodies(self):
        rec = fixture('13-non-utf8-body-b64')
        rec['response']['body_utf8'] = 'x'
        self.assert_rejected(rec, 'S2')

    def test_truncated_raw(self):
        rec = fixture('08-legacy-502-truncated')
        rec['response']['body_fidelity'] = 'raw'
        self.assert_rejected(rec, 'S5')

    def test_carrier_pointer_cannot_be_carrier_intact(self):
        rec = fixture('12-carrier-intact-reasoning-redacted')
        rec['redaction']['applied'].append('/choices/0/message/content')
        self.assert_rejected(rec, 'S8', schema_too=False)

    def test_transport_error_with_status(self):
        rec = fixture('11-raw-timeout-transport-error')
        rec['response']['http_status'] = 504
        self.assert_rejected(rec, 'S4')

    def test_attempt_zero_must_be_initial(self):
        rec = fixture('01-content-json')
        rec['chain']['step_kind'] = 'retry'
        self.assert_rejected(rec, 'S9')

    def test_import_without_origin_file(self):
        rec = fixture('08-legacy-502-truncated')
        rec['source']['origin']['file_sha256'] = None
        self.assert_rejected(rec, 'S10')
        rec = fixture('08-legacy-502-truncated')
        rec['source']['origin'] = None
        self.assert_rejected(rec, 'S10')

    def test_unknown_redaction_token_and_field(self):
        rec = fixture('01-content-json')
        rec['redaction']['applied'].append('looked_fine')
        self.assert_rejected(rec, 'unknown token')
        rec = fixture('01-content-json')
        rec['response']['body'] = {}
        self.assert_rejected(rec, "unknown field 'body'")


if __name__ == '__main__':
    unittest.main()
