#!/usr/bin/env python3
"""Give an existing analysis script a trace, without rewriting it.

Sixty published results come from scripts that predate the tracing harness. They
can be re-run for nothing, because the paid extraction they read from is already
stored, but wiring the harness into sixty files by hand would be sixty chances
to change a number while claiming only to have added logging.

So the script is run unmodified, inside a live run directory. What that captures:

    the command and its arguments, exactly as issued
    the commit the code was at, and whether the tree had uncommitted edits
    everything the script printed, in order
    every Neo4j statement it issued, with the server's own timing — the driver
      log handler is attached to the driver's own logger, so this works without
      the script knowing
    which artifact files it created or changed
    how long it took, and whether it succeeded

What it does not capture is a per-stage breakdown of inputs and outputs, because
the script has no stages to report. That is a real difference and the run
records it: `trace_depth` is "script" rather than "stage", so nobody can mistake
one for the other later.

Execution is in-process rather than as a subprocess, which is what lets the
driver log handler see the database traffic. The cost is that a script calling
sys.exit or leaving global state behind can affect the next one, so each runs in
its own child process of this one, with the harness set up inside the child.

    python3 experiments/retrace.py --list
    python3 experiments/retrace.py --all
    python3 experiments/retrace.py examples/mdm/23_category_context_divergence.py
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import runpy
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "experiments/minimal"
PLAN = ROOT / "outputs/minimal/retrace_plan.json"
OUT_ROOT = ROOT / "outputs/minimal"
WATCHED = (ROOT / "outputs",)


def snapshot() -> dict[str, float]:
    """Modification times of every artifact, so changes can be attributed."""
    found: dict[str, float] = {}
    for base in WATCHED:
        for path in base.rglob("*.json"):
            try:
                found[str(path)] = path.stat().st_mtime
            except OSError:
                continue
    return found


def _execute(script: str, argv: list[str], queue) -> None:
    """Run one script inside a fresh process, under a live run directory."""
    sys.path.insert(0, str(HARNESS))
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    import observe

    name = Path(script).stem.replace("_", "-")[:40]
    run = observe.Run(OUT_ROOT, f"retrace-{name}", {
        "decisive": {"script": script, "argv": argv,
                     "trace_depth": "script",
                     "note": ("re-run of a pre-harness script, captured whole "
                              "rather than stage by stage")},
        "runtime": {"cwd": str(ROOT)}})

    before = snapshot()
    started = time.perf_counter()
    status, error = "ok", ""

    class Tee:
        """Send the script's output to the run log as well as the terminal."""

        def __init__(self, stream, log) -> None:
            self._stream, self._log = stream, log

        def write(self, text: str) -> int:
            self._stream.write(text)
            for line in text.rstrip("\n").splitlines():
                if line.strip():
                    self._log(f"    {line}")
            return len(text)

        def flush(self) -> None:
            self._stream.flush()

    original_argv, original_out = sys.argv, sys.stdout
    try:
        with run.stage("script", path=script, argv=argv) as out:
            sys.argv = [script] + argv
            sys.stdout = Tee(original_out, run.log)
            try:
                runpy.run_path(str(ROOT / script), run_name="__main__")
            except SystemExit as exit_code:
                if exit_code.code not in (0, None):
                    status = "error"
                    error = f"exited with {exit_code.code}"
            out["status"] = status
            out["seconds"] = round(time.perf_counter() - started, 3)
    except Exception as exc:  # noqa: BLE001 — recorded, never imputed
        status, error = "error", f"{type(exc).__name__}: {exc}"
        run.log(f"FAILED: {error}")
    finally:
        sys.argv, sys.stdout = original_argv, original_out

    after = snapshot()
    touched = sorted(p for p, m in after.items()
                     if before.get(p) != m and "/outputs/minimal/" not in p)
    contracts = []
    for path in touched:
        try:
            payload = json.loads(Path(path).read_text())
            if isinstance(payload, dict) and payload.get("contract"):
                contracts.append(payload["contract"])
        except Exception:  # noqa: BLE001
            continue

    directory = run.finish({
        "script": script, "status": status, "error": error,
        "artifacts_touched": [p.replace(str(ROOT) + "/", "") for p in touched],
        "contracts_written": sorted(set(contracts)),
        "trace_depth": "script",
    })
    queue.put({"script": script, "status": status, "error": error,
               "run_dir": str(directory.relative_to(ROOT)),
               "contracts": sorted(set(contracts)),
               "seconds": round(time.perf_counter() - started, 3)})


