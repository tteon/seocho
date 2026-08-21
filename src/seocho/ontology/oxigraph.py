"""Bounded Unix-socket client for the optional Oxigraph ontology read model."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class OxigraphTermResult:
    found: bool
    term: str
    uri: Optional[str] = None
    label: Optional[str] = None
    definition: Optional[str] = None
    bundle_sha256: Optional[str] = None


class OxigraphReadModelClient:
    """Client for one-request-per-connection newline-delimited JSON protocol."""

    def __init__(self, socket_path: str | Path, *, timeout_seconds: float = 0.25) -> None:
        self.socket_path = str(socket_path)
        self.timeout_seconds = timeout_seconds

    def health(self) -> Dict[str, Any]:
        return self._request({"op": "health"})

    def lookup_term(
        self, term: str, *, workspace_id: str, ontology_context_hash: str
    ) -> OxigraphTermResult:
        payload = self._request(
            {
                "op": "term",
                "term": str(term),
                "workspace_id": str(workspace_id),
                "ontology_context_hash": str(ontology_context_hash),
            }
        )
        return OxigraphTermResult(
            found=bool(payload.get("found", False)), term=str(term),
            uri=_optional_text(payload.get("uri")), label=_optional_text(payload.get("label")),
            definition=_optional_text(payload.get("definition")),
            bundle_sha256=_optional_text(payload.get("bundle_sha256")),
        )

    def _request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > 16_384:
            raise ValueError("Oxigraph read-model request exceeds 16 KiB")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(self.timeout_seconds)
            conn.connect(self.socket_path)
            conn.sendall(encoded)
            response = _read_line(conn)
        try:
            payload = json.loads(response)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Oxigraph read model returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Oxigraph read model returned a non-object response")
        if payload.get("error"):
            raise RuntimeError(f"Oxigraph read model error: {payload['error']}")
        return payload


def _read_line(conn: socket.socket) -> bytes:
    chunks = bytearray()
    while len(chunks) <= 65_536:
        chunk = conn.recv(4_096)
        if not chunk:
            break
        chunks.extend(chunk)
        if b"\n" in chunk:
            return bytes(chunks).split(b"\n", 1)[0]
    raise RuntimeError("Oxigraph read-model response exceeded 64 KiB or was incomplete")


def _optional_text(value: Any) -> Optional[str]:
    return str(value) if value is not None else None
