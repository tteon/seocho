#!/usr/bin/env python3
"""Run a FinBench benchmark from a YAML spec, and report parameters beside results.

The experiment accumulated a dozen scripts, each with its own flags. That is fine for
exploring and useless for explaining: a number in a chat log cannot be traced back to
the settings that produced it, and the settings that mattered most here (warm-up, page
cache size, query precedence) are exactly the ones easiest to forget having set.

So a run is declared in one file and the report echoes that file verbatim next to the
numbers. "What did you set, and what happened" becomes answerable from one document —
which is the requirement for handing the result to someone who was not present.

Stages are independent and skippable, so the spec doubles as documentation of what a
full run consists of:

    data        generate deterministic snapshots per scale factor
    load        bulk-load each into its own database
    graph_layer bounded Cypher, tuned vs naive shape, no LLM
    agent_layer the Graph Agentic RAG path per model
    infra_sweep restart under memory profiles to find where plans stop being flat

Usage:
    python scripts/finbench/bench_runner.py examples/finbench/bench.yaml
    python scripts/finbench/bench_runner.py examples/finbench/bench.yaml --dry-run
    python scripts/finbench/bench_runner.py examples/finbench/bench.yaml --only graph_layer
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_HERE = Path(__file__).resolve().parent
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

STAGES = ("data", "load", "graph_layer", "agent_layer", "infra_sweep")


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _interpolate(value: Any, *, errors: List[str], where: str = "") -> Any:
    """Resolve ${VAR} / ${VAR:-default}, matching seocho.run.yaml's convention."""
    if isinstance(value, dict):
        return {k: _interpolate(v, errors=errors, where=f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, errors=errors, where=f"{where}[{i}]") for i, v in enumerate(value)]
    if not isinstance(value, str):
        return value

    def repl(match: re.Match) -> str:
        name, default = match.group(1), match.group(2)
        resolved = os.getenv(name)
        if resolved is None:
            if default is None:
                errors.append(
                    f"{where or 'spec'}: ${{{name}}} is not set. "
                    f"Export it or write ${{{name}:-fallback}}.")
                return ""
            return default
        return resolved

    return _ENV_RE.sub(repl, value)


def load_spec(path: Path) -> Dict[str, Any]:
    payload = yaml.safe_load(path.read_text()) or {}
    errors: List[str] = []
    spec = _interpolate(payload, errors=errors, where="spec")
    if errors:
        raise SystemExit("spec errors:\n  " + "\n  ".join(errors))
    return spec


