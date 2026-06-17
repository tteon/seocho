"""Tests for the perf/behavioral budget harness (seocho-6q9.2)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import perf_budget as pb  # noqa: E402


def _write(path: Path, spans) -> None:
    path.write_text("\n".join(json.dumps(s) for s in spans) + "\n", encoding="utf-8")


def test_latency_ms_derivation() -> None:
    assert pb._latency_ms({"metadata": {"elapsed_seconds": 2.5}}) == 2500.0
    assert pb._latency_ms({"metadata": {"total_ms": 800}}) == 800.0
    assert pb._latency_ms({"latency_ms": 1234.0}) == 1234.0
    assert pb._latency_ms({"metadata": {}}) is None


def test_startup_budget_breach() -> None:
    spans = [{"name": "sdk.session.start", "timestamp": "t", "metadata": {"elapsed_seconds": 1.2}}]
    budget = pb.Budget(startup_ms=800, startup_names=["sdk.session.start"], default_ceiling_ms=2000)
    violations = pb.evaluate(spans, budget)
    assert len(violations) == 1
    assert violations[0].kind == "startup"
    assert violations[0].observed_ms == 1200.0
    assert violations[0].budget_ms == 800.0


def test_span_ceiling_breach_and_name_override() -> None:
    spans = [
        {"name": "sdk.query", "metadata": {"elapsed_seconds": 3.0}},   # over default 2000
        {"name": "sdk.fast", "metadata": {"elapsed_seconds": 0.5}},    # under default
        {"name": "sdk.slowok", "metadata": {"elapsed_seconds": 4.0}},  # allowed by override
    ]
    budget = pb.Budget(default_ceiling_ms=2000, name_ceilings={"sdk.slowok": 5000})
    violations = pb.evaluate(spans, budget)
    names = {v.name for v in violations}
    assert names == {"sdk.query"}


def test_all_within_budget_is_empty() -> None:
    spans = [{"name": "sdk.query", "metadata": {"elapsed_seconds": 0.1}}]
    assert pb.evaluate(spans, pb.Budget(default_ceiling_ms=2000)) == []


def test_ignore_names_exempt() -> None:
    spans = [{"name": "batch.reindex", "metadata": {"elapsed_seconds": 99.0}}]
    budget = pb.Budget(default_ceiling_ms=2000, ignore_names=["batch.reindex"])
    assert pb.evaluate(spans, budget) == []


def test_spans_without_latency_skipped() -> None:
    spans = [{"name": "sdk.session.marker", "metadata": {}}]
    assert pb.evaluate(spans, pb.Budget(default_ceiling_ms=1)) == []


def test_load_spans_inline_fallback(tmp_path: Path) -> None:
    path = tmp_path / "seocho.jsonl"
    _write(path, [
        {"name": "ok", "metadata": {"elapsed_seconds": 0.1}},
        {"name": "ok2", "metadata": {"elapsed_seconds": 0.2}},
    ])
    spans = pb._inline_read_jsonl(path)
    assert [s["name"] for s in spans] == ["ok", "ok2"]
    assert spans[0]["latency_ms"] == 100.0


def test_main_exit_codes(tmp_path: Path, capsys) -> None:
    path = tmp_path / "seocho.jsonl"

    # breach -> exit 1
    _write(path, [{"name": "sdk.query", "timestamp": "t", "metadata": {"elapsed_seconds": 3.0}}])
    assert pb.main(["--path", str(path), "--max-span-ms", "2000"]) == 1

    # within budget -> exit 0
    _write(path, [{"name": "sdk.query", "metadata": {"elapsed_seconds": 0.1}}])
    assert pb.main(["--path", str(path), "--max-span-ms", "2000"]) == 0

    # no budgets declared -> exit 2
    assert pb.main(["--path", str(path)]) == 2

    # missing file -> exit 2
    assert pb.main(["--path", str(tmp_path / "nope.jsonl"), "--max-span-ms", "2000"]) == 2


def test_main_with_config_file(tmp_path: Path) -> None:
    cfg = tmp_path / "budgets.json"
    cfg.write_text(json.dumps({"startup_ms": 800, "startup_names": ["boot"], "default_ceiling_ms": 2000}))
    path = tmp_path / "seocho.jsonl"
    _write(path, [{"name": "boot", "timestamp": "t", "metadata": {"elapsed_seconds": 1.0}}])
    assert pb.main(["--path", str(path), "--config", str(cfg)]) == 1
