"""File-backed E2E receipts and presentation, independent of live SDK clients."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .run_outcomes import summarize_outcome


@dataclass(slots=True)
class RunReport:
    """Aggregated outcome; completion never implies answer correctness."""

    payload: dict[str, Any] = field(default_factory=dict)
    report_json: Path | None = None
    report_md: Path | None = None

    @property
    def ok(self) -> bool:
        outcome = self.payload.get("outcome") or summarize_outcome(self.payload)
        return outcome["status"] == "completed"


class ReportStore(Protocol):
    """Internal persistence boundary; implementations preserve prior runs."""

    def write(self, payload: dict[str, Any]) -> RunReport: ...


class FileReportStore:
    """Own a fresh report target, atomically replacing only our checkpoints."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.json_path = directory / "report.json"
        if (directory / "report.md").exists():
            raise FileExistsError(
                f"Report already exists: {directory}; choose a fresh output directory"
            )
        # Exclusive creation arbitrates competing writers before any live work.
        with self.json_path.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "schema_version": "seocho.run_report.v2",
                    "outcome": {"status": "running"},
                },
                stream,
            )

    def write(self, payload: dict[str, Any]) -> RunReport:
        text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
        md_path = self.directory / "report.md"
        for path, content in (
            (self.json_path, text),
            (md_path, render_report_md(payload)),
        ):
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        return RunReport(payload=payload, report_json=self.json_path, report_md=md_path)


def _md_cell(value: Any) -> str:
    return (
        str(value if value is not None else "").replace("\n", " ").replace("|", "\\|")
    )


def _short(value: Any, *, limit: int = 60) -> str:
    text = str(value if value is not None else "")
    return text if len(text) <= limit else text[:limit] + "..."


def _join_or_dash(values: Any) -> str:
    items = (
        [str(item) for item in values if str(item).strip()]
        if isinstance(values, (list, tuple))
        else []
    )
    return ", ".join(items) if items else "-"


def _format_triple(triple: dict[str, Any]) -> str:
    source = str(triple.get("source", "") or "").strip() or "?"
    relation = str(triple.get("relation", "") or "").strip() or "RELATED_TO"
    target = str(triple.get("target", "") or "").strip() or "?"
    return f"`{source}` -[{relation}]-> `{target}`"


def render_report_md(payload: dict[str, Any]) -> str:
    run = payload.get("run", {})
    indexing = payload.get("indexing", {})
    queries = payload.get("queries", [])
    lines = [
        f"# SEOCHO run: {run.get('name', '')}",
        "",
        f"- status: {payload.get('outcome', {}).get('status', 'unknown')}",
        f"- active stage: {payload.get('active_stage', 'unknown')}",
        f"- started: {run.get('started_at', '')}",
        f"- models: indexing={run.get('models', {}).get('indexing', '')}, "
        f"query={run.get('models', {}).get('query', '')}",
        f"- enforcement: {run.get('enforcement', '')}",
        f"- projection governance: {run.get('governance_mode', 'direct')}",
        f"- graph: {run.get('graph', 'DozerDB/Neo4j Bolt URI required')} (database={run.get('database', '')})",
        "",
        "## Indexing",
        "",
        f"- files: {indexing.get('files_indexed', 0)} indexed, "
        f"{indexing.get('files_unchanged', 0)} unchanged, "
        f"{indexing.get('files_failed', 0)} failed",
        f"- graph: {indexing.get('total_nodes', 0)} nodes, "
        f"{indexing.get('total_relationships', 0)} relationships",
        f"- validation errors: {indexing.get('validation_errors_count', 0)}",
        "",
    ]
    diagnostics = payload.get("diagnostics", [])
    if diagnostics:
        lines += ["## Diagnostics", ""]
        for item in diagnostics:
            lines += [
                f"- **{item['stage']} / {item['code']}** {item.get('item_id', '')}: {item['message']}",
                f"  Next: {item['action']}",
            ]
        lines.append("")
    if queries:
        lines += [
            "## Queries",
            "",
            "| # | question | answered | support | missing | evidence | latency |",
            "|---|---|---|---|---|---|---|",
        ]
        for item in queries:
            if item.get("error"):
                answered = "error"
            elif item.get("empty"):
                answered = "empty"
            else:
                answered = "yes"
            question_text = _short(item.get("question", ""))
            support = item.get("support_status", "-")
            missing = _join_or_dash(item.get("missing_slots", []))
            evidence = item.get("selected_triple_count", "-")
            lines.append(
                f"| {_md_cell(item.get('id', ''))} | {_md_cell(question_text)} | {answered} | "
                f"{_md_cell(support)} | {_md_cell(missing)} | {_md_cell(evidence)} | "
                f"{_md_cell(item.get('latency_s', ''))}s |"
            )
        lines.append("")
        for item in queries:
            lines += [f"### Q{item.get('id', '')}: {item.get('question', '')}", ""]
            if item.get("expect"):
                lines += [f"**Expected:** {item['expect']}", ""]
            if item.get("error"):
                lines += [f"**Error:** {item['error']}", ""]
            else:
                lines += [str(item.get("answer", "")) or "_(empty answer)_", ""]
                evidence = item.get("evidence_bundle") or {}
                if evidence or item.get("support_assessment"):
                    coverage = item.get("coverage", "-")
                    lines += [
                        f"**Evidence:** intent={item.get('intent_id', '-')}, "
                        f"support={item.get('support_status', '-')}, coverage={coverage}",
                        "",
                    ]
                if item.get("missing_slots"):
                    lines += [
                        f"**Missing slots:** {_join_or_dash(item.get('missing_slots'))}",
                        "",
                    ]
                triples = (
                    evidence.get("selected_triples", [])
                    if isinstance(evidence, dict)
                    else []
                )
                if triples:
                    lines.append("**Selected triples:**")
                    lines.append("")
                    for triple in triples[:5]:
                        if isinstance(triple, dict):
                            lines.append(f"- {_format_triple(triple)}")
                    if len(triples) > 5:
                        lines.append(f"- ... {len(triples) - 5} more")
                    lines.append("")
    else:
        description = "No query results recorded. Check the selected phase, status and diagnostics."
        if payload.get("outcome", {}).get("status") == "completed" and (
            run.get("only") == "index" or run.get("question_count") == 0
        ):
            description = "Index-only run (query phase not requested)."
        lines += ["## Queries", "", description, ""]
    scorecard = payload.get("agent_scorecard", {})
    if scorecard:
        agent = scorecard.get("agent", {})
        lines += [
            "## Agent semantic scorecard",
            "",
            f"- evidence coverage: {agent.get('mean_evidence_coverage')}",
            f"- supported rate: {agent.get('supported_rate')}",
            f"- missing slots per question: {agent.get('missing_slots_per_question')}",
            f"- reference containment: {agent.get('reference_contains_rate')}",
            "- interpretation: compare matched runs before claiming RDF-governance lift.",
            "",
        ]
    receipt = payload.get("reproducibility", {})
    if receipt:
        lines += [
            "## Reproducibility",
            "",
            f"- source: {receipt.get('source', {}).get('git_revision') or 'unavailable'}",
            f"- input files: {receipt.get('input_files')}",
            "- input/settings fingerprints: see report.json / reproducibility.conditions",
            *[f"- evidence gap: {gap}" for gap in receipt.get("gaps", [])],
            *[f"- limitation: {gap}" for gap in receipt.get("limitations", [])],
            "",
        ]
    return "\n".join(lines)
