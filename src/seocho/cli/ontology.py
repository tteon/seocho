"""The ``seocho ontology`` command group.

First group migrated out of the legacy monolith onto the CommandGroup
registry (seocho-vh0): parser construction and handling live together here,
and the group plugs in through ``register_group`` instead of the four legacy
edit sites. New groups (``ont``, ``policy``) follow this file's shape.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..exceptions import SeochoError


def register(subparsers) -> None:
    ontology_parser = subparsers.add_parser("ontology", help="Offline ontology governance helpers")
    ontology_subparsers = ontology_parser.add_subparsers(dest="ontology_command", required=True)

    ontology_check_parser = ontology_subparsers.add_parser("check", help="Validate one ontology definition")
    ontology_check_parser.add_argument("--schema", required=True, help="Ontology file (JSON-LD, YAML, or TTL)")
    ontology_check_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_export_parser = ontology_subparsers.add_parser("export", help="Export ontology-derived artifacts")
    ontology_export_parser.add_argument("--schema", required=True, help="Ontology file (JSON-LD, YAML, or TTL)")
    ontology_export_parser.add_argument(
        "--format",
        required=True,
        choices=["jsonld", "yaml", "dict", "shacl"],
        help="Output artifact format",
    )
    ontology_export_parser.add_argument("--output", default=None, help="Optional output file path")
    ontology_export_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_diff_parser = ontology_subparsers.add_parser("diff", help="Diff two ontology definitions")
    ontology_diff_parser.add_argument("--left", required=True, help="Left ontology file")
    ontology_diff_parser.add_argument("--right", required=True, help="Right ontology file")
    ontology_diff_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_report_parser = ontology_subparsers.add_parser(
        "report",
        help="Compile a promotion-oriented ontology governance report",
    )
    ontology_report_parser.add_argument("--schema", required=True, help="Ontology file (JSON-LD, YAML, or TTL)")
    ontology_report_parser.add_argument("--artifact-name", default=None, help="Optional semantic artifact draft name")
    ontology_report_parser.add_argument("--output", default=None, help="Optional output JSON file path")
    ontology_report_parser.add_argument(
        "--skip-owl-inspection",
        action="store_true",
        help="Skip optional Owlready2 offline inspection",
    )
    ontology_report_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_inspect_parser = ontology_subparsers.add_parser(
        "inspect-owl",
        help="Inspect an OWL ontology with Owlready2 (optional offline dependency)",
    )
    ontology_inspect_parser.add_argument("--source", required=True, help="OWL file path or URI")
    ontology_inspect_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_review_parser = ontology_subparsers.add_parser(
        "review",
        help="Ambiguity review loop: quarantine OOV entities, cluster them, and map them back into the taxonomy",
    )
    ontology_review_parser.add_argument(
        "review_action",
        choices=["ingest", "clusters", "export-spec", "apply"],
        help="ingest: detect+quarantine from an extracted-graph JSON; clusters: list ranked quarantine; "
             "export-spec: write a starter mapping-spec YAML; apply: apply a mapping-spec to an ontology",
    )
    ontology_review_parser.add_argument("--quarantine", default=".seocho_quarantine.jsonl", help="Quarantine JSONL path")
    ontology_review_parser.add_argument("--schema", default=None, help="Ontology file (for ingest/export-spec/apply)")
    ontology_review_parser.add_argument("--graph", default=None, help="Extracted-graph JSON (for ingest)")
    ontology_review_parser.add_argument("--spec", default=None, help="Mapping-spec YAML (for apply)")
    ontology_review_parser.add_argument("--output", default=None, help="Output path (export-spec / apply)")
    ontology_review_parser.add_argument("--workspace", default="", help="workspace_id to stamp on quarantined items")
    ontology_review_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_datahub_parser = ontology_subparsers.add_parser(
        "datahub",
        help="Export an ontology to a DataHub Business Glossary (MCP payloads; optional live emit)",
    )
    ontology_datahub_parser.add_argument("--schema", required=True, help="Ontology file (JSON-LD, YAML, or TTL)")
    ontology_datahub_parser.add_argument("--output", default=None, help="Write MCP JSON to this path (dry-run)")
    ontology_datahub_parser.add_argument("--gms", default=None, help="DataHub GMS server URL (for live emit)")
    ontology_datahub_parser.add_argument("--token", default=None, help="DataHub access token (for live emit)")
    ontology_datahub_parser.add_argument("--emit", action="store_true", help="Actually emit to --gms (default: dry-run)")
    ontology_datahub_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_select_parser = ontology_subparsers.add_parser(
        "select-guardrail",
        help="Domain-adaptively pick the best guardrail ontology for a corpus (ADR-0122)",
    )
    ontology_select_parser.add_argument(
        "--candidates", required=True,
        help="Comma-separated name=path pairs, e.g. lean=fibo_minus.jsonld,rich=fibo_plus.jsonld",
    )
    ontology_select_parser.add_argument(
        "--corpus", required=True,
        help="Corpus profile JSON (label->freq, a CorpusProfile dict, or an experiment record)",
    )
    ontology_select_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_dhapply_parser = ontology_subparsers.add_parser(
        "datahub-apply",
        help="Round-trip approved DataHub glossary terms back into the ontology (close the review loop)",
    )
    ontology_dhapply_parser.add_argument("--schema", required=True, help="Ontology file (JSON-LD, YAML, or TTL)")
    ontology_dhapply_parser.add_argument("--terms", required=True, help="Reviewed glossary terms JSON (list of records)")
    ontology_dhapply_parser.add_argument("--status", default="APPROVED", help="Only apply terms with this review status")
    ontology_dhapply_parser.add_argument("--output", default=None, help="Write the new ontology JSON-LD here")
    ontology_dhapply_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_dhqueue_parser = ontology_subparsers.add_parser(
        "datahub-queue",
        help="Surface the ambiguity review queue in DataHub as PROPOSED glossary terms for non-developer review",
    )
    ontology_dhqueue_parser.add_argument("--schema", required=True, help="Ontology file (for the package id)")
    ontology_dhqueue_parser.add_argument("--quarantine", default=".seocho_quarantine.jsonl", help="Quarantine JSONL path")
    ontology_dhqueue_parser.add_argument("--gms", default=None, help="DataHub GMS server URL (for live emit)")
    ontology_dhqueue_parser.add_argument("--token", default=None, help="DataHub access token (for live emit)")
    ontology_dhqueue_parser.add_argument("--emit", action="store_true", help="Actually emit to --gms (default: dry-run)")
    ontology_dhqueue_parser.add_argument("--output", default=None, help="Write MCP JSON to this path (dry-run)")
    ontology_dhqueue_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_eval_answers_parser = ontology_subparsers.add_parser(
        "eval-answers",
        help="Measure answer accuracy of an ontology guardrail over a gold QA set (ADR-0124/0125)",
    )
    ontology_eval_answers_parser.add_argument("--schema", required=True, help="Ontology file (JSON-LD, YAML, or TTL)")
    ontology_eval_answers_parser.add_argument(
        "--cases", required=True,
        help="Gold QA cases JSON: list of {question, gold_answer, context, category, case_id}",
    )
    ontology_eval_answers_parser.add_argument("--provider", default="mara", help="LLM provider preset (default: mara)")
    ontology_eval_answers_parser.add_argument("--model", default=None, help="Model override (default: provider default)")
    ontology_eval_answers_parser.add_argument("--workers", type=int, default=6, help="Concurrent workers (default: 6)")
    ontology_eval_answers_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")


    ontology_import_parser = ontology_subparsers.add_parser(
        "import",
        help="Convert an external schema (Arrows.app JSON, Cypher DDL, native "
             "JSON/YAML) into a draft ontology document — never persists",
    )
    ontology_import_parser.add_argument("source", help="Path to the schema file")
    ontology_import_parser.add_argument(
        "--format", default="auto",
        help="arrows | cypher | native | auto (default: detect from content)")
    ontology_import_parser.add_argument(
        "--output", default=None,
        help="Write the draft document here (YAML); default prints to stdout")
    ontology_import_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="JSON output")


    ontology_subparsers.add_parser(
        "templates", help="List curated starter ontologies for `ontology clone`")

    ontology_clone_parser = ontology_subparsers.add_parser(
        "clone", help="Copy a curated starter ontology as an editable draft — "
                      "prints unless --output is given",
    )
    ontology_clone_parser.add_argument("template", help="Template name (see `ontology templates`)")
    ontology_clone_parser.add_argument("--output", default=None, help="Write the draft YAML here")
    ontology_clone_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="JSON output")


def handle(args: argparse.Namespace) -> int:
    from ..ontology_governance import (
        build_ontology_governance_report,
        check_ontology,
        diff_ontologies,
        export_ontology_payload,
        inspect_owl_ontology,
        load_ontology_file,
    )
    import yaml

    if args.ontology_command == "check":
        ontology = load_ontology_file(args.schema)
        result = check_ontology(ontology)
        if getattr(args, "output_json", False):
            print(json.dumps(result.to_dict(), indent=2))
        else:
            status = "ok" if result.ok else "invalid"
            print(f"ontology {status}: {result.ontology_name}@{result.ontology_version}")
            print(
                f"  package_id={result.package_id} "
                f"graph_model={result.stats['graph_model']} "
                f"nodes={result.stats['node_count']} relationships={result.stats['relationship_count']}"
            )
            for item in result.errors:
                print(f"error: {item}")
            for item in result.warnings:
                print(f"warning: {item}")
        return 0 if result.ok else 1

    if args.ontology_command == "export":
        ontology = load_ontology_file(args.schema)
        payload = export_ontology_payload(ontology, output_format=args.format)

        if args.format == "yaml":
            rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        else:
            rendered = json.dumps(payload, indent=2, ensure_ascii=False)

        if args.output:
            Path(args.output).write_text(rendered + ("" if rendered.endswith("\n") else "\n"), encoding="utf-8")

        if getattr(args, "output_json", False):
            print(json.dumps({"format": args.format, "output": args.output, "payload": payload}, indent=2, ensure_ascii=False))
        elif args.output:
            print(f"exported {args.format} to {args.output}")
        else:
            print(rendered)
        return 0

    if args.ontology_command == "diff":
        left = load_ontology_file(args.left)
        right = load_ontology_file(args.right)
        diff = diff_ontologies(left, right)
        if getattr(args, "output_json", False):
            print(json.dumps(diff.to_dict(), indent=2))
        else:
            print(f"diff {diff.left_name} -> {diff.right_name}")
            print(
                f"  package_id={diff.package_id} "
                f"recommended_bump={diff.recommended_bump} "
                f"requires_migration={'yes' if diff.requires_migration else 'no'}"
            )
            for section_name, section_changes in diff.changes.items():
                for change_kind, values in section_changes.items():
                    if values:
                        print(f"{section_name} {change_kind}: {', '.join(values)}")
            for warning in diff.migration_warnings:
                print(f"warning: {warning}")
        return 0

    if args.ontology_command == "report":
        report = build_ontology_governance_report(
            args.schema,
            artifact_name=args.artifact_name,
            include_owl_inspection=not args.skip_owl_inspection,
        )
        payload = report.to_dict()
        rendered = json.dumps(payload, indent=2, ensure_ascii=False)
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        if getattr(args, "output_json", False):
            print(rendered)
        else:
            descriptor = payload.get("context_descriptor", {})
            print(f"ontology report: {payload['source']}")
            print(
                "  "
                f"package_id={descriptor.get('ontology_id', '')} "
                f"version={descriptor.get('ontology_version', '')} "
                f"context_hash={descriptor.get('context_hash', '')}"
            )
            print(
                "  "
                f"shapes={payload['shacl_export']['stats'].get('node_shape_count', 0)} "
                f"properties={payload['shacl_export']['stats'].get('property_shape_count', 0)} "
                f"sample_data_ok={'yes' if payload['sample_data_validation'].get('ok') else 'no'}"
            )
            for note in payload.get("notes", []):
                print(f"note: {note}")
            if args.output:
                print(f"written: {args.output}")
        return 0 if report.ok else 1

    if args.ontology_command == "inspect-owl":
        inspection = inspect_owl_ontology(args.source)
        if getattr(args, "output_json", False):
            print(json.dumps(inspection.to_dict(), indent=2))
        else:
            if not inspection.available:
                print(inspection.error or "owlready2 unavailable")
                return 1
            if inspection.error:
                print(f"owlready2 inspection failed: {inspection.error}")
                return 1
            print(f"owlready2 source: {inspection.source}")
            print(
                "  "
                f"classes={inspection.stats.get('class_count', 0)} "
                f"properties={inspection.stats.get('property_count', 0)} "
                f"individuals={inspection.stats.get('individual_count', 0)} "
                f"imports={inspection.stats.get('import_count', 0)}"
            )
        return 0 if inspection.available and inspection.error is None else 1

    if args.ontology_command == "review":
        from ..ontology import Ontology
        from ..ontology_ambiguity import (
            AmbiguityQuarantine,
            apply_mapping_spec,
            detect_ambiguities,
            load_mapping_spec,
            starter_mapping_spec,
        )

        q = AmbiguityQuarantine(args.quarantine)

        if args.review_action == "ingest":
            if not args.schema or not args.graph:
                raise SeochoError("review ingest requires --schema and --graph")
            ontology = Ontology.load(args.schema)
            with open(args.graph, "r", encoding="utf-8") as f:
                graph = json.load(f)
            found = detect_ambiguities(graph, ontology, source=args.graph, workspace_id=args.workspace)
            n = q.add(found)
            if getattr(args, "output_json", False):
                print(json.dumps({"ingested": n, "items": [f.to_dict() for f in found]}, indent=2, ensure_ascii=False))
            else:
                print(f"quarantined {n} ambiguous mention(s) → {args.quarantine}")
            return 0

        if args.review_action == "clusters":
            clusters = q.clusters()
            if getattr(args, "output_json", False):
                print(json.dumps(clusters, indent=2, ensure_ascii=False))
            else:
                if not clusters:
                    print("quarantine empty")
                for c in clusters:
                    print(f"  {c['frequency']:4d}×  {c['surface']:30s} signals={c['signals']} "
                          f"candidates={c['candidate_labels']}")
            return 0

        if args.review_action == "export-spec":
            if not args.schema:
                raise SeochoError("review export-spec requires --schema")
            import yaml
            ontology = Ontology.load(args.schema)
            spec = starter_mapping_spec(q.clusters(), ontology)
            text = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)
            if args.output:
                Path(args.output).write_text(text, encoding="utf-8")
                print(f"wrote starter mapping-spec → {args.output} ({len(spec['mappings'])} mappings)")
            else:
                print(text)
            return 0

        if args.review_action == "apply":
            if not args.schema or not args.spec:
                raise SeochoError("review apply requires --schema and --spec")
            ontology = Ontology.load(args.schema)
            spec = load_mapping_spec(args.spec)
            new_onto = apply_mapping_spec(ontology, spec)
            payload = new_onto.to_jsonld()
            if args.output:
                Path(args.output).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"applied {len(spec.get('mappings', []))} mapping(s): "
                      f"{ontology.version} → {new_onto.version}, {len(ontology.nodes)} → {len(new_onto.nodes)} classes "
                      f"→ {args.output}")
            else:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        raise SeochoError(f"Unknown review action: {args.review_action}")

    if args.ontology_command == "datahub":
        from ..ontology import Ontology
        from ..datahub_export import emit_to_datahub, glossary_mcps_to_json, ontology_to_glossary_mcps

        ontology = Ontology.load(args.schema)
        mcps = ontology_to_glossary_mcps(ontology)
        if args.emit:
            result = emit_to_datahub(mcps, gms_server=args.gms, token=args.token, dry_run=False)
            if getattr(args, "output_json", False):
                print(json.dumps({k: v for k, v in result.items() if k != "mcps"}, indent=2, ensure_ascii=False))
            else:
                print(f"datahub emit: mode={result['mode']} "
                      + (f"sent={result.get('sent')}" if result.get("emitted") else f"({result.get('error','dry-run')})"))
            return 0 if result.get("emitted") or result["mode"] == "dry_run" else 1
        text = glossary_mcps_to_json(mcps)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
            from ..datahub_export import export_summary
            s = export_summary(mcps)
            print(f"wrote {s['mcp_count']} MCP(s) → {args.output} "
                  f"({s['glossary_terms']} terms, {s['glossary_nodes']} nodes, {s['is_a_edges']} is-a edges)")
        else:
            print(text)
        return 0

    if args.ontology_command == "datahub-apply":
        from ..ontology import Ontology
        from ..datahub_export import datahub_glossary_to_mapping_spec
        from ..ontology_ambiguity import apply_mapping_spec

        ontology = Ontology.load(args.schema)
        with open(args.terms, "r", encoding="utf-8") as f:
            term_records = json.load(f)
        spec = datahub_glossary_to_mapping_spec(term_records, only_status=args.status, ontology_name=ontology.name)
        new_onto = apply_mapping_spec(ontology, spec)
        payload = new_onto.to_jsonld()
        if args.output:
            Path(args.output).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"applied {len(spec['mappings'])} approved term(s): {ontology.version} → {new_onto.version}, "
                  f"{len(ontology.nodes)} → {len(new_onto.nodes)} classes → {args.output}")
        else:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.ontology_command == "datahub-queue":
        from ..ontology import Ontology
        from ..datahub_export import ambiguity_clusters_to_glossary_proposals, emit_to_datahub
        from ..ontology_ambiguity import AmbiguityQuarantine

        ontology = Ontology.load(args.schema)
        package_id = ontology.package_id or ontology.name
        clusters = AmbiguityQuarantine(args.quarantine).clusters()
        mcps = ambiguity_clusters_to_glossary_proposals(clusters, package_id=package_id)
        result = emit_to_datahub(mcps, gms_server=args.gms, token=args.token,
                                 dry_run=not (args.emit and args.gms))
        n_terms = sum(1 for m in mcps if m["entityType"] == "glossaryTerm")
        if args.output:
            Path(args.output).write_text(json.dumps(mcps, indent=2, ensure_ascii=False), encoding="utf-8")
        if getattr(args, "output_json", False):
            print(json.dumps({"proposed_terms": n_terms, "mode": result["mode"],
                              "emitted": result["emitted"]}, indent=2, ensure_ascii=False))
        else:
            print(f"review queue: {n_terms} PROPOSED term(s) under '{package_id}.Proposed'  "
                  f"mode={result['mode']} emitted={result['emitted']}"
                  + ("  (add --gms URL --emit to publish to DataHub)" if not result["emitted"] else ""))
        return 0

    if args.ontology_command == "select-guardrail":
        from ..ontology import Ontology
        from ..guardrail_selector import load_corpus_profile, select_guardrail

        candidates = {}
        for pair in str(args.candidates).split(","):
            pair = pair.strip()
            if not pair:
                continue
            name, _, path = pair.partition("=")
            if not path:
                name, path = Path(name).stem, name
            candidates[name.strip()] = Ontology.load(path.strip())
        rec = select_guardrail(candidates, load_corpus_profile(args.corpus))
        if getattr(args, "output_json", False):
            print(json.dumps(rec.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(f"chosen: {rec.chosen}  (domain={rec.domain_kind}, numeric_intensity={rec.numeric_intensity})")
            print("  coverage: " + ", ".join(f"{n}={s['corpus_coverage']}" for n, s in rec.candidate_scores.items()))
            print(f"  {rec.rationale}")
            for a in rec.advisories:
                print(f"  · {a}")
        return 0

    if args.ontology_command == "eval-answers":
        from ..ontology import Ontology
        from ..evaluation import evaluate_answer_accuracy, load_answer_cases
        from ..store.llm import create_llm_backend

        ontology = Ontology.load(args.schema)
        cases = load_answer_cases(args.cases)
        backend = create_llm_backend(provider=args.provider, model=args.model)
        report = evaluate_answer_accuracy(backend, ontology, cases, model=args.model, workers=args.workers)
        if getattr(args, "output_json", False):
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(f"answer accuracy: {report.accuracy}  (scored={report.n_scored}, errors={report.errors})")
            for cat in sorted(report.by_category):
                print(f"  {cat or '(uncategorized)'}: {report.by_category[cat]} "
                      f"(n={report.by_category_n.get(cat, 0)})")
        return 0

    if args.ontology_command == "import":
        import yaml

        from ..ontology_import import import_document

        content = Path(args.source).read_text(encoding="utf-8")
        result = import_document(content, format=args.format)
        if getattr(args, "output_json", False):
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            if result.detected_format:
                print(f"format: {result.detected_format}")
            if result.suggested_name:
                print(f"suggested name: {result.suggested_name}")
            for warning in result.warnings:
                print(f"warning: {warning}")
            if result.document is not None:
                rendered = yaml.safe_dump(result.document, sort_keys=False,
                                          allow_unicode=True)
                if args.output:
                    Path(args.output).write_text(rendered, encoding="utf-8")
                    print(f"draft written to {args.output} — review, then validate "
                          f"with: seocho ontology check --schema {args.output}")
                else:
                    print(rendered)
        # A draft with no document is an unusable import; say so in the exit code.
        return 0 if result.document is not None else 1

    if args.ontology_command == "templates":
        from ..ontology_templates import list_templates

        for template in list_templates():
            print(f"{template['name']:22s} {template['description']}")
        return 0

    if args.ontology_command == "clone":
        import yaml

        from ..ontology_templates import load_template

        try:
            document = load_template(args.template)
        except KeyError as exc:
            raise SeochoError(str(exc)) from exc
        if getattr(args, "output_json", False):
            print(json.dumps(document, indent=2, ensure_ascii=False))
            return 0
        rendered = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
            print(f"template {args.template!r} written to {args.output} — edit, "
                  f"then validate with: seocho ontology check --schema {args.output}")
        else:
            print(rendered)
        return 0

    raise SeochoError(f"Unknown ontology command: {args.ontology_command}")
