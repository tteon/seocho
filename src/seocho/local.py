from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
from dotenv import dotenv_values

from .exceptions import SeochoConnectionError, SeochoError
from .instance import derive_instance
from .models import JsonSerializable
from .runtime_contract import DATABASE_NAME_PATTERN

DEFAULT_API_PORT = "8001"
DEFAULT_UI_PORT = "8501"
DEFAULT_GRAPH_PORT = "7474"
DEFAULT_BOLT_PORT = "7687"
DEFAULT_FALLBACK_OPENAI_KEY = "dummy-key"
_PLACEHOLDER_OPENAI_KEYS = {"sk-your-key-here", "your-openai-api-key", "changeme"}

# Compose project name of the shared stack started by `make up` (Makefile sets
# COMPOSE_PROJECT_NAME=seocho). Per-instance app tiers reach its neo4j to
# create/drop their ephemeral logical databases.
SHARED_PROJECT_NAME = "seocho"
INSTANCE_COMPOSE_FILE = "docker-compose.instance.yml"

_DATABASE_NAME_RE = re.compile(DATABASE_NAME_PATTERN)


@dataclass(slots=True)
class LocalRuntimeStatus(JsonSerializable):
    action: str
    status: str
    project_dir: str
    command: List[str] = field(default_factory=list)
    api_url: str = ""
    ui_url: str = ""
    graph_url: str = ""
    used_fallback_openai_key: bool = False
    runtime_status: str = ""
    graph_count: int = 0
    details: Dict[str, Any] = field(default_factory=dict)
    instance: str = ""
    database: str = ""


def find_project_dir(start_dir: Optional[str] = None) -> Path:
    candidates = []
    if start_dir:
        candidates.append(Path(start_dir).expanduser().resolve())
    candidates.append(Path.cwd())
    candidates.extend(Path(__file__).resolve().parents)

    checked: set[Path] = set()
    for candidate in candidates:
        for parent in (candidate, *candidate.parents):
            if parent in checked:
                continue
            checked.add(parent)
            if (parent / "docker-compose.yml").exists() and (parent / "pyproject.toml").exists():
                return parent
    raise SeochoError(
        "Could not find a SEOCHO project directory with docker-compose.yml. "
        "Run this command from the repository or pass --project-dir."
    )


def serve_local_runtime(
    *,
    project_dir: Optional[str] = None,
    with_opik: bool = False,
    build: bool = False,
    wait: bool = True,
    timeout: float = 90.0,
    poll_interval: float = 2.0,
    fallback_openai_key: str = DEFAULT_FALLBACK_OPENAI_KEY,
    instance: Optional[str] = None,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    http_get: Callable[..., requests.Response] = requests.get,
) -> LocalRuntimeStatus:
    root = find_project_dir(project_dir)
    settings = _load_runtime_settings(root)
    layout = derive_instance(instance) if instance else None

    command = ["docker", "compose"]
    if layout is not None:
        # Per-instance app tier: its own project + self-contained app-only
        # compose file, reaching the shared neo4j over the shared network.
        command.extend(["-p", layout.project_name, "-f", INSTANCE_COMPOSE_FILE])
    if with_opik:
        command.extend(["--profile", "opik"])
    command.extend(["up", "-d"])
    if build:
        command.append("--build")

    env, used_fallback = _build_runtime_env(settings, fallback_openai_key=fallback_openai_key)
    if layout is not None:
        env.update(layout.env_overrides())
        api_port: str = str(layout.api_port)
        ui_port: str = str(layout.ui_port)
    else:
        api_port = settings["EXTRACTION_API_PORT"]
        ui_port = settings["CHAT_INTERFACE_PORT"]

    api_url = f"http://localhost:{api_port}"
    ui_url = f"http://localhost:{ui_port}"
    # neo4j is shared, so its browser URL is always the shared HTTP port.
    graph_url = f"http://localhost:{settings['NEO4J_HTTP_PORT']}"

    if dry_run:
        return LocalRuntimeStatus(
            action="serve",
            status="dry_run",
            project_dir=str(root),
            command=command,
            api_url=api_url,
            ui_url=ui_url,
            graph_url=graph_url,
            used_fallback_openai_key=used_fallback,
            instance=instance or "",
            database=layout.database if layout else "",
        )

    # An instance's ephemeral logical database must exist on the shared neo4j
    # before its app tier connects to it.
    if layout is not None:
        _admin_database(root, settings, layout.database, action="create", runner=runner)

    _run_compose(command, root, env, runner)
    runtime_status = ""
    graph_count = 0
    details: Dict[str, Any] = {}
    if wait:
        runtime_status, graph_count, details = _wait_for_runtime_ready(
            api_url=api_url,
            timeout=timeout,
            poll_interval=poll_interval,
            http_get=http_get,
        )

    return LocalRuntimeStatus(
        action="serve",
        status="ready" if wait else "started",
        project_dir=str(root),
        command=command,
        api_url=api_url,
        ui_url=ui_url,
        graph_url=graph_url,
        used_fallback_openai_key=used_fallback,
        runtime_status=runtime_status,
        graph_count=graph_count,
        details=details,
        instance=instance or "",
        database=layout.database if layout else "",
    )


