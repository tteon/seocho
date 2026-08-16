"""Instruments must actually fire, not merely exist.

Three separate failures made the whole observability stack inert in the
deployed runtime, and every one of them passed the existing contract test:

1. `configure_tracing_from_env()` had no caller in `runtime/`, so `_BACKENDS`
   stayed empty and every `rag.*` span resolved to `_NullSpan`. The span tree
   was correct and never emitted.
2. `log_extraction`/`log_query` grew `workspace_id`, `provider` and `stage`
   parameters that no call site passed, so no span ever carried them.
3. `log_span` ignored the enclosing trace context, so `sdk.extraction` and
   `sdk.query` landed as orphans beside the `rag.*` tree instead of inside it.

The contract test could not see any of it because it defines "emitted" as *the
metric name appears as a string literal somewhere under src/*. A module full of
instruments that nothing ever calls satisfies that. So these tests assert on
what a backend actually received.
"""

from __future__ import annotations

import time

import pytest

from seocho import tracing


class _Recorder(tracing.TracingBackend):
    def __init__(self):
        self.spans = []

    def log_span(self, name, *, input_data=None, output_data=None,
                 metadata=None, tags=None, **kwargs):
        self.spans.append({
            "name": name, "input": input_data or {}, "output": output_data or {},
            "metadata": metadata or {}, "tags": list(tags or []),
        })

    def flush(self):
        pass


@pytest.fixture
def recorder(monkeypatch):
    """log_span fans out to tracing._BACKENDS, so that list is the real seam."""
    rec = _Recorder()
    monkeypatch.setattr(tracing, "_BACKENDS", [rec])
    return rec


# ---------------------------------------------------------------------------
# 1. The server turns tracing on
# ---------------------------------------------------------------------------

def test_configure_tracing_from_env_is_called_at_server_startup():
    """The bootstrap that was missing. Asserted on source because importing
    runtime.agent_server pulls in the whole FastAPI app and its dependencies."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "runtime" / "agent_server.py").read_text()
    startup = source[source.index("async def _startup():"):]
    startup = startup[:startup.index("\n# ---")] if "\n# ---" in startup else startup

    assert "configure_tracing_from_env" in startup, (
        "the server never enables tracing, so every rag.* span is a no-op and "
        "stage attribution is impossible however good the span tree is"
    )


def test_tracing_backend_env_actually_installs_a_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("SEOCHO_TRACE_BACKEND", "jsonl")
    monkeypatch.setenv("SEOCHO_TRACE_JSONL_PATH", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(tracing, "_BACKENDS", [])

    assert tracing.configure_tracing_from_env() is True
    assert tracing._BACKENDS, "SEOCHO_TRACE_BACKEND=jsonl installed no backend"

    tracing.disable_tracing()


def test_backend_none_stays_off(monkeypatch):
    """Boot must never depend on a collector — the default is off."""
    monkeypatch.setenv("SEOCHO_TRACE_BACKEND", "none")
    monkeypatch.setattr(tracing, "_BACKENDS", [])
    assert tracing.configure_tracing_from_env() is False
    assert not tracing._BACKENDS


# ---------------------------------------------------------------------------
# 2. The call sites pass the arguments
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module,call,expected", [
    ("src/seocho/index/pipeline.py", "log_extraction(", ("workspace_id=", "provider=", "stage=")),
    ("src/seocho/local_engine.py", "log_query(", ("workspace_id=", "provider=", "stage=")),
])
def test_call_sites_pass_the_context_arguments(module, call, expected):
    """A parameter nothing passes is a parameter that does nothing."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / module).read_text()
    start = source.index(call)

    # Take the whole call by balancing parentheses — slicing at the first ")"
    # stops inside the argument list and reads as a missing argument.
    depth, end = 0, start
    for i in range(start, len(source)):
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    block = source[start:end]

    missing = [arg for arg in expected if arg not in block]
    assert not missing, f"{module} calls {call} without {missing}"


# ---------------------------------------------------------------------------
# 3. Stage spans join the request's trace
# ---------------------------------------------------------------------------

def test_stage_span_nests_under_the_enclosing_request_span(recorder):
    """`sdk.extraction` must be a child of `rag.ask`, not an orphan.

    Stage attribution is the ability to ask "which part of THIS request was
    slow or wrong". An orphan span cannot answer that however complete its own
    attributes are.
    """
    with tracing.start_span("rag.ask"):
        time.sleep(0.005)
        tracing.log_extraction(
            text_preview="source text", ontology_name="ent", model="MiniMax-M2.5",
            nodes_count=3, relationships_count=1, score=0.9,
            validation_errors=0, elapsed_seconds=1.25,
            workspace_id="tenant-a", provider="mara", stage="indexing",
        )

    by_name = {s["name"]: s["metadata"] for s in recorder.spans}
    assert "rag.ask" in by_name and "sdk.extraction" in by_name

    ask, extraction = by_name["rag.ask"], by_name["sdk.extraction"]
    assert extraction["trace_id"] == ask["trace_id"], "stage span is in another trace"
    assert extraction["parent_span_id"] == ask["span_id"], (
        "stage span is an orphan; it will not appear inside the request on a "
        "waterfall, so no per-request breakdown is possible"
    )


def test_stage_span_carries_a_duration(recorder):
    """A zero-duration record cannot be placed on a waterfall."""
    tracing.log_extraction(
        text_preview="x", ontology_name="ent", model="m",
        nodes_count=1, relationships_count=0, score=1.0,
        validation_errors=0, elapsed_seconds=2.5,
    )
    assert recorder.spans[-1]["metadata"]["duration_ms"] == 2500.0


def test_request_span_tree_has_real_timings(recorder):
    """start_span was already correct — pin it so a refactor cannot flatten it."""
    with tracing.start_span("rag.ask"):
        time.sleep(0.01)
        with tracing.start_span("rag.retrieve"):
            time.sleep(0.02)

    by_name = {s["name"]: s["metadata"] for s in recorder.spans}
    outer, inner = by_name["rag.ask"], by_name["rag.retrieve"]

    assert inner["parent_span_id"] == outer["span_id"]
    assert inner["duration_ms"] >= 15, "child span lost its timing"
    assert outer["duration_ms"] >= inner["duration_ms"], "parent shorter than child"


def test_stage_and_workspace_reach_the_span(recorder):
    tracing.log_extraction(
        text_preview="x", ontology_name="enterprise", model="MiniMax-M2.5",
        nodes_count=1, relationships_count=0, score=1.0, validation_errors=0,
        elapsed_seconds=0.1, workspace_id="tenant-a", provider="mara",
        stage="indexing",
    )
    span = recorder.spans[-1]
    assert "workspace:tenant-a" in span["tags"]
    assert "stage:indexing" in span["tags"]
    assert "model:mara/MiniMax-M2.5" in span["tags"]