def run_one(script: str, argv: list[str], timeout: int) -> dict[str, Any]:
    context = mp.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_execute, args=(script, argv, queue))
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(10)
        return {"script": script, "status": "timeout", "error": f"> {timeout}s",
                "contracts": [], "run_dir": "", "seconds": timeout}
    try:
        return queue.get_nowait()
    except Exception:  # noqa: BLE001
        return {"script": script, "status": "no result",
                "error": "the child produced no record", "contracts": [],
                "run_dir": "", "seconds": 0}


# The tooling that audits the experiments is not itself an experiment. Running
# it under the retracer would produce a run directory whose only content is the
# audit of the run directories, which is noise.
TOOLING = ("experiments/registry.py", "experiments/verify_claims.py",
           "experiments/findings.py", "experiments/audit_observability.py",
           "experiments/retrace_plan.py", "experiments/retrace.py",
           "scripts/ops/audit_databases.py")


def planned_scripts() -> list[str]:
    if not PLAN.is_file():
        raise SystemExit("run experiments/retrace_plan.py --json first")
    payload = json.loads(PLAN.read_text())
    scripts = []
    for row in payload["contracts"]:
        if (row["bucket"] == "free" and row["script"]
                and row["script"] not in TOOLING
                and row["script"] not in scripts):
            scripts.append(row["script"])
    return scripts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scripts", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--argv", default="", help="arguments passed to every script")
    args = ap.parse_args()

    scripts = args.scripts or (planned_scripts() if (args.all or args.list) else [])
    if not scripts:
        raise SystemExit("name a script, or pass --all")
    if args.list:
        for script in scripts:
            print(script)
        print(f"\n{len(scripts)} scripts re-runnable at no cost")
        return 0

    argv = args.argv.split() if args.argv else []
    results = []
    for index, script in enumerate(scripts, 1):
        print(f"[{index}/{len(scripts)}] {script}", flush=True)
        result = run_one(script, argv, args.timeout)
        results.append(result)
        print(f"    {result['status']}  {result['seconds']}s  "
              f"{', '.join(result['contracts']) or 'no contract written'}",
              flush=True)

    ok = sum(1 for r in results if r["status"] == "ok")
    traced = sum(1 for r in results if r["run_dir"])
    payload = {
        "contract": "seocho.retrace_run.v1",
        "question": ("Can every pre-harness result be re-produced with a record "
                     "of the run behind it?"),
        "method": ("each script executed unmodified inside a live run "
                   "directory, in its own process, capturing the command, the "
                   "commit, everything printed, every database statement with "
                   "the server's timing, and which artifacts changed"),
        "claim_boundary": ("A script-level trace, not a stage-level one. It "
                           "records that the script ran and what it touched; it "
                           "does not record the inputs and outputs of each step "
                           "inside it, because the script has no steps to "
                           "report. Marked trace_depth=script in every run."),
        "scripts": len(results), "succeeded": ok, "with_run_directory": traced,
        "results": results,
    }
    out = OUT_ROOT / "retrace_run.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"\n{ok}/{len(results)} succeeded, {traced} left a run directory")
    for result in results:
        if result["status"] != "ok":
            print(f"  {result['status']:9s} {result['script']}  {result['error'][:70]}")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
