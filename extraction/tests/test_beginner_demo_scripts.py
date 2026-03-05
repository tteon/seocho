from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = [
    "scripts/demo/pipeline_raw_data.sh",
    "scripts/demo/pipeline_meta_artifact.sh",
    "scripts/demo/pipeline_neo4j_load.sh",
    "scripts/demo/pipeline_graphrag_opik.sh",
    "scripts/demo/run_beginner_pipelines.sh",
]


@pytest.mark.parametrize("relative_path", SCRIPTS)
def test_demo_script_has_valid_bash_syntax(relative_path: str) -> None:
    script_path = REPO_ROOT / relative_path
    result = subprocess.run(
        ["bash", "-n", str(script_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("relative_path", SCRIPTS)
def test_demo_script_help_text(relative_path: str) -> None:
    script_path = REPO_ROOT / relative_path
    result = subprocess.run(
        ["bash", str(script_path), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
