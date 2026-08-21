"""The serving image must not ship remote code execution.

`extraction/entrypoint.sh` started Jupyter Lab with `--allow-root` and
`--NotebookApp.token=''` on `0.0.0.0:8888`, in the same image that serves the
API. Anyone who reached that port got arbitrary code execution as root, with
`MARA_API_KEY`, `NEO4J_PASSWORD` and `SEOCHO_AUTH_SECRET` in the shell's
environment. The only control was `SEOCHO_BIND_HOST` defaulting to `127.0.0.1`,
and the repo's own guides tell operators to set it to `0.0.0.0`.

Two more properties of the same file were wrong for different reasons:

  - `python -m extraction.main` ran a full extraction pipeline with graph writes
    on every container start. At one replica that is a surprise; at N replicas
    it is N concurrent ingests racing the same graph.
  - uvicorn was backgrounded behind `tail -f /dev/null`, and `set -e` does not
    apply to background jobs, so a dead API server left the container "up"
    forever — with no healthcheck and no restart policy to notice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "extraction" / "entrypoint.sh"


@pytest.fixture(scope="module")
def entrypoint() -> str:
    """Executable lines only — the comments in this file *describe* the flags
    that were removed, so scanning raw text finds them in the prose."""
    return "\n".join(
        line for line in ENTRYPOINT.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def test_no_tokenless_notebook_server(entrypoint):
    assert "--NotebookApp.token=''" not in entrypoint
    assert '--NotebookApp.token=""' not in entrypoint
    assert "--allow-root" not in entrypoint, (
        "a root notebook server in the serving image is remote code execution "
        "for anyone who can reach the port"
    )


def test_jupyter_is_opt_in_and_requires_a_token(entrypoint):
    assert "SEOCHO_ENABLE_JUPYTER" in entrypoint, "Jupyter must be opt-in"
    assert "SEOCHO_JUPYTER_TOKEN" in entrypoint, (
        "enabling the notebook server must require a token, or the opt-in just "
        "moves the same hole behind a flag"
    )
    # The guard must precede the launch, not follow it.
    assert entrypoint.index("SEOCHO_JUPYTER_TOKEN") < entrypoint.index("jupyter lab")


def test_batch_pipeline_does_not_run_on_every_start(entrypoint):
    assert "SEOCHO_RUN_BATCH_ON_START" in entrypoint, (
        "a full ingest on container start becomes N concurrent ingests at N "
        "replicas, racing the same graph"
    )


def test_api_server_is_pid_one(entrypoint):
    """A backgrounded server cannot fail the container, so nothing restarts it."""
    assert "exec uvicorn" in entrypoint
    assert "tail -f /dev/null" not in entrypoint, (
        "keeping PID 1 alive independently of the server is what made a dead "
        "API look healthy"
    )


@pytest.mark.parametrize("dockerfile", [
    "extraction/Dockerfile",
    "evaluation/Dockerfile",
])
def test_images_do_not_run_as_root(dockerfile):
    text = (ROOT / dockerfile).read_text()
    assert "USER " in text, f"{dockerfile} runs every process as root"
    # USER must come after the COPY/RUN steps it protects.
    assert text.index("USER ") > text.rindex("COPY ")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text())


def test_app_service_has_a_healthcheck_and_restart_policy(compose):
    service = compose["services"]["extraction-service"]
    assert service.get("healthcheck"), (
        "without a probe a crashed API server is indistinguishable from a "
        "healthy one"
    )
    assert service.get("restart") == "unless-stopped"


def test_healthcheck_does_not_depend_on_the_batch_pipeline(compose):
    """`/health/batch` reports on a batch that no longer runs by default."""
    probe = str(compose["services"]["extraction-service"]["healthcheck"]["test"])
    assert "/health/batch" not in probe
    assert "/health/runtime" in probe


def test_ui_waits_for_readiness_not_start_order(compose):
    depends = compose["services"]["evaluation-interface"]["depends_on"]
    assert isinstance(depends, dict), "bare depends_on only orders creation"
    assert depends["extraction-service"]["condition"] == "service_healthy"


def test_auth_mode_must_be_stated_explicitly(compose):
    """The default is anonymous; compose should make the operator choose."""
    env = compose["services"]["extraction-service"]["environment"]
    entries = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]
    auth = [e for e in entries if e.startswith("SEOCHO_AUTH_MODE=")]
    assert auth, "SEOCHO_AUTH_MODE is not passed at all"
    assert ":?" in auth[0], (
        "compose guards NEO4J_PASSWORD this way; the access-control decision "
        "deserves the same forcing function"
    )
