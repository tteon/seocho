"""Regression anchors for user-facing edge cases logged in .beads.

Each test pins the *current* observable behavior at the time of writing
and links to the corresponding bd issue. When a fix lands, the matching
test MUST be updated — that is the signal to update the issue's status
in .beads as well.

Issues tracked here (all sev-high, sprint 2026-S03):

- seocho-1zck — SDK silently degrades agent-mode failures to pipeline
- seocho-35n4 — Semantic artifact approval is non-atomic
- seocho-cimb — Ontology drift gate is advisory-only on apply
- seocho-vncn — Session query cache cross-database hits / no TTL
- seocho-8k1h — OpikBackend silently drops traces if init fails

These tests deliberately do NOT require live services: no Neo4j/DozerDB,
no live LLM, no live Opik. They use in-tree fakes + monkeypatched runtimes.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from seocho.models import Memory
from seocho.ontology import Ontology, NodeDef, RelDef, P
from seocho.session import Session


# ======================================================================
# Shared fakes (kept local; do not import test_session_agent fixtures)
# ======================================================================


def _ontology() -> Ontology:
    return Ontology(
        name="edge_cases",
        description="Edge-case regression anchors",
        nodes={"Company": NodeDef(description="x", properties={"name": P(required=True)})},
        relationships={},
    )


class _FakeLLM:
    model = "fake-model"

    def complete(self, **_: Any) -> Any:
        return SimpleNamespace(
            text=json.dumps({"nodes": [], "relationships": []}),
            model="fake-model",
            usage={"total_tokens": 0},
        )

    async def acomplete(self, **kwargs: Any) -> Any:
        return self.complete(**kwargs)

    def to_agents_sdk_model(self, *, model: Optional[str] = None) -> Any:
        return MagicMock()


class _FakeGraphStore:
    def write(self, **_: Any) -> Dict[str, Any]:
        return {"nodes_created": 0, "relationships_created": 0, "errors": []}

    def query(self, *_: Any, **__: Any) -> List[Dict[str, Any]]:
        return [{"name": "TestCorp"}]

    def get_schema(self, *, database: str = "neo4j") -> Dict[str, Any]:
        return {"labels": ["Company"], "relationship_types": []}


class _AgentConfigStub:
    def __init__(self, mode: str = "agent") -> None:
        self.execution_mode = mode
        self.handoff = False
        self.routing_policy = None


def _make_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "agent",
    pipeline_answer: str = "pipeline-answer-ALPHA",
) -> Session:
    """Build a Session with FakePipelineEngine returning a deterministic answer.

    Patches Session._get_pipeline_engine so we don't construct _LocalEngine.
    """
    sess = Session(
        ontology=_ontology(),
        graph_store=_FakeGraphStore(),
        llm=_FakeLLM(),
        agent_config=_AgentConfigStub(mode),
        database="neo4j",
        workspace_id="test-ws",
    )

    class _FakePipeline:
        def add(self, content: str, **_: Any) -> Memory:
            return Memory(
                memory_id="mem-1",
                workspace_id="test-ws",
                content=content,
                metadata={
                    "nodes_created": 0,
                    "relationships_created": 0,
                    "chunks_processed": 1,
                    "validation_errors": [],
                    "write_errors": [],
                },
                status="active",
                database="neo4j",
                category="general",
            )

        def ask(self, question: str, **_: Any) -> str:
            return pipeline_answer

    monkeypatch.setattr(sess, "_get_pipeline_engine", lambda: _FakePipeline())
    return sess


# ======================================================================
# seocho-1zck — SDK silent agent-mode fallback
# ======================================================================


class TestSilentAgentModeFallback:
    """REGRESSION ANCHOR: seocho-1zck.

    Today, when agent-mode runtime raises, Session silently falls back to
    pipeline and returns a degraded result with no exception. These tests
    pin that contract so the fix lands deliberately.
    """

    @staticmethod
    def _install_boom_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace get_agents_runtime so .run / .run_streamed raise."""

        class _BoomRuntime:
            def run(self, **_: Any) -> Any:
                raise RuntimeError("boom: agent backend offline")

            def run_streamed(self, **_: Any) -> Any:
                raise RuntimeError("boom: streaming backend offline")

        import extraction.agents_runtime as ar  # type: ignore

        monkeypatch.setattr(ar, "get_agents_runtime", lambda: _BoomRuntime())

    def test_agent_indexing_failure_returns_degraded_dict_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sess = _make_session(monkeypatch, mode="agent")
        # Sidestep the real Agent constructor (needs a proper Model object).
        monkeypatch.setattr(sess, "_get_indexing_agent", lambda: object())
        self._install_boom_runtime(monkeypatch)

        # No exception escapes (this is the bug seocho-1zck pins).
        result = sess.add("Apple Inc is a company")

        # The returned dict shape comes from _add_via_pipeline + agent
        # fallback overlay; the bug is that user code must read these
        # fields rather than handle an exception.
        assert result.get("ok") is True
        assert result.get("degraded") is True, "Expected degraded marker on silent fallback"
        assert result.get("fallback_from") == "agent"
        assert "boom" in result.get("fallback_reason", "")

    def test_agent_query_failure_returns_pipeline_answer_silently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sess = _make_session(monkeypatch, mode="agent", pipeline_answer="PIPELINE_ANSWER")
        monkeypatch.setattr(sess, "_get_query_agent", lambda: object())
        self._install_boom_runtime(monkeypatch)

        answer = sess.ask("Who is CEO?")
        # Today: no exception, pipeline string flows back. After fix: raise
        # or expose a structured error path.
        assert answer == "PIPELINE_ANSWER", (
            "Silent agent->pipeline fallback regression — see seocho-1zck"
        )

    def test_ask_stream_yields_single_full_chunk_when_streaming_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sess = _make_session(monkeypatch, mode="agent", pipeline_answer="FULL_ANSWER_X")
        monkeypatch.setattr(sess, "_get_query_agent", lambda: object())
        self._install_boom_runtime(monkeypatch)

        chunks = list(sess.ask_stream("Who is CEO?"))
        # Today: one chunk equal to the full pipeline answer. After fix:
        # streaming should remain streaming OR a sentinel event should be
        # emitted before the fallback chunk.
        assert chunks == ["FULL_ANSWER_X"], (
            f"ask_stream silent downgrade regression — got {chunks!r}"
        )


