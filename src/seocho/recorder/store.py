from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from .blobs import BlobStore


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


class RecorderStore:
    """One writer per DuckDB file; raw events have no mutation API."""

    def __init__(self, directory: Path, *, read_only: bool = False) -> None:
        self.directory = directory
        if not read_only:
            directory.mkdir(parents=True, exist_ok=True)
        self.blobs = BlobStore(directory / 'blobs', read_only=read_only)
        self.lock = threading.RLock()
        self.read_only = read_only
        self.file_lock = None
        if not read_only:
            import fcntl
            self.file_lock = (directory / 'writer.lock').open('a')
            try:
                fcntl.flock(self.file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.file_lock.close()
                raise RuntimeError('Recorder already has a writer for this directory') from None
        try:
            self.db = duckdb.connect(str(directory / 'recorder.duckdb'), read_only=read_only)
            if not read_only:
                self.db.execute('CREATE TABLE IF NOT EXISTS raw_events (event_id VARCHAR PRIMARY KEY, '
                                'call_id VARCHAR, stage VARCHAR, data JSON, UNIQUE(call_id, stage))')
                self.db.execute('CREATE TABLE IF NOT EXISTS agent_events (event_id VARCHAR PRIMARY KEY, data JSON)')
                self.db.execute("CREATE VIEW IF NOT EXISTS interventions AS SELECT event_id, "
                    "json_extract_string(data, '$.run_id') AS derived_run_id, "
                    "json_extract_string(data, '$.base_run_id') AS base_run_id, data "
                    "FROM agent_events WHERE json_extract_string(data, '$.action')='intervention'")
        except BaseException:
            if self.file_lock:
                self.file_lock.close()
            raise

    def append(self, call_id: str, stage: str, data: dict[str, Any]) -> None:
        if self.read_only:
            raise ValueError('Read-only store')
        with self.lock:
            self.db.execute('INSERT INTO raw_events VALUES (?, ?, ?, ?)',
                            [uuid4().hex, call_id, stage, canonical(data).decode()])

    def append_agent_event(self, event: Any) -> None:
        from .events import AgentEvent
        validated = AgentEvent.model_validate(event)
        if self.read_only:
            raise ValueError('Read-only store')
        data = validated.model_dump()
        data['received_at'] = datetime.now(timezone.utc).isoformat()
        body = data.pop('payload')
        if validated.action == 'intervention':
            data['base_run_id'] = body.get('base_run_id')
        data['payload_blob'] = self.blobs.put(canonical(body), validated.domain)
        with self.lock:
            self.db.execute('INSERT INTO agent_events VALUES (?, ?)',
                            [validated.event_id, canonical(data).decode()])

    def agent_events(self, run_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute('SELECT data FROM agent_events').fetchall()
        return [data for (raw,) in rows if (data := json.loads(raw))['run_id'] == run_id]

    def audit_agent_events(self, run_id: str) -> dict[str, Any]:
        events = self.agent_events(run_id)
        decisions = {e['event_id']: e for e in events if e['kind'] == 'decision'}
        seen: set[str] = set()
        issues: list[str] = []
        for event in events:
            try:
                self.blobs.get(event['payload_blob'], event['domain'])
            except (OSError, ValueError):
                issues.append('BLOB_CORRUPTION:' + event['event_id'])
            if event['kind'] != 'realization':
                continue
            decision = decisions.get(event['decision_id'])
            if not decision or any(decision[key] != event[key] for key in
                    ('workspace_id', 'condition_id', 'step_id', 'agent_id', 'action', 'domain', 'parent_step', 'subtask_id')):
                issues.append('INVALID_PAIR:' + event['event_id'])
            elif event['decision_id'] in seen:
                issues.append('DUPLICATE_REALIZATION:' + event['event_id'])
            seen.add(event['decision_id'])
        calls = {c['call_id']: c for c in self.calls(run_id=run_id)}
        covered: set[str] = set()
        steps = {e['step_id'] for e in events}
        for event in events:
            if event.get('parent_step') and event['parent_step'] not in steps:
                issues.append('MISSING_PARENT:' + event['event_id'])
            try:
                payload = json.loads(self.blobs.get(event['payload_blob'], event['domain']))
            except (OSError, ValueError):
                continue
            if event['kind'] != 'realization' or event['action'] == 'run':
                continue
            for call_id in payload.get('call_ids', []):
                call = calls.get(call_id)
                if not call or any(call['start']['tags'].get(key) != event[key]
                                   for key in ('workspace_id', 'step_id', 'agent_id', 'condition_id')):
                    issues.append('CALL_LINK_MISMATCH:' + event['event_id'])
                else:
                    covered.add(call_id)
        issues.extend('UNLINKED_CALL:' + key for key in calls.keys() - covered)
        issues.extend('MISSING_REALIZATION:' + key for key in decisions.keys() - seen)
        return {'run_id': run_id, 'events': len(events), 'issues': issues,
                'valid': bool(decisions) and not issues}

    def calls(self, *, run_id: str | None = None, domain: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute('SELECT call_id, stage, data FROM raw_events').fetchall()
        calls: dict[str, dict[str, Any]] = {}
        for call_id, stage, raw in rows:
            calls.setdefault(call_id, {'call_id': call_id})[stage] = json.loads(raw)
        return [r for r in calls.values() if 'start' in r
                and (run_id is None or r['start']['tags'].get('run_id') == run_id)
                and (domain is None or r['start']['domain'] == domain)]

    def cached(self, request_hash: str, namespace: str, domain: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT data FROM raw_events WHERE stage='finish' "
                "AND json_extract_string(data, '$.cache_eligible')='true' "
                "AND json_extract_string(data, '$.request_hash')=? "
                "AND json_extract_string(data, '$.namespace')=? "
                "AND json_extract_string(data, '$.domain')=? ORDER BY call_id LIMIT 1",
                [request_hash, namespace, domain]).fetchone()
        return json.loads(row[0]) if row else None

    def audit(self, run_id: str, expected_call_ids: list[str] | None = None) -> dict[str, Any]:
        calls = self.calls(run_id=run_id)
        actual = {c['call_id'] for c in calls}
        missing = sorted(set(expected_call_ids or []) - actual)
        unexpected = sorted(actual - set(expected_call_ids)) if expected_call_ids is not None else []
        issues = []
        for c in calls:
            if c['start']['tag_status'] != 'TAGGED':
                issues.append({'call_id': c['call_id'], 'issue': 'UNTAGGED'})
            if c.get('finish', {}).get('state') in ('interrupted', 'cancelled', 'incomplete_stream'):
                issues.append({'call_id': c['call_id'], 'issue': 'INCOMPLETE'})
            if 'finish' not in c:
                issues.append({'call_id': c['call_id'], 'issue': 'INCOMPLETE'})
            try:
                for stage in ('start', 'finish'):
                    record = c.get(stage, {})
                    for key in ('request_blob', 'normalized_request_blob', 'response_blob'):
                        if record.get(key):
                            self.blobs.get(record[key], c['start']['domain'])
                    for component in record.get('message_blobs', []):
                        self.blobs.get(component, c['start']['domain'])
            except (ValueError, OSError):
                issues.append({'call_id': c['call_id'], 'issue': 'BLOB_CORRUPTION'})
        return {'run_id': run_id, 'calls': len(calls), 'issues': issues, 'missing_call_ids': missing,
                'unexpected_call_ids': unexpected, 'expected_manifest_supplied': expected_call_ids is not None,
                'valid': bool(calls) and not issues and not missing and not unexpected,
                'coverage_proven': expected_call_ids is not None and not missing and not unexpected and not issues}

    def export(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.db.execute('COPY raw_events TO ? (FORMAT PARQUET)', [str(path)])
            self.db.execute('COPY agent_events TO ? (FORMAT PARQUET)',
                            [str(path.with_name(path.stem + '-agent-events.parquet'))])

    def close(self) -> None:
        self.db.close()
        if self.file_lock:
            self.file_lock.close()
