from __future__ import annotations

import json
import socket
import threading

from seocho.ontology.oxigraph import OxigraphReadModelClient


def test_unix_socket_client_returns_typed_term_result(tmp_path):
    socket_path = str(tmp_path / "oxigraph.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(socket_path)
    listener.listen(1)

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            request = json.loads(connection.recv(4096))
            assert request["op"] == "term"
            connection.sendall(json.dumps({"found": True, "uri": "urn:Person", "label": "Person", "bundle_sha256": "abc"}).encode() + b"\n")
        listener.close()

    thread = threading.Thread(target=serve)
    thread.start()
    result = OxigraphReadModelClient(socket_path).lookup_term(
        "Person", workspace_id="default", ontology_context_hash="hash"
    )
    thread.join()
    assert result.found is True
    assert result.uri == "urn:Person"
    assert result.bundle_sha256 == "abc"
