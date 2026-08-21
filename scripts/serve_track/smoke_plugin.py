#!/usr/bin/env python3
"""Prove the vLLM stat-logger plugin actually loads and writes. Run after any vLLM bump.

The plugin was written, registered correctly, and never verified end-to-end. It
does not work on the path most people reach for first: `load_stat_logger_plugin_
factories()` is called in exactly one place in vLLM 0.27.1 --
`AsyncLLM.__init__` -- so the offline `LLM(...)` batch API loads nothing. The
failure is silent in the worst way: the entry point resolves, the class passes
vLLM's `issubclass(StatLoggerBase)` check, no warning is logged, and the output
file simply stays empty. You conclude the cache is behaving oddly when in fact
nothing was ever measured.

This checks the four things that can independently break, in order of how
quietly they fail:

  1. registration   the entry point is visible to the vLLM interpreter, which is
                    a DIFFERENT venv from the SDK's -- installing the plugin into
                    the wrong one is the most common setup error
  2. contract       our class still satisfies StatLoggerBase's abstract methods
                    and `record()` still takes the arguments we unpack; vLLM
                    calls these "not stable interfaces", so a bump can move them
  3. server load    a real `vllm serve` writes the engine-initialized record
  4. measurement    a repeated prompt reports num_cached_tokens > 0, which is the
                    field the whole KV-reuse argument rests on

Step 4 exists because a prompt shorter than one block never caches anything, so
a smoke test with a five-token prompt reports `num_cached_tokens: 0` on a warm
request and looks like a broken plugin. It uses a prompt long enough to fill
blocks, and asserts the warm request is mostly cached.

Usage:
    scripts/serve_track/smoke_plugin.py                    # steps 1-2, no GPU
    scripts/serve_track/smoke_plugin.py --serve            # all four, needs a GPU
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SMALL_MODEL = "Qwen/Qwen3-0.6B"
PORT = 8199


def _ok(label: str, detail: str = "") -> None:
    print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))


def _fail(label: str, detail: str) -> int:
    print(f"  FAIL  {label}\n        {detail}")
    return 1


def check_registration() -> int:
    from importlib.metadata import entry_points

    found = list(entry_points().select(group="vllm.stat_logger_plugins"))
    if not found:
        return _fail(
            "entry point registered",
            "nothing under 'vllm.stat_logger_plugins'. The plugin must be "
            "installed into the vLLM venv, not the SDK's:\n"
            "        VIRTUAL_ENV=~/.venvs/vllm-serve uv pip install -e "
            "scripts/serve_track/vllm_plugin",
        )
    _ok("entry point registered", ", ".join(f"{e.name}={e.value}" for e in found))
    return 0


def check_contract() -> int:
    import inspect

    try:
        from vllm.v1.metrics.loggers import StatLoggerBase
    except ImportError as exc:
        return _fail("vllm importable", f"{type(exc).__name__}: {exc}")

    from seocho_vllm_probe import SeochoRequestStatsLogger

    if not issubclass(SeochoRequestStatsLogger, StatLoggerBase):
        return _fail("subclasses StatLoggerBase", "vLLM rejects the plugin at load")

    missing = [
        name
        for name in getattr(StatLoggerBase, "__abstractmethods__", set())
        if getattr(SeochoRequestStatsLogger, name, None) is None
    ]
    if missing:
        return _fail("implements every abstract method", f"missing: {missing}")

    # `record` is the one that unpacks vLLM internals; a renamed or reordered
    # parameter here is how a version bump breaks this silently.
    params = set(inspect.signature(StatLoggerBase.record).parameters)
    ours = set(inspect.signature(SeochoRequestStatsLogger.record).parameters)
    if not params <= ours:
        return _fail(
            "record() accepts what vLLM passes",
            f"vLLM sends {sorted(params - ours)} which our signature does not take",
        )
    _ok("StatLoggerBase contract", f"abstract={sorted(StatLoggerBase.__abstractmethods__)}")
    return 0


def _post(prompt: str, max_tokens: int = 8) -> int:
    body = json.dumps(
        {"model": SMALL_MODEL, "prompt": prompt, "max_tokens": max_tokens, "temperature": 0}
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status


def check_serving(out_path: Path, venv: Path, timeout: float = 300.0) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)

    env = dict(os.environ)
    env["SEOCHO_PROBE_OUT"] = str(out_path)
    env["VLLM_PLUGINS"] = "seocho_probe"
    env.setdefault("HF_HOME", "/data/hadry/huggingface")

    log = out_path.with_name("smoke_server.log")
    with log.open("w") as handle:
        proc = subprocess.Popen(
            [str(venv / "bin" / "vllm"), "serve", SMALL_MODEL,
             "--port", str(PORT), "--gpu-memory-utilization", "0.55",
             "--max-model-len", "2048", "--enforce-eager", "--enable-prefix-caching"],
            stdout=handle, stderr=subprocess.STDOUT, env=env,
        )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return _fail("server started", f"exited rc={proc.returncode}; see {log}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(3)
        else:
            return _fail("server started", f"no /health within {timeout:.0f}s; see {log}")
        _ok("server started")

        # A prompt long enough to fill KV blocks. Anything shorter caches nothing
        # and makes a working plugin look broken.
        prompt = "The Northgate Plant assembles Model K1 which is sold in Norland. " * 30
        for _ in range(2):
            _post(prompt)
            time.sleep(2)
        time.sleep(3)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not out_path.exists() or out_path.stat().st_size == 0:
        return _fail(
            "plugin wrote records",
            f"{out_path} is empty. If you ran through the offline LLM() API this "
            "is expected -- only AsyncLLM loads stat-logger plugins.",
        )

    rows = [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]
    if not any(r.get("event") == "engine_initialized" for r in rows):
        return _fail("engine_initialized recorded", "log_engine_initialized never fired")
    _ok("plugin wrote records", f"{len(rows)} rows")

    finished = [r for r in rows if r.get("request_id")]
    if len(finished) < 2:
        return _fail("per-request rows", f"expected 2, got {len(finished)}")

    warm = finished[-1]
    cached, total = warm.get("num_cached_tokens"), warm.get("num_prompt_tokens")
    if not cached or not total or cached < total * 0.5:
        return _fail(
            "num_cached_tokens is populated",
            f"warm request cached {cached} of {total} prompt tokens. This field "
            "carries the whole KV-reuse measurement; if it is zero on a repeated "
            "prompt, prefix caching is off or the field moved.",
        )
    _ok("prefix reuse measured", f"warm request cached {cached}/{total} tokens")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true",
                        help="also boot a real server (needs a GPU)")
    parser.add_argument("--venv", type=Path,
                        default=Path.home() / ".venvs" / "vllm-serve")
    parser.add_argument("--out", type=Path,
                        default=Path("outputs/serve_track/smoke/request_stats.jsonl"))
    args = parser.parse_args()

    print("vLLM stat-logger plugin smoke test")
    rc = check_registration()
    rc |= check_contract()
    if args.serve and rc == 0:
        rc |= check_serving(args.out, args.venv)
    elif not args.serve:
        print("  SKIP  server load and prefix-reuse measurement (pass --serve)")

    print("\n" + ("all checks passed" if rc == 0 else "FAILED"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