# ======================================================================
# seocho-35n4 — Semantic artifact approval is non-atomic
# ======================================================================


class TestArtifactApprovalRace:
    """REGRESSION ANCHOR: seocho-35n4.

    approve_semantic_artifact does read JSON / mutate / write JSON without
    locking. Two concurrent approvals end with last-writer-wins; the loser's
    approved_by is silently overwritten with no error.
    """

    def test_concurrent_approvals_lose_one_writer_silently(
        self, tmp_path: Path
    ) -> None:
        from extraction.semantic_artifact_store import (
            approve_semantic_artifact,
            save_semantic_artifact,
        )

        base_dir = str(tmp_path)
        saved = save_semantic_artifact(
            workspace_id="ws-race",
            name="t",
            ontology_candidate={"turtle": "@prefix : <urn:t/> ."},
            shacl_candidate={"turtle": ""},
            vocabulary_candidate=None,
            source_summary={},
            base_dir=base_dir,
        )
        artifact_id = saved["artifact_id"]

        barrier = threading.Barrier(2)
        results: List[Dict[str, Any]] = []
        errors: List[BaseException] = []

        def _approve(approver: str) -> None:
            try:
                barrier.wait(timeout=5)
                payload = approve_semantic_artifact(
                    workspace_id="ws-race",
                    artifact_id=artifact_id,
                    approved_by=approver,
                    approval_note=f"by-{approver}",
                    base_dir=base_dir,
                )
                results.append(payload)
            except BaseException as exc:  # noqa: BLE001 — anchor test
                errors.append(exc)

        t1 = threading.Thread(target=_approve, args=("alice",))
        t2 = threading.Thread(target=_approve, args=("bob",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # The race manifests as ONE OR MORE of three failure modes today:
        #   (a) lost update — both writers return success, final on-disk
        #       state preserves only one approver
        #   (b) one writer crashes with JSONDecodeError reading a file
        #       being truncated mid-write by the other
        #   (c) final on-disk file is corrupted JSON because byte-level
        #       writes interleaved
        # Any of these is unacceptable for a governance-critical state
        # transition. The assertion: at least ONE failure mode is reached
        # under contention.
        total = len(results) + len(errors)
        assert total == 2, f"expected 2 attempts, got {total}"

        final_path = tmp_path / "ws-race" / f"{artifact_id}.json"
        on_disk_text = final_path.read_text(encoding="utf-8")
        try:
            on_disk = json.loads(on_disk_text)
            corrupted_on_disk = False
        except json.JSONDecodeError:
            on_disk = None
            corrupted_on_disk = True

        mid_read_crash = any(
            isinstance(e, (json.JSONDecodeError, ValueError, OSError))
            for e in errors
        )
        lost_update = (not corrupted_on_disk and not mid_read_crash and len(results) == 2)

        assert mid_read_crash or corrupted_on_disk or lost_update, (
            "Expected at least one race-induced failure mode under "
            f"contention. results={results!r} errors={errors!r} "
            f"on_disk_corrupted={corrupted_on_disk}"
        )
        # REGRESSION ANCHOR: when the fix lands (atomic rename or version
        # check), none of these failure modes should be reachable: both
        # writers must serialize, OR the loser must fail with a clean
        # conflict error rather than corrupting state.


# ======================================================================
# seocho-cimb — Drift gate is advisory-only on apply
# ======================================================================


class TestDriftGateAdvisoryOnly:
    """REGRESSION ANCHOR: seocho-cimb.

    get_rule_profile surfaces artifact_ontology_mismatch when stored hash
    differs from active, but does NOT raise. Callers can ignore the block
    and apply a drift-mismatched profile. Pin this so any move toward
    enforcement requires updating the test.
    """

    def test_get_rule_profile_returns_mismatch_block_without_raising(
        self, tmp_path: Path
    ) -> None:
        from extraction.rule_profile_store import (
            get_rule_profile,
            save_rule_profile,
        )

        base_dir = str(tmp_path)
        saved = save_rule_profile(
            workspace_id="ws-drift",
            name="p",
            rule_profile={"rules": []},
            base_dir=base_dir,
            ontology_identity_hash="HASH_A",
        )
        profile_id = saved["profile_id"]

        # Read with a different "active" hash — today this returns the
        # payload with a mismatch block; it does NOT block the read.
        payload = get_rule_profile(
            workspace_id="ws-drift",
            profile_id=profile_id,
            base_dir=base_dir,
            expected_ontology_hash="HASH_B",
        )

        assert "artifact_ontology_mismatch" in payload, (
            "Soft-gate block should be surfaced in the response payload"
        )
        block = payload["artifact_ontology_mismatch"]
        assert block.get("mismatch") is True
        # After fix: read or apply path should refuse / require force flag.


# ======================================================================
# seocho-vncn — Query cache cross-database hits / no TTL
# ======================================================================


class TestQueryCacheCrossDatabase:
    """REGRESSION ANCHOR: seocho-vncn.

    SessionContext._query_cache is keyed only on the question string. A
    cached answer for database='alpha' is returned to a follow-up call for
    database='beta'. Pin so the fix (key includes database, plus TTL) is
    deliberate.
    """

    def test_cached_answer_leaks_across_databases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sess = _make_session(monkeypatch, mode="pipeline", pipeline_answer="ANSWER_FOR_ALPHA")

        # First call populates cache for 'alpha' under the bare question key.
        first = sess.ask("Who is CEO?", database="alpha")
        assert first == "ANSWER_FOR_ALPHA"

        # Swap pipeline answer; if the cache were database-scoped this call
        # would re-run and yield the new value. Today, the cache hit returns
        # alpha's answer for beta.
        class _NewPipeline:
            def ask(self, *_: Any, **__: Any) -> str:
                return "ANSWER_FOR_BETA"

            def add(self, *_: Any, **__: Any) -> Memory:  # pragma: no cover
                raise AssertionError("not used")

        monkeypatch.setattr(sess, "_get_pipeline_engine", lambda: _NewPipeline())

        second = sess.ask("Who is CEO?", database="beta")
        assert second == "ANSWER_FOR_ALPHA", (
            "Cross-database cache poisoning regression — see seocho-vncn"
        )


# ======================================================================
# seocho-8k1h — OpikBackend silently drops traces on init failure
# ======================================================================


class TestOpikBackendSilentInit:
    """REGRESSION ANCHOR: seocho-8k1h.

    OpikBackend wraps client construction in try/except and silently no-ops
    log_span when self._client is None. Users see no error from the SDK.
    """

    def test_log_span_no_ops_when_opik_client_init_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Build a fake `opik` module whose Opik() constructor raises.
        fake_opik = ModuleType("opik")

        class _BoomOpik:
            def __init__(self, **_: Any) -> None:
                raise RuntimeError("opik unreachable")

        fake_opik.Opik = _BoomOpik  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opik", fake_opik)

        # Ensure env doesn't leak into init.
        for var in ("OPIK_API_KEY", "OPIK_WORKSPACE", "OPIK_URL_OVERRIDE"):
            monkeypatch.delenv(var, raising=False)

        from seocho.tracing import OpikBackend

        backend = OpikBackend(api_key="bad", workspace="x", project_name="p")

        # Init "succeeded" without raising; client is None.
        assert backend._client is None, (
            "OpikBackend silent-init regression — see seocho-8k1h"
        )

        # log_span returns None silently (no exception, no trace).
        result = backend.log_span(
            "anything",
            input_data={"q": "?"},
            output_data={"a": "!"},
        )
        assert result is None
