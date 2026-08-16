"""The three configuration surfaces must agree, and .env must reach the container.

Two separate failures are locked down here, because fixing the first does not
prevent the second.

`.env.example` drifting from what the code reads is a documentation failure: a
reader cannot discover a setting that is not listed. That is what
`check-env-contract.py` measures.

A variable reaching `docker-compose.yml` but not the process inside the container
is a different and quieter failure. Compose reads `.env` for `${VAR}`
substitution and does *not* inject it into containers, so before `env_file:` was
added the service passed 11 variables while the code read 74 -- setting
`MARA_API_KEY` or `SEOCHO_AUTH_SECRET` in `.env` did nothing at all, with no
error anywhere. Deleting one `env_file:` block silently restores that, so the
wiring is asserted rather than trusted.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - yaml ships with the runtime deps
    yaml = None

ROOT = Path(__file__).resolve().parents[2]

#: Services that run our code and therefore need the whole configuration
#: surface. Backing stores (neo4j, postgres) are deliberately excluded: they
#: read their own vendor variables and handing them ours would leak secrets
#: into a container that has no use for them.
APP_SERVICES = ("extraction-service", "evaluation-interface")


def test_env_example_matches_code_and_compose():
    """The checker is the contract; run it rather than restating its rules."""
    result = subprocess.run(
        [sys.executable, "scripts/ci/check-env-contract.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_app_services_load_env_file():
    """Without this, everything in .env is inert inside the container."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]

    for name in APP_SERVICES:
        assert name in services, f"{name} vanished from docker-compose.yml"
        env_file = services[name].get("env_file")
        assert env_file, (
            f"{name} has no env_file, so .env never reaches the process inside "
            f"the container and every variable set there is silently ignored"
        )
        paths = [
            entry["path"] if isinstance(entry, dict) else entry
            for entry in (env_file if isinstance(env_file, list) else [env_file])
        ]
        assert ".env" in paths, f"{name} loads {paths}, not .env"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_container_hostname_still_overrides_env_file():
    """`environment` must keep beating `env_file`, or compose breaks itself.

    A host-side `.env` sensibly carries `NEO4J_URI=bolt://localhost:7687`, which
    resolves to the container itself rather than the database. The compose file
    corrects it to the service hostname, and that correction only holds because
    `environment` is applied after `env_file`. If the override were dropped in
    favour of the .env value, the stack would come up and fail to connect.
    """
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    env = compose["services"]["extraction-service"]["environment"]
    entries = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]

    uri = [e for e in entries if e.startswith("NEO4J_URI=")]
    assert uri, "extraction-service no longer pins NEO4J_URI to the service host"
    assert "localhost" not in uri[0], (
        f"{uri[0]} points the container at itself instead of the neo4j service"
    )
