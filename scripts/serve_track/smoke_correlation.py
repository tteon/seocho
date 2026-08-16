"""End-to-end smoke: does the window/event join actually attribute blocks to a stage?

Stands in for the RAG pipeline with two shaped calls against a live vLLM, so the
harness is proven on real KV events before a real workload is wired to it:

  retrieve_ctx  a long, stable prefix + a varying tail — the shape a graph
                subgraph serialization has
  synthesize    the same prefix again — must reuse blocks, which is the whole
                claim the Serve Track talk rests on

Passes if the second call reports cached tokens and the correlation attributes
stored blocks to the stage that caused them.

Usage:
  python scripts/serve_track/smoke_correlation.py --out-dir outputs/serve_track/smoke
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"serve_track_{name}", _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kv_windows = _load("kv_windows")
correlate_kv = _load("correlate_kv")

_probe_spec = importlib.util.spec_from_file_location(
    "kv_events_probe", _HERE.parent / "cache_probe" / "kv_events_probe.py"
)
_probe = importlib.util.module_from_spec(_probe_spec)
sys.modules["kv_events_probe"] = _probe
_probe_spec.loader.exec_module(_probe)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000/v1")
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--events", default="tcp://127.0.0.1:5557")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/serve_track/smoke"))
    args = parser.parse_args()

    from openai import OpenAI

    args.out_dir.mkdir(parents=True, exist_ok=True)
    recorder = kv_windows.WindowRecorder(args.out_dir / "kv_windows.jsonl")
    client = OpenAI(base_url=args.base_url, api_key="smoke")

    # Subscriber must bind: vLLM connects when given a concrete host:port.
    sub = _probe.Subscriber(args.events, bind=True)
    sub.start()
    time.sleep(2.0)

    # A stable head that stands in for system + ontology sections, long enough
    # to span many blocks so reuse is visible at block granularity.
    #
    # The head is salted per invocation. Without it the *previous* smoke run's
    # blocks are still resident, the first call reuses them, and the test reads
    # as "no reuse happened" when the truth is "reuse happened one run too
    # early". A cold first call is what makes the assertion mean anything.
    salt = uuid.uuid4().hex
    head = f"Run {salt}. " + "Ontology: Account(id,balance) -[TRANSFER]-> Account. " * 60
    sections = {"system": 0, "ontology": len(head)}

    def call(role: str, prompt: str, extra_sections: dict) -> None:
        with recorder.record_step(
            trace_id="smoke", role=role, model=args.model, provider="vllm",
            prompt_chars=len(prompt),
            prompt_sections={**sections, **extra_sections},
        ) as window:
            response = client.completions.create(
                model=args.model, prompt=prompt, max_tokens=8, temperature=0.0)
            usage = response.usage
            window.usage = usage.model_dump() if hasattr(usage, "model_dump") else {}
            time.sleep(0.5)  # let the engine's events arrive inside the window

    call("retrieve_ctx", head + "Subgraph rows: 1..40. Question: who funds A?",
         {"subgraph": 44})
    call("synthesize", head + "Subgraph rows: 1..40. Answer concisely.",
         {"subgraph": 44})

    sub.stop()
    time.sleep(1.0)

    events_path = args.out_dir / "kv_events.jsonl"
    with events_path.open("w", encoding="utf-8") as handle:
        import json
        for frame in sub.frames:
            handle.write(json.dumps(frame, default=str) + "\n")

    report = correlate_kv.correlate(args.out_dir)
    import json
    print(json.dumps(report, indent=2, ensure_ascii=False))

    stages = report["stages"]
    problems = []
    if report["events"] == 0:
        problems.append("no KV events received — check the ZMQ endpoint and --bind")

    first_blocks = sum(stages.get("retrieve_ctx", {}).get("blocks_per_call", []) or [0])
    second_blocks = sum(stages.get("synthesize", {}).get("blocks_per_call", []) or [0])
    if first_blocks <= 0:
        problems.append("no blocks attributed to the first stage — window/event clocks disagree")
    # Reuse is asserted block-side, not token-side: vLLM 0.27.1 leaves
    # `prompt_tokens_details.cached_tokens` unpopulated (measured), so a shared
    # prefix shows up as blocks the second call did NOT have to store.
    elif second_blocks >= first_blocks:
        problems.append(
            f"second stage stored {second_blocks} blocks vs {first_blocks} — "
            "the shared prefix was not reused; is --enable-prefix-caching on?"
        )

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        raise SystemExit(1)
    print("\nOK: blocks attributed per stage and prefix reuse observed.")


if __name__ == "__main__":
    main()
