from typing import Any, Dict, List, Optional

import requests

from seocho import ApprovedArtifacts, Seocho, SemanticPromptContext
from seocho.client_artifacts import (
    approved_artifacts_from_ontology,
    prompt_context_from_ontology,
)
from seocho.exceptions import SeochoConnectionError, SeochoHTTPError
from seocho.http_transport import RuntimeHttpTransport
from seocho.ontology import NodeDef, Ontology, P


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: Optional[Dict[str, Any]] = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Dict[str, Any]:
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class _FakeSession:
    def __init__(self, responses: List[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def request(self, method: str, url: str, json=None, params=None, timeout=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "params": params,
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError("No fake responses left")
        return self.responses.pop(0)


def _build_client() -> Seocho:
    ontology = Ontology(
        name="contracts",
        package_id="contracts.core",
        nodes={"Company": NodeDef(properties={"name": P(str, unique=True)})},
        relationships={},
    )
    return Seocho(ontology=ontology)


def test_client_artifact_helpers_delegate_to_shared_module() -> None:
    client = _build_client()

    artifacts = approved_artifacts_from_ontology(client)
    prompt_context = prompt_context_from_ontology(client, instructions=["Prefer ontology labels."])

    assert isinstance(artifacts, ApprovedArtifacts)
    assert isinstance(prompt_context, SemanticPromptContext)
    assert prompt_context.instructions[0] == "Prefer ontology labels."


def test_http_transport_wraps_http_errors() -> None:
    transport = RuntimeHttpTransport(
        base_url="http://localhost:8001/",
        session=_FakeSession([_FakeResponse(status_code=400, payload={"detail": "bad request"})]),
        timeout=30.0,
    )

    try:
        transport.request_json("GET", "/graphs")
        raise AssertionError("Expected SeochoHTTPError")
    except SeochoHTTPError as exc:
        assert exc.status_code == 400


def test_http_transport_wraps_connection_errors() -> None:
    class _BrokenSession:
        def request(self, *args, **kwargs):
            raise requests.RequestException("connection refused")

    transport = RuntimeHttpTransport(
        base_url="http://localhost:8001/",
        session=_BrokenSession(),  # type: ignore[arg-type]
        timeout=30.0,
    )

    try:
        transport.request_json("GET", "/graphs")
        raise AssertionError("Expected SeochoConnectionError")
    except SeochoConnectionError as exc:
        assert "Could not reach SEOCHO" in str(exc)
