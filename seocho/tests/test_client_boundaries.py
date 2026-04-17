from typing import Any, Dict, List, Optional

import requests

from seocho import ApprovedArtifacts, Seocho, SemanticPromptContext
from seocho.client_artifacts import (
    approved_artifacts_from_ontology,
    prompt_context_from_ontology,
)
from seocho.client_bundle import RuntimeBundleClientHelper
from seocho.client_remote import RemoteClientHelper
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
        self.closed = False

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

    def close(self) -> None:
        self.closed = True


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


def test_client_initializes_remote_helper_and_delegates_request_json() -> None:
    session = _FakeSession([_FakeResponse(payload={"graphs": []})])
    client = Seocho(base_url="http://localhost:8001", session=session)

    payload = client._request_json("GET", "/graphs")

    assert isinstance(client._remote, RemoteClientHelper)
    assert client._transport is client._remote.transport
    assert client.base_url == "http://localhost:8001/"
    assert payload == {"graphs": []}

    client.close()
    assert session.closed is True


def test_client_bundle_helper_exports_runtime_bundle(monkeypatch) -> None:
    class _FakeBundle:
        def __init__(self) -> None:
            self.saved_path: str | None = None

        def save(self, path: str) -> None:
            self.saved_path = path

    fake_bundle = _FakeBundle()
    calls: Dict[str, Any] = {}

    def _fake_build_runtime_bundle(client: Seocho, *, app_name: str | None, default_database: str):
        calls["client"] = client
        calls["app_name"] = app_name
        calls["default_database"] = default_database
        return fake_bundle

    monkeypatch.setattr("seocho.client_bundle.build_runtime_bundle", _fake_build_runtime_bundle)

    client = Seocho(base_url="http://localhost:8001", session=_FakeSession([]))
    bundle = client.export_runtime_bundle(
        path="/tmp/portable.bundle.json",
        app_name="portable-app",
        default_database="news",
    )

    assert isinstance(client._bundle_helper, RuntimeBundleClientHelper)
    assert bundle is fake_bundle
    assert fake_bundle.saved_path == "/tmp/portable.bundle.json"
    assert calls["client"] is client
    assert calls["app_name"] == "portable-app"
    assert calls["default_database"] == "news"

    client.close()


def test_client_bundle_helper_rehydrates_from_bundle(monkeypatch) -> None:
    expected = object()
    calls: Dict[str, Any] = {}

    def _fake_create_client_from_runtime_bundle(bundle_source: str, *, workspace_id: str | None = None):
        calls["bundle_source"] = bundle_source
        calls["workspace_id"] = workspace_id
        return expected

    monkeypatch.setattr(
        "seocho.client_bundle.create_client_from_runtime_bundle",
        _fake_create_client_from_runtime_bundle,
    )

    client = Seocho.from_runtime_bundle("portable.bundle.json", workspace_id="tenant-a")

    assert client is expected
    assert calls["bundle_source"] == "portable.bundle.json"
    assert calls["workspace_id"] == "tenant-a"
