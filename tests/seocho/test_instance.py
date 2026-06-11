"""Tests for worktree-isolated runtime boot (seocho-6q9.3).

Covers the canonical, side-effect-free instance-layout derivation plus the
dry-run / fake-runner command construction in ``serve_local_runtime`` /
``stop_local_runtime`` — so the isolation guarantees (distinct ports, distinct
ephemeral database, ephemeral CREATE/DROP) are asserted without docker.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from seocho.exceptions import SeochoError
from seocho.instance import (
    INSTANCE_PORT_SLOTS,
    derive_instance,
)
from seocho.local import (
    INSTANCE_COMPOSE_FILE,
    SHARED_PROJECT_NAME,
    admin_database_command,
    serve_local_runtime,
    stop_local_runtime,
)
from seocho.runtime_contract import DATABASE_NAME_PATTERN

REPO_ROOT = Path(__file__).resolve().parents[2]
_DB_RE = re.compile(DATABASE_NAME_PATTERN)


# --- pure derivation -------------------------------------------------------


def test_derive_instance_is_deterministic():
    assert derive_instance("alice") == derive_instance("alice")


def test_derive_instance_concrete_layout():
    layout = derive_instance("alice")
    assert layout.project_name == "seocho-alice"
    assert layout.database == "wt522b276a356b"
    assert layout.api_port == 8880
    assert layout.ui_port == 9180


def test_distinct_instances_do_not_collide():
    alice = derive_instance("alice")
    bob = derive_instance("bob")
    assert not alice.collides_with(bob)
    assert alice.api_port != bob.api_port
    assert alice.ui_port != bob.ui_port
    assert alice.database != bob.database
    assert alice.project_name != bob.project_name


def test_same_slot_port_collision_is_detectable():
    # tenant-00001 and tenant-00044 hash to the same port slot; their databases
    # and projects differ but ports would contend — collides_with must catch it.
    a = derive_instance("tenant-00001")
    b = derive_instance("tenant-00044")
    assert a.slot == b.slot
    assert a.api_port == b.api_port
    assert a.database != b.database
    assert a.collides_with(b)


def test_collides_with_is_false_for_same_instance():
    assert not derive_instance("alice").collides_with(derive_instance("alice"))


def test_derived_database_always_satisfies_runtime_contract():
    for raw in ["alice", "BOB", "wt/3", "tenant a", "x", "feature-123", "main"]:
        layout = derive_instance(raw)
        assert _DB_RE.match(layout.database), (raw, layout.database)


def test_ports_stay_within_derived_bands():
    for raw in ["alice", "bob", "wt-1", "tenant-z", "main", "feature-x"]:
        layout = derive_instance(raw)
        assert 0 <= layout.slot < INSTANCE_PORT_SLOTS
        assert 8800 <= layout.api_port < 8800 + INSTANCE_PORT_SLOTS
        assert 9100 <= layout.ui_port < 9100 + INSTANCE_PORT_SLOTS


@pytest.mark.parametrize("bad", ["", "   ", "----", "///"])
def test_derive_instance_rejects_empty_or_unslugifiable(bad):
    with pytest.raises(SeochoError):
        derive_instance(bad)


def test_env_overrides_carry_isolation_knobs():
    env = derive_instance("alice").env_overrides()
    assert env["COMPOSE_PROJECT_NAME"] == "seocho-alice"
    assert env["EXTRACTION_API_PORT"] == "8880"
    assert env["CHAT_INTERFACE_PORT"] == "9180"
    assert env["SEOCHO_DATABASE"] == "wt522b276a356b"
    assert env["NEO4J_DATABASE"] == "wt522b276a356b"


# --- admin (CREATE/DROP DATABASE) command ----------------------------------


def test_admin_database_command_create_and_drop():
    create = admin_database_command("wt522b276a356b", action="create")
    assert create[:5] == ["docker", "compose", "-p", SHARED_PROJECT_NAME, "exec"]
    assert create[-1] == "CREATE DATABASE `wt522b276a356b` IF NOT EXISTS"
    drop = admin_database_command("wt522b276a356b", action="drop")
    assert drop[-1] == "DROP DATABASE `wt522b276a356b` IF EXISTS"


def test_admin_database_command_forwards_credentials_into_container():
    # cypher-shell runs inside the neo4j container; creds must be injected with
    # `exec -e`, not left on the docker-compose client env (the live-boot bug).
    cmd = admin_database_command("wt522b276a356b", action="create", user="neo4j", password="s3cret")
    assert "-e" in cmd
    assert "NEO4J_USERNAME=neo4j" in cmd
    assert "NEO4J_PASSWORD=s3cret" in cmd
    # password must precede the `neo4j` service / cypher-shell tokens
    assert cmd.index("NEO4J_PASSWORD=s3cret") < cmd.index("cypher-shell")
    # no password -> no NEO4J_PASSWORD token (avoid an empty credential)
    nopw = admin_database_command("wt522b276a356b", action="create")
    assert not any(t.startswith("NEO4J_PASSWORD=") for t in nopw)


def test_admin_database_command_rejects_injection():
    with pytest.raises(SeochoError):
        admin_database_command("wt`; DROP DATABASE neo4j; --", action="create")


def test_admin_database_command_rejects_unknown_action():
    with pytest.raises(SeochoError):
        admin_database_command("wt522b276a356b", action="truncate")


# --- serve / stop command construction (dry-run) ---------------------------


def test_serve_dry_run_with_instance_uses_isolated_project_and_ports():
    status = serve_local_runtime(project_dir=str(REPO_ROOT), instance="alice", dry_run=True)
    assert "-p" in status.command
    assert status.command[status.command.index("-p") + 1] == "seocho-alice"
    assert "-f" in status.command
    assert status.command[status.command.index("-f") + 1] == INSTANCE_COMPOSE_FILE
    assert status.api_url == "http://localhost:8880"
    assert status.ui_url == "http://localhost:9180"
    assert status.instance == "alice"
    assert status.database == "wt522b276a356b"


def test_serve_dry_run_without_instance_is_unchanged():
    status = serve_local_runtime(project_dir=str(REPO_ROOT), dry_run=True)
    assert "-p" not in status.command
    assert "-f" not in status.command
    assert status.instance == ""
    assert status.database == ""
    assert status.api_url == "http://localhost:8001"


def test_stop_dry_run_with_instance_targets_only_that_project():
    status = stop_local_runtime(project_dir=str(REPO_ROOT), instance="bob", dry_run=True)
    assert status.command[:4] == ["docker", "compose", "-p", "seocho-bob"]
    assert "down" in status.command
    assert status.database == derive_instance("bob").database


# --- serve / stop with a fake runner (no docker) ---------------------------


class _Recorder:
    """Captures argv passed to subprocess.run and returns a success result."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        self.envs.append(dict(kwargs.get("env") or {}))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _ready_http_get(url, **kwargs):
    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    if url.endswith("/graphs"):
        return _Resp({"graphs": []})
    return _Resp({"status": "ready"})


