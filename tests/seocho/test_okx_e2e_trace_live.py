import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.benchmarks.okx_e2e_trace_live import _stage


def test_stage_accepts_a_passing_live_report() -> None:
    report, elapsed_ms = asyncio.run(_stage("memory", lambda: {"passed": True}))
    assert report == {"passed": True}
    assert elapsed_ms >= 0


def test_stage_rejects_a_failed_live_report() -> None:
    with pytest.raises(RuntimeError, match="live stage failed: memory"):
        asyncio.run(_stage("memory", lambda: {"passed": False}))
