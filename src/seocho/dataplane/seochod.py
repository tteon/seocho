"""Fail-closed Unix-socket client for the Rust DozerDB projection daemon."""

from __future__ import annotations

import json
import socket
import time
import uuid
from typing import Any, Mapping, Sequence


class SeochodProtocolError(RuntimeError):
    """The local Rust projection daemon rejected a protocol request."""


class SeochodProjectionClient:
    """Send approved graph projections to a local ``seochod`` process."""

    def __init__(self, socket_path: str, *, timeout_seconds: float = 30.0) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def project(
        self,
        nodes: Sequence[Mapping[str, Any]],
        relationships: Sequence[Mapping[str, Any]],
        *,
        database: str,
        workspace_id: str,
        source_id: str,
        semantic_receipt: Mapping[str, Any] | None = None,
        admission: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        payload = {
            "op": "project",
            "request_id": request_id,
            "database": database,
            "workspace_id": workspace_id,
            "source_id": source_id,
            "writer_ts": time.time(),
            "nodes": list(nodes),
            "relationships": list(relationships),
            "semantic_receipt": dict(semantic_receipt or {}),
            "admission": dict(admission or {}),
        }
        payload_bytes = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        from ..metrics import get_metrics
        from ..tracing import start_span

        started = time.perf_counter()
        outcome = "ok"
        with start_span(
            "seochod.project",
            input_data={
                "node_count": len(nodes), "relationship_count": len(relationships),
                "payload_bytes": payload_bytes, "has_semantic_receipt": bool(semantic_receipt),
                "has_lifecycle_admission": bool(admission),
            },
            metadata={"seochod.request_id": request_id, "workspace_id": workspace_id},
            tags=["projection", "driver:rust-neo4j"],
        ) as span:
            try:
                response = self._request(payload)
                if not response.get("ok"):
                    outcome = "rejected"
                    detail = response.get("error") or "; ".join(response.get("errors", [])) or "unknown daemon error"
                    raise SeochodProtocolError(f"seochod projection rejected: {detail}")
                span.set_output({
                    "nodes_created": response.get("nodes_created", 0),
                    "relationships_created": response.get("relationships_created", 0),
                    "daemon_duration_ms": response.get("duration_ms"),
                })
                return response
            except Exception:
                if outcome == "ok":
                    outcome = "error"
                raise
            finally:
                elapsed = time.perf_counter() - started
                span.set_metadata({"outcome": outcome, "duration_ms": round(elapsed * 1000, 2)})
                metrics = get_metrics()
                metrics.record("seocho.projection.daemon.request.duration", elapsed, {"outcome": outcome})
                metrics.add("seocho.projection.daemon.request.count", attributes={"outcome": outcome})
                metrics.record("seocho.projection.daemon.payload_bytes", payload_bytes)

    def health(self) -> dict[str, Any]:
        return self._request({"op": "health"})

    def _request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > 2 * 1024 * 1024:
            raise SeochodProtocolError("seochod request exceeds 2 MiB")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout_seconds)
                conn.connect(self.socket_path)
                conn.sendall(encoded)
                raw = conn.makefile("rb").readline(2 * 1024 * 1024 + 1)
        except OSError as exc:
            raise SeochodProtocolError(f"seochod unavailable at {self.socket_path}: {exc}") from exc
        if not raw or len(raw) > 2 * 1024 * 1024:
            raise SeochodProtocolError("seochod returned an invalid response")
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SeochodProtocolError("seochod returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise SeochodProtocolError("seochod returned a non-object response")
        return response