def _redacted(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Spec copy safe to write into a report — credentials removed, not hashed away."""
    import copy

    clone = copy.deepcopy(spec)
    target = clone.get("target") or {}
    if target.get("password"):
        target["password"] = "<redacted>"
    return clone


def stage_data(spec: Dict[str, Any]) -> Dict[str, Any]:
    cfg = spec.get("data") or {}
    out_root = Path(cfg.get("out_root", "outputs/finbench"))
    results = []
    for sf in cfg.get("scales", []):
        snapshot = out_root / f"sf{sf}"
        exists = (snapshot / "manifest.json").exists()
        if exists and not cfg.get("regenerate"):
            manifest = json.loads((snapshot / "manifest.json").read_text())
            results.append({"scale_factor": sf, "reused": True,
                            "counts": manifest.get("counts")})
            continue
        started = time.perf_counter()
        proc = subprocess.run(
            ["python", str(_HERE / "gen_duckdb.py"), "--sf", str(sf), "--out", str(out_root)],
            capture_output=True, text=True, timeout=3600)
        if proc.returncode != 0:
            results.append({"scale_factor": sf, "error": proc.stderr[-400:]})
            continue
        manifest = json.loads((snapshot / "manifest.json").read_text())
        results.append({"scale_factor": sf, "reused": False,
                        "generate_seconds": round(time.perf_counter() - started, 2),
                        "counts": manifest.get("counts")})
    return {"snapshots": results}


def stage_load(spec: Dict[str, Any]) -> Dict[str, Any]:
    cfg = spec.get("load") or {}
    if str(cfg.get("mode", "bulk")).lower() == "skip":
        return {"skipped": "mode=skip"}
    target = spec.get("target") or {}
    out_root = Path((spec.get("data") or {}).get("out_root", "outputs/finbench"))
    bulk = _load_module("finbench_bulk", "bulk_load.py")
    results = []
    for sf in (spec.get("data") or {}).get("scales", []):
        database = f"{cfg.get('db_prefix', 'finbenchsf')}{sf}"
        try:
            report = bulk.bulk_load(
                src=out_root / f"sf{sf}", database=database,
                container=cfg.get("container", "graphrag-neo4j"),
                uri=target.get("uri", "bolt://localhost:7687"),
                user=target.get("user", "neo4j"), password=target.get("password", ""),
                staging=out_root / "_staging")
            results.append({"scale_factor": sf, "database": database,
                            "counts": report.get("counts"),
                            "import_seconds": round(report["timings_s"]["import_s"], 2),
                            "relationships_per_second": round(
                                report.get("relationships_per_second") or 0)})
        except Exception as exc:
            results.append({"scale_factor": sf, "database": database,
                            "error": f"{type(exc).__name__}: {exc}"[:300]})
    return {"loads": results}


def stage_graph_layer(spec: Dict[str, Any]) -> Dict[str, Any]:
    cfg = spec.get("graph_layer") or {}
    if not cfg.get("enabled", True):
        return {"skipped": "disabled"}
    target = spec.get("target") or {}
    protocol_cfg = spec.get("protocol") or {}
    out_root = Path((spec.get("data") or {}).get("out_root", "outputs/finbench"))
    prefix = (spec.get("load") or {}).get("db_prefix", "finbenchsf")
    verify = _load_module("finbench_verify", "verify_scenarios.py")
    results = []
    for sf in (spec.get("data") or {}).get("scales", []):
        try:
            report = verify.verify(
                out_root / f"sf{sf}", target.get("uri"), target.get("user"),
                target.get("password"), f"{prefix}{sf}",
                repeats=int(protocol_cfg.get("repeats", 10)),
                warm=bool(protocol_cfg.get("warm_up", True)),
                container=(spec.get("load") or {}).get("container", "graphrag-neo4j"))
            results.append({
                "scale_factor": sf, "database": f"{prefix}{sf}",
                "graph": report["graph"], "passed": report["passed"],
                "scenarios": [
                    {"name": s["name"], "passed": s["passed"],
                     "tuned": {k: s["tuned"][k] for k in ("db_hits", "latency_ms", "p99_ms", "sargable")},
                     "naive": {k: s["naive"][k] for k in ("db_hits", "latency_ms", "p99_ms", "sargable")},
                     "naive_cost_multiple": s["naive_cost_multiple"]}
                    for s in report["scenarios"]],
            })
        except Exception as exc:
            results.append({"scale_factor": sf, "error": f"{type(exc).__name__}: {exc}"[:300]})
    return {"scales": results}


def stage_agent_layer(spec: Dict[str, Any]) -> Dict[str, Any]:
    cfg = spec.get("agent_layer") or {}
    if not cfg.get("enabled", True):
        return {"skipped": "disabled"}
    target = spec.get("target") or {}
    protocol_cfg = spec.get("protocol") or {}
    prefix = (spec.get("load") or {}).get("db_prefix", "finbenchsf")
    breakdown = _load_module("finbench_breakdown", "mara_breakdown.py")

    # Precedence and plan profiling are read by the engine from the environment, so
    # they are set here and recorded in the report — a run whose routing mode is not
    # written down cannot be reproduced.
    os.environ["SEOCHO_QUERY_PRECEDENCE"] = str(cfg.get("precedence", "template_first"))
    os.environ["SEOCHO_PROFILE_PLANS"] = str(protocol_cfg.get("profile_plans", "off"))

    from seocho.ontology import Ontology

    ontology = Ontology.load(Path(cfg["ontology"]))
    cases = json.loads(Path(cfg["cases"]).read_text())["cases"]
    results = []
    for sf in (spec.get("data") or {}).get("scales", []):
        for model in cfg.get("models", []):
            try:
                report = breakdown._run_model(
                    model, ontology, target.get("uri"), target.get("user"),
                    target.get("password"), f"{prefix}{sf}", cases,
                    bool(cfg.get("reasoning_mode", True)))
                results.append({
                    "scale_factor": sf, "model": model,
                    "accuracy": report["accuracy"], "correct": report["correct"],
                    "total": report["total"], "errors": report["errors"],
                    "latency_ms": report["latency_ms"], "stages": report["stages"],
                    "per_case": [{"id": c["id"], "correct": c["correct"],
                                  "intent": c["s1_intent"],
                                  "db_hits": (c.get("s4_plan") or {}).get("db_hits"),
                                  "sargable": (c.get("s4_plan") or {}).get("sargable")}
                                 for c in report["cases"]],
                })
            except Exception as exc:
                results.append({"scale_factor": sf, "model": model,
                                "error": f"{type(exc).__name__}: {exc}"[:300]})
    return {"runs": results, "precedence": os.environ["SEOCHO_QUERY_PRECEDENCE"]}


def stage_infra_sweep(spec: Dict[str, Any]) -> Dict[str, Any]:
    cfg = spec.get("infra_sweep") or {}
    if not cfg.get("enabled", False):
        return {"skipped": "disabled"}
    target = spec.get("target") or {}
    protocol_cfg = spec.get("protocol") or {}
    out_root = Path((spec.get("data") or {}).get("out_root", "outputs/finbench"))
    database = cfg.get("database", "finbenchsf1000")
    sf = re.sub(r"\D", "", database) or "1000"
    cliff = _load_module("finbench_cliff", "cliff_pagecache.py")
    override = out_root / "_bench-infra-override.yml"
    override.parent.mkdir(parents=True, exist_ok=True)
    compose = ["docker-compose.yml", "docker-compose.finbench.yml"]
    steps = []
    for profile in cfg.get("profiles", []):
        entry = {k: str(v) for k, v in profile.items() if k != "name"}
        if str(entry.get("mem", "")).lower() in ("none", "", "unlimited"):
            entry.pop("mem", None)
        print(f"[bench] infra profile {profile.get('name')} — restarting", flush=True)
        cliff._restart_with_profile(entry, container="graphrag-neo4j",
                                    compose=compose, override_path=override)
        if not cliff._await_database(target.get("uri"), target.get("user"),
                                    target.get("password"), database):
            steps.append({"profile": profile, "error": "database_not_online"})
            continue
        step = cliff.measure_step(
            entry, src=out_root / f"sf{sf}", database=database,
            uri=target.get("uri"), user=target.get("user"),
            password=target.get("password"),
            repeats=int(protocol_cfg.get("repeats", 10)), container="graphrag-neo4j")
        step["profile_name"] = profile.get("name")
        steps.append(step)
    return {"steps": steps}


_STAGE_FUNCS = {
    "data": stage_data,
    "load": stage_load,
    "graph_layer": stage_graph_layer,
    "agent_layer": stage_agent_layer,
    "infra_sweep": stage_infra_sweep,
}


def _markdown(report: Dict[str, Any]) -> str:
    spec = report["spec"]
    lines = [f"# {spec.get('name', 'finbench')} — benchmark report", "",
             f"tag `{(spec.get('output') or {}).get('tag', '-')}`", "",
             "## Parameters as set", "",
             "Every value below was read from the spec file; the raw spec is in the JSON",
             "under `spec` so a number can always be traced to what produced it.", "",
             "| section | parameter | value |", "|---|---|---|"]
    for section in ("data", "load", "protocol", "graph_layer", "agent_layer", "infra_sweep"):
        block = spec.get(section) or {}
        for key, value in block.items():
            if key == "profiles":
                value = "; ".join(
                    f"{p.get('name')}(mem={p.get('mem')},heap={p.get('heap')},cache={p.get('cache')})"
                    for p in value)
            lines.append(f"| {section} | {key} | `{value}` |")

    data = report["stages"].get("data") or {}
    if data.get("snapshots"):
        lines += ["", "## Data generated", "", "| SF | nodes | transfers | reused |",
                  "|---|---|---|---|"]
        for snap in data["snapshots"]:
            counts = snap.get("counts") or {}
            nodes = sum(counts.get(k, 0) for k in ("person", "company", "account", "loan", "channel"))
            lines.append(f"| {snap['scale_factor']} | {nodes:,} | "
                         f"{counts.get('transfer', 0):,} | {snap.get('reused')} |")

    load = report["stages"].get("load") or {}
    if load.get("loads"):
        lines += ["", "## Load", "", "| SF | database | relationships | import s | rel/s |",
                  "|---|---|---|---|---|"]
        for row in load["loads"]:
            if row.get("error"):
                lines.append(f"| {row['scale_factor']} | {row['database']} | — | — | {row['error'][:40]} |")
                continue
            lines.append(f"| {row['scale_factor']} | {row['database']} | "
                         f"{(row.get('counts') or {}).get('relationships', 0):,} | "
                         f"{row.get('import_seconds')} | "
                         f"{row.get('relationships_per_second', 0):,} |")

    graph = report["stages"].get("graph_layer") or {}
    if graph.get("scales"):
        lines += ["", "## Graph layer — plan shape is the variable", "",
                  "Both shapes return the same answer, so accuracy cannot separate them.", "",
                  "| SF | scenario | tuned hits | tuned p50 | naive hits | naive p50 | cost multiple |",
                  "|---|---|---|---|---|---|---|"]
        for entry in graph["scales"]:
            if entry.get("error"):
                lines.append(f"| {entry['scale_factor']} | — | — | — | — | — | {entry['error'][:40]} |")
                continue
            for sc in entry["scenarios"]:
                lines.append(f"| {entry['scale_factor']} | {sc['name']} | {sc['tuned']['db_hits']} | "
                             f"{sc['tuned']['latency_ms']:.1f}ms | {sc['naive']['db_hits']:,} | "
                             f"{sc['naive']['latency_ms']:.0f}ms | {sc['naive_cost_multiple']}x |")

    agent = report["stages"].get("agent_layer") or {}
    if agent.get("runs"):
        lines += ["", f"## Agent layer (precedence `{agent.get('precedence')}`)", "",
                  "| SF | model | accuracy | S4 sargable | dbHits | p50 ms |",
                  "|---|---|---|---|---|---|"]
        for run in agent["runs"]:
            if run.get("error"):
                lines.append(f"| {run['scale_factor']} | {run['model']} | — | — | — | {run['error'][:40]} |")
                continue
            st = run.get("stages") or {}
            sarg = st.get("s4_sargable_rate")
            lines.append(f"| {run['scale_factor']} | {run['model']} | {run['accuracy']:.0%} | "
                         f"{'n/a' if sarg is None else f'{sarg:.0%}'} | "
                         f"{st.get('s4_db_hits_total', 0):,} | {run['latency_ms']['p50']:.0f} |")

    infra = report["stages"].get("infra_sweep") or {}
    if infra.get("steps"):
        lines += ["", "## Infrastructure sweep — where plans stop being flat", "",
                  "| profile | mem | heap | cache | scenario | tuned hits | tuned p50 | naive p50 |",
                  "|---|---|---|---|---|---|---|---|"]
        for step in infra["steps"]:
            prof = step.get("profile", {})
            name = step.get("profile_name") or "-"
            if step.get("error"):
                lines.append(f"| {name} | {prof.get('mem','unlimited')} | {prof.get('heap','-')} | "
                             f"{prof.get('cache','-')} | — | — | — | {step['error']} |")
                continue
            for sc in step["scenarios"]:
                lines.append(
                    f"| {name} | {prof.get('mem','unlimited')} | {prof.get('heap','-')} | "
                    f"{step.get('effective_pagecache') or prof.get('cache')} | {sc['name']} | "
                    f"{sc['tuned']['db_hits']} | {sc['tuned']['latency_ms']:.1f}ms | "
                    f"{sc['naive']['latency_ms']:.0f}ms |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--only", action="append", choices=STAGES,
                        help="run only these stages (repeatable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve and print the spec without executing")
    args = parser.parse_args()

    spec = load_spec(args.spec)
    selected = args.only or [s for s in STAGES if (spec.get(s) or {}).get("enabled", True)
                             or s in ("data", "load")]

    if args.dry_run:
        print(json.dumps({"spec": _redacted(spec), "stages_to_run": selected}, indent=2))
        return

    started = time.time()
    stages: Dict[str, Any] = {}
    for stage in STAGES:
        if stage not in selected:
            stages[stage] = {"skipped": "not selected"}
            continue
        print(f"[bench] stage {stage}", flush=True)
        try:
            stages[stage] = _STAGE_FUNCS[stage](spec)
        except Exception as exc:
            stages[stage] = {"error": f"{type(exc).__name__}: {exc}"[:400]}
            print(f"[bench] stage {stage} failed: {exc}", flush=True)

    report = {
        "schema_version": "seocho.finbench.bench.v1",
        "spec_path": str(args.spec),
        "spec": _redacted(spec),
        "elapsed_seconds": round(time.time() - started, 1),
        "stages": stages,
    }
    out_cfg = spec.get("output") or {}
    out_dir = Path(out_cfg.get("dir", "outputs/finbench/bench"))
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = str(out_cfg.get("tag", "manual"))
    (out_dir / f"{tag}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    markdown = _markdown(report)
    (out_dir / f"{tag}.md").write_text(markdown)
    print(markdown)
    print(f"[bench] wrote {out_dir / (tag + '.json')} and .md")


if __name__ == "__main__":
    main()