def test_serve_with_instance_creates_database_before_compose_up():
    recorder = _Recorder()
    status = serve_local_runtime(
        project_dir=str(REPO_ROOT),
        instance="alice",
        runner=recorder,
        http_get=_ready_http_get,
    )
    # First side effect must be the ephemeral CREATE DATABASE on the shared
    # neo4j; only then the per-instance app tier is brought up.
    assert recorder.calls[0][-1] == "CREATE DATABASE `wt522b276a356b` IF NOT EXISTS"
    assert recorder.calls[0][:4] == ["docker", "compose", "-p", SHARED_PROJECT_NAME]
    assert recorder.calls[1][:2] == ["docker", "compose"]
    assert "-p" in recorder.calls[1] and "up" in recorder.calls[1]
    assert status.status == "ready"
    assert status.database == "wt522b276a356b"


def test_stop_with_instance_drops_database_after_compose_down():
    recorder = _Recorder()
    status = stop_local_runtime(
        project_dir=str(REPO_ROOT),
        instance="alice",
        runner=recorder,
    )
    # Compose down first (remove app tier), then drop only this ephemeral DB.
    assert "down" in recorder.calls[0]
    # the down command must carry the instance env so the compose file (which has
    # required vars like SEOCHO_DATABASE) interpolates even for teardown.
    assert recorder.envs[0].get("SEOCHO_DATABASE") == "wt522b276a356b"
    assert recorder.calls[1][-1] == "DROP DATABASE `wt522b276a356b` IF EXISTS"
    assert status.status == "stopped"
