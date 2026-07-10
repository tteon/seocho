from __future__ import annotations

from typing import Any, Dict, List, Optional

from seocho.agent.exchange import AgentExchange
from seocho.tracing import TracingBackend, disable_tracing, enable_tracing


class _Recorder(TracingBackend):
    def __init__(self) -> None:
        self.spans: List[Dict[str, Any]] = []

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        self.spans.append({"name": name, "metadata": metadata or {}})


def test_exchange_traces_references_without_contents() -> None:
    exchange = AgentExchange(
        exchange_id="ex-secret",
        workspace_id="tenant-secret",
        run_id="run-1",
        sender_agent_id="risk-agent",
        recipient_agent_id="execution-agent",
        message_type="risk_assessment",
        memory_refs=("mem-wallet-0xsecret@v3",),
        evidence_refs=("customer-alice-report",),
        causal_token="fdb-secret-token",
        trace_id="upstream-trace",
    )
    recorder = _Recorder()
    try:
        enable_tracing(backend=recorder)
        exchange.emit_trace()
    finally:
        disable_tracing()

    span = recorder.spans[0]
    rendered = repr(span)
    assert span["name"] == "agent.exchange"
    assert span["metadata"]["seocho.agent.memory_ref_count"] == 1
    assert span["metadata"]["seocho.agent.evidence_ref_count"] == 1
    assert "0xsecret" not in rendered
    assert "alice" not in rendered.lower()
    assert "fdb-secret-token" not in rendered


def test_exchange_requires_scope_and_participants() -> None:
    try:
        AgentExchange(
            exchange_id="x",
            workspace_id="",
            run_id="r",
            sender_agent_id="a",
            recipient_agent_id="b",
            message_type="handoff",
        )
    except ValueError as exc:
        assert "workspace_id" in str(exc)
    else:
        raise AssertionError("missing workspace_id must fail")
