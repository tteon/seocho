"""The ``seocho ont`` command group — WP-O shapes-as-source pipeline.

Distinct from the legacy ``seocho ontology`` group on purpose: ``ontology``
operates on SEOCHO ontology definitions (JSON-LD/YAML) for governance and
review; ``ont`` treats SHACL ``shapes/*.ttl`` as the cache layer's source of
truth — canonical formatting, derived-artifact builds, a lockfile whose
``active_hash`` is the system-wide invalidation token, and blast-radius
estimates for proposed changes. Second group registered through the
CommandGroup seam; requires the ``seocho[ontology]`` extra (rdflib).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "ont", help="SHACL shapes-as-source pipeline (fmt/lint/build/verify/blast-radius)")
    ont = parser.add_subparsers(dest="ont_command", required=True)

    fmt = ont.add_parser("fmt", help="Rewrite shapes into canonical Turtle")
    fmt.add_argument("paths", nargs="+", help="Shape files or directories")
    fmt.add_argument("--check", action="store_true",
                     help="Exit 1 if any file is not canonical; write nothing")

    lint = ont.add_parser("lint", help="Shape sanity: targetClass, sh:path, canonical form")
    lint.add_argument("shapes", help="Shapes directory")
    lint.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    build = ont.add_parser("build", help="Derive vocab/path-index/address-space + lockfile")
    build.add_argument("shapes", help="Shapes directory")
    build.add_argument("--out", default="build/ontology", help="Artifact output directory")
    build.add_argument("--lock", default="seocho.lock", help="Lockfile path")
    build.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    verify = ont.add_parser("verify", help="Recompute and compare against the lockfile")
    verify.add_argument("shapes", help="Shapes directory")
    verify.add_argument("--lock", default="seocho.lock", help="Lockfile path")
    verify.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    blast = ont.add_parser("blast-radius",
                           help="Address-level impact of a proposed shapes change")
    blast.add_argument("shapes", help="Current shapes directory")
    blast.add_argument("--change", required=True,
                       help="Proposed shapes directory to compare against")
    blast.add_argument("--json", dest="output_json", action="store_true", help="JSON output")


def handle(args: argparse.Namespace) -> int:
    from .. import ontology_pipeline as pipeline

    if args.ont_command == "fmt":
        files: list[Path] = []
        for raw in args.paths:
            path = Path(raw)
            files.extend(pipeline.shape_files(path) if path.is_dir() else [path])
        dirty = 0
        for path in files:
            graph = pipeline.load_graph(path)
            formatted = pipeline.canonical_turtle(graph)
            if path.read_text(encoding="utf-8") != formatted:
                dirty += 1
                if args.check:
                    print(f"would reformat {path}")
                else:
                    path.write_text(formatted, encoding="utf-8")
                    print(f"reformatted {path}")
        if args.check and dirty:
            print(f"{dirty} file(s) not canonical", file=sys.stderr)
            return 1
        if not dirty:
            print(f"{len(files)} file(s) already canonical")
        return 0

    if args.ont_command == "lint":
        findings = pipeline.lint(Path(args.shapes))
        if getattr(args, "output_json", False):
            print(json.dumps({"findings": findings}, indent=2))
        else:
            for finding in findings:
                print(finding)
            if not findings:
                print("shapes clean")
        return 1 if any(f.startswith("ERROR") for f in findings) else 0

    if args.ont_command == "build":
        result = pipeline.build(Path(args.shapes))
        pipeline.write_build(result, Path(args.out), Path(args.lock))
        if getattr(args, "output_json", False):
            print(json.dumps(result.lock, indent=2, sort_keys=True))
        else:
            print(f"built {len(result.artifacts)} artifacts -> {args.out}")
            print(f"lockfile -> {args.lock}")
            print(f"active_hash {result.active_hash}")
        return 0

    if args.ont_command == "verify":
        ok, problems = pipeline.verify(Path(args.shapes), Path(args.lock))
        if getattr(args, "output_json", False):
            print(json.dumps({"ok": ok, "problems": problems}, indent=2))
        else:
            print("lock verified" if ok else "LOCK DRIFT:")
            for problem in problems:
                print(f"  {problem}")
        return 0 if ok else 1

    if args.ont_command == "blast-radius":
        report = pipeline.blast_radius(Path(args.shapes), Path(args.change))
        if getattr(args, "output_json", False):
            print(json.dumps(report, indent=2))
        else:
            print(f"address-space share touched: {report['address_space_share_touched']}")
            for key in ("addresses_added", "addresses_removed", "addresses_changed",
                        "artifacts_changed"):
                if report[key]:
                    print(f"{key}: {', '.join(report[key])}")
            print(report["note"])
        return 0

    raise ValueError(f"unknown ont command: {args.ont_command}")
