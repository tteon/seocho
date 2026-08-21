"""The three configuration surfaces must agree, and .env must reach the container.

Two separate failures are locked down here, because fixing the first does not
prevent the second.

`.env.example` drifting from what the code reads is a documentation failure: a
reader cannot discover a setting that is not listed. That is what
`check-env-contract.py` measures.

A variable reaching `compose.yaml` but not the process inside the container
is a different and quieter failure. Compose reads `.env` for `${VAR}`
substitution and does *not* inject it into containers, so before `env_file:` was
added the service passed 11 variables while the code read 74 -- setting
`MARA_API_KEY` or `SEOCHO_AUTH_SECRET` in `.env` did nothing at all, with no
error anywhere. Deleting one `env_file:` block silently restores that, so the
wiring is asserted rather than trusted.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - yaml ships with the runtime deps
    yaml = None

ROOT = Path(__file__).resolve().parents[2]

#: Services that need the whole configuration surface. The rule is "a container
#: gets the configuration it reads", so this is narrower than "our code":
#:
#:  - backing stores (neo4j, postgres) read their own vendor variables;
#:  - `evaluation-interface` is our code but reads exactly ONE variable,
#:    EXTRACTION_SERVICE_URL, and is the internet-facing tier. Handing it the
#:    whole .env put SEOCHO_AUTH_SECRET, NEO4J_PASSWORD and every provider key
#:    into the environment of a thin proxy with no use for them.
APP_SERVICES = ("extraction-service",)

#: Services that must NOT receive the whole .env, with the reason.
SECRET_MINIMISED_SERVICES = {
    "evaluation-interface": "reads only EXTRACTION_SERVICE_URL",
}


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
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    services = compose["services"]

    for name in APP_SERVICES:
        assert name in services, f"{name} vanished from compose.yaml"
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
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    env = compose["services"]["extraction-service"]["environment"]
    entries = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]

    uri = [e for e in entries if e.startswith("NEO4J_URI=")]
    assert uri, "extraction-service no longer pins NEO4J_URI to the service host"
    assert "localhost" not in uri[0], (
        f"{uri[0]} points the container at itself instead of the neo4j service"
    )


def test_documented_variables_never_inject_empty_values():
    """A documented variable must not change behaviour just by being documented.

    This is the bug the compose-text assertions above could not see, and it was
    introduced by the very commit that added them.

    Compose injects `NAME=` from env_file as a present-but-empty key, and
    `os.getenv("NAME", default)` returns "" rather than the default when the key
    is present. `extraction/config.py` gives DOZERDB_* precedence over NEO4J_*,
    so an empty `DOZERDB_URI=` line in .env.example silently defeated the
    container-hostname override the test above so carefully pins -- the
    container resolved an empty URI, user and password.

    So: a variable with no value is commented out. It stays documented (the
    contract checker counts `# NAME=`) and stays inert.
    """
    lines = (ROOT / ".env.example").read_text().splitlines()
    live_empty = [
        line for line in lines
        if re.fullmatch(r"[A-Z][A-Z0-9_]*=", line.strip())
    ]
    assert not live_empty, (
        "these declarations inject an empty value into every container, which "
        "defeats the code default rather than documenting it -- comment them "
        f"out instead: {live_empty}"
    )


def test_empty_env_value_really_does_defeat_a_default():
    """Pin the mechanism, so the rule above cannot be argued away later."""
    import os

    key = "SEOCHO_TEST_EMPTY_PROBE"
    previous = os.environ.get(key)
    try:
        os.environ[key] = ""
        assert os.getenv(key, "fallback") == "", (
            "if this ever fails, os.getenv semantics changed and the rule in "
            "test_documented_variables_never_inject_empty_values can be relaxed"
        )
        del os.environ[key]
        assert os.getenv(key, "fallback") == "fallback"
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def test_graph_credentials_resolve_under_the_documented_env():
    """Resolve the real precedence chain against the real .env.example.

    extraction/config.py prefers DOZERDB_* over NEO4J_*, so this walks the
    documented file into an environment and checks that the compose override
    survives -- the assertion the compose-text test cannot make.
    """
    import os

    env = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        env[name.strip()] = value

    # What compose.yaml pins for the container.
    env["NEO4J_URI"] = "bolt://neo4j:7687"

    def getenv(name, default=None):
        return env.get(name, default)

    resolved = getenv("DOZERDB_URI", getenv("NEO4J_URI", "bolt://localhost:7687"))
    assert resolved == "bolt://neo4j:7687", (
        f"container would connect to {resolved!r} instead of the neo4j service"
    )
    assert getenv("DOZERDB_USER", getenv("NEO4J_USER", "neo4j"))
    assert getenv("DOZERDB_PASSWORD", getenv("NEO4J_PASSWORD", "password"))


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_thin_proxy_does_not_receive_every_secret():
    """A container gets the configuration it reads, not the configuration that
    exists. `evaluation/server.py` reads one variable; env_file would give it
    every provider key and the auth secret."""
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())

    for name, reason in SECRET_MINIMISED_SERVICES.items():
        service = compose["services"][name]
        assert not service.get("env_file"), (
            f"{name} {reason}, so the whole .env must not be injected into it"
        )
        env = service.get("environment") or {}
        keys = (
            set(env) if isinstance(env, dict)
            else {entry.split("=")[0] for entry in env}
        )
        leaked = {k for k in keys if "SECRET" in k or "PASSWORD" in k}
        assert not leaked, f"{name} is handed {sorted(leaked)}"



def test_checker_finds_provider_credentials_without_importing_the_sdk():
    """The vLLM tier's credential is ProviderSpec data, not a getenv literal.

    It was undocumented while the checker printed "all three surfaces agree".
    The first fix imported `seocho.store.llm` to read the presets — which made
    the result depend on whether the SDK's dependencies were installed, so the
    checker passed locally and flipped those names to "declared but read
    nowhere" in CI. Parsing the source is deterministic.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "env_contract_checker", ROOT / "scripts" / "ci" / "check-env-contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    names = module.provider_key_variables()
    assert "SEOCHO_VLLM_API_KEY" in names, "the H200 tier's credential is invisible"
    assert "VLLM_API_KEY" in names, "the legacy alias is invisible"
    assert "MARA_API_KEY" in names, "the default provider's key is invisible"


def test_checker_agrees_in_a_clean_environment():
    """CI is leaner than a dev box; the checker must not depend on that."""
    import os

    result = subprocess.run(
        [sys.executable, "scripts/ci/check-env-contract.py"],
        cwd=ROOT, capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
