import os
import sys
import subprocess
from pathlib import Path


ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from seocho.local import serve_local_runtime, stop_local_runtime


class _FakeHTTPResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _write_project_files(project_dir: Path) -> None:
    (project_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (project_dir / "pyproject.toml").write_text("[project]\nname='seocho'\n", encoding="utf-8")


def test_serve_local_runtime_dry_run_uses_fallback_key_for_placeholder_env(tmp_path, monkeypatch):
    _write_project_files(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-your-key-here\nEXTRACTION_API_PORT=9001\nCHAT_INTERFACE_PORT=9901\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = serve_local_runtime(project_dir=str(tmp_path), dry_run=True)

    assert status.status == "dry_run"
    assert status.used_fallback_openai_key is True
    assert status.api_url == "http://localhost:9001"
    assert status.ui_url == "http://localhost:9901"
    assert status.command == ["docker", "compose", "up", "-d"]


def test_serve_local_runtime_waits_for_health(tmp_path, monkeypatch):
    _write_project_files(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    recorded = {}

    def fake_runner(command, cwd, env, check, text, capture_output):
        recorded["command"] = command
        recorded["cwd"] = cwd
        recorded["env_openai_key"] = env.get("OPENAI_API_KEY")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    responses = [
        _FakeHTTPResponse({"status": "ready"}),
        _FakeHTTPResponse({"graphs": [{"graph_id": "kgnormal"}]}),
    ]

    def fake_get(url, timeout):
        return responses.pop(0)

    status = serve_local_runtime(
        project_dir=str(tmp_path),
        runner=fake_runner,
        http_get=fake_get,
        timeout=1.0,
        poll_interval=0.01,
    )

    assert status.status == "ready"
    assert status.runtime_status == "ready"
    assert status.graph_count == 1
    assert recorded["cwd"] == str(tmp_path)
    assert recorded["command"] == ["docker", "compose", "up", "-d"]
    assert recorded["env_openai_key"] == "dummy-key"


def test_stop_local_runtime_dry_run(tmp_path):
    _write_project_files(tmp_path)

    status = stop_local_runtime(project_dir=str(tmp_path), volumes=True, dry_run=True)

    assert status.status == "dry_run"
    assert status.command == ["docker", "compose", "down", "-v"]