def stop_local_runtime(
    *,
    project_dir: Optional[str] = None,
    volumes: bool = False,
    instance: Optional[str] = None,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> LocalRuntimeStatus:
    root = find_project_dir(project_dir)
    settings = _load_runtime_settings(root)
    layout = derive_instance(instance) if instance else None

    command = ["docker", "compose"]
    if layout is not None:
        command.extend(["-p", layout.project_name, "-f", INSTANCE_COMPOSE_FILE])
    command.append("down")
    if volumes:
        command.append("-v")

    if layout is not None:
        api_url = f"http://localhost:{layout.api_port}"
        ui_url = f"http://localhost:{layout.ui_port}"
    else:
        api_url = f"http://localhost:{settings['EXTRACTION_API_PORT']}"
        ui_url = f"http://localhost:{settings['CHAT_INTERFACE_PORT']}"
    graph_url = f"http://localhost:{settings['NEO4J_HTTP_PORT']}"

    if dry_run:
        return LocalRuntimeStatus(
            action="stop",
            status="dry_run",
            project_dir=str(root),
            command=command,
            api_url=api_url,
            ui_url=ui_url,
            graph_url=graph_url,
            instance=instance or "",
            database=layout.database if layout else "",
        )

    _run_compose(command, root, os.environ.copy(), runner)
    # Teardown removes only this instance's resources: its app project (above)
    # and its ephemeral logical database. The shared neo4j is left intact.
    if layout is not None:
        _admin_database(root, settings, layout.database, action="drop", runner=runner)

    return LocalRuntimeStatus(
        action="stop",
        status="stopped",
        project_dir=str(root),
        command=command,
        api_url=api_url,
        ui_url=ui_url,
        graph_url=graph_url,
        instance=instance or "",
        database=layout.database if layout else "",
    )


def _load_runtime_settings(project_dir: Path) -> Dict[str, str]:
    env_path = project_dir / ".env"
    file_settings = {
        key: value
        for key, value in dotenv_values(env_path).items()
        if isinstance(key, str) and isinstance(value, str) and value.strip()
    }
    merged = dict(file_settings)
    for key, value in os.environ.items():
        if value.strip():
            merged[key] = value
    return {
        "OPENAI_API_KEY": merged.get("OPENAI_API_KEY", ""),
        "EXTRACTION_API_PORT": merged.get("EXTRACTION_API_PORT", DEFAULT_API_PORT),
        "CHAT_INTERFACE_PORT": merged.get("CHAT_INTERFACE_PORT", DEFAULT_UI_PORT),
        "NEO4J_HTTP_PORT": merged.get("NEO4J_HTTP_PORT", DEFAULT_GRAPH_PORT),
        "NEO4J_BOLT_PORT": merged.get("NEO4J_BOLT_PORT", DEFAULT_BOLT_PORT),
        "NEO4J_USER": merged.get("NEO4J_USER", "neo4j"),
        "NEO4J_PASSWORD": merged.get("NEO4J_PASSWORD", ""),
    }


def _build_runtime_env(
    settings: Dict[str, str],
    *,
    fallback_openai_key: str,
) -> tuple[Dict[str, str], bool]:
    env = os.environ.copy()
    for key, value in settings.items():
        if value:
            env[key] = value
    used_fallback = False
    if not _has_effective_openai_key(env.get("OPENAI_API_KEY")):
        if not fallback_openai_key.strip():
            raise SeochoError(
                "OPENAI_API_KEY is not set and no fallback key was provided. "
                "Set OPENAI_API_KEY or pass --fallback-openai-key."
            )
        env["OPENAI_API_KEY"] = fallback_openai_key.strip()
        used_fallback = True
    return env, used_fallback


def _run_compose(
    command: List[str],
    project_dir: Path,
    env: Dict[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    try:
        runner(
            command,
            cwd=str(project_dir),
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise SeochoError("docker compose is required for local runtime commands and was not found.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if isinstance(exc.stderr, str) else ""
        stdout = exc.stdout.strip() if isinstance(exc.stdout, str) else ""
        detail = stderr or stdout or str(exc)
        raise SeochoError(f"docker compose failed: {detail}") from exc


def admin_database_command(database: str, *, action: str) -> List[str]:
    """Build the docker/cypher-shell argv that creates or drops ``database``.

    ``database`` is re-validated against the runtime contract before it is
    interpolated into Cypher (defense in depth — derived names already comply).
    """
    if action not in ("create", "drop"):
        raise SeochoError(f"unknown database admin action: {action!r}")
    if not _DATABASE_NAME_RE.match(database):
        raise SeochoError(
            f"refusing to interpolate database name {database!r}: must match "
            f"{DATABASE_NAME_PATTERN!r}"
        )
    if action == "create":
        cypher = f"CREATE DATABASE `{database}` IF NOT EXISTS"
    else:
        cypher = f"DROP DATABASE `{database}` IF EXISTS"
    return [
        "docker",
        "compose",
        "-p",
        SHARED_PROJECT_NAME,
        "exec",
        "-T",
        "neo4j",
        "cypher-shell",
        "-d",
        "system",
        cypher,
    ]


def _admin_database(
    project_dir: Path,
    settings: Dict[str, str],
    database: str,
    *,
    action: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Create/drop an ephemeral logical database on the shared neo4j."""
    command = admin_database_command(database, action=action)
    env = os.environ.copy()
    user = settings.get("NEO4J_USER") or "neo4j"
    password = settings.get("NEO4J_PASSWORD") or ""
    # cypher-shell reads credentials from the environment to keep them off argv.
    env["NEO4J_USERNAME"] = user
    if password:
        env["NEO4J_PASSWORD"] = password
    _run_compose(command, project_dir, env, runner)


def _wait_for_runtime_ready(
    *,
    api_url: str,
    timeout: float,
    poll_interval: float,
    http_get: Callable[..., requests.Response],
) -> tuple[str, int, Dict[str, Any]]:
    deadline = time.monotonic() + max(timeout, poll_interval)
    last_error = "runtime did not report healthy state before timeout"

    while time.monotonic() < deadline:
        try:
            health_response = http_get(f"{api_url}/health/runtime", timeout=min(poll_interval, 5.0))
            graphs_response = http_get(f"{api_url}/graphs", timeout=min(poll_interval, 5.0))
            if health_response.status_code == 200 and graphs_response.status_code == 200:
                health_payload = _response_json(health_response, path="/health/runtime")
                graphs_payload = _response_json(graphs_response, path="/graphs")
                return (
                    str(health_payload.get("status", "")),
                    len(graphs_payload.get("graphs", [])) if isinstance(graphs_payload.get("graphs"), list) else 0,
                    {"health": health_payload, "graphs": graphs_payload},
                )
            last_error = (
                f"health={health_response.status_code} graphs={graphs_response.status_code}"
            )
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(poll_interval)

    raise SeochoConnectionError(
        f"SEOCHO runtime did not become ready at {api_url} within {timeout:.0f}s: {last_error}"
    )


def _response_json(response: requests.Response, *, path: str) -> Dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SeochoConnectionError(f"SEOCHO returned invalid JSON for {path}") from exc
    if not isinstance(payload, dict):
        raise SeochoConnectionError(f"SEOCHO returned unexpected payload for {path}")
    return payload


def _has_effective_openai_key(value: Optional[str]) -> bool:
    if not value:
        return False
    normalized = value.strip()
    return bool(normalized) and normalized not in _PLACEHOLDER_OPENAI_KEYS
