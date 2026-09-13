"""Offline, matched comparison of saved run receipts; no backend dependencies."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping, Sequence

from .eval.semantic_scorecard import score_semantic_utility
from .run_evidence import SCHEMA
from .run_outcomes import query_state

CHANGEABLE = (
    "source",
    "models",
    "ontology",
    "indexing",
    "agent",
    "query",
    "vector",
    "governance",
    "execution",
    "graph",
    "environment",
    "provider_endpoints",
)
REQUIRED = frozenset((*CHANGEABLE, "documents", "questions"))


@dataclass(frozen=True, slots=True)
class MetricDelta:
    baseline: float | None
    candidate: float | None
    delta: float | None
    interpretation: str


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def load_report(path: Path) -> dict[str, Any]:
    target = path / "report.json" if path.is_dir() else path
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("run"), dict):
        raise ValueError(
            f"{target}: expected a SEOCHO run report object with run metadata"
        )
    if not isinstance(value.get("queries", []), list):
        raise ValueError(f"{target}: queries must be a list")
    if not all(isinstance(q, dict) for q in value.get("queries", [])):
        raise ValueError(f"{target}: each query must be an object")
    for key in ("indexing", "reproducibility", "outcome"):
        if key in value and not isinstance(value[key], dict):
            raise ValueError(f"{target}: {key} must be an object")
    return value


def _queries(
    report: Mapping[str, Any], label: str, issues: list[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in report.get("queries", []):
        identity = str(item.get("id", ""))
        if not identity or identity in result:
            issues.append(f"{label}: missing or duplicate question ID {identity!r}")
        result[identity] = item
    return result


def _metrics(report: Mapping[str, Any]) -> dict[str, float | None]:
    queries = report.get("queries", [])
    score = score_semantic_utility(report.get("indexing"), queries).to_dict()
    metrics = {
        f"{section}.{key}": _number(value)
        for section in ("indexing", "agent")
        for key, value in score[section].items()
    }
    for state in ("error", "empty", "skipped"):
        metrics[f"agent.{state}_count"] = float(
            sum(query_state(q) == state for q in queries)
        )
    observation_keys = {
        "agent.evidence_available_rate": "selected_triple_count",
        "agent.mean_evidence_coverage": "coverage",
        "agent.missing_slots_per_question": "missing_slots",
    }
    for metric, field in observation_keys.items():
        if not queries or not all(field in q for q in queries):
            metrics[metric] = None
    if report.get("indexing") is None:
        for metric in list(metrics):
            if metric.startswith("indexing."):
                metrics[metric] = None
    # Absence of telemetry is not free execution. This runner has no cost ledger.
    metrics.update(
        {
            "usage.input_tokens": None,
            "usage.output_tokens": None,
            "usage.cost_usd": None,
        }
    )
    return metrics


def compare_runs(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    changes: Sequence[str] = (),
    hypothesis: str = "",
) -> dict[str, Any]:
    """Show diagnostics always; aggregate deltas require matched recorded inputs."""
    if set(changes) - set(CHANGEABLE):
        raise ValueError(
            "Unknown changed condition; documents and questions must remain fixed"
        )
    if changes and not hypothesis.strip():
        raise ValueError(
            "Declare --hypothesis when allowing changed experiment conditions"
        )
    issues: list[str] = []
    left = baseline.get("reproducibility", {})
    right = candidate.get("reproducibility", {})
    for label, receipt in (("baseline", left), ("candidate", right)):
        if receipt.get("schema_version") != SCHEMA:
            issues.append(
                f"{label}: missing supported reproducibility receipt (legacy reports are diagnostic only)"
            )
        conditions = receipt.get("conditions", {})
        if not isinstance(conditions, dict):
            issues.append(f"{label}: invalid condition fingerprints")
            continue
        missing = REQUIRED - conditions.keys()
        if missing:
            issues.append(
                f"{label}: missing fingerprints: {', '.join(sorted(missing))}"
            )
        if any(
            not isinstance(value, str) or len(value) != 64
            for value in conditions.values()
        ):
            issues.append(f"{label}: malformed fingerprints")
        issues.extend(f"{label}: {gap}" for gap in receipt.get("gaps", []))
    lc, rc = left.get("conditions", {}), right.get("conditions", {})
    if not isinstance(lc, dict):
        lc = {}
    if not isinstance(rc, dict):
        rc = {}
    changed = sorted(
        key for key in REQUIRED if key in lc and key in rc and lc[key] != rc[key]
    )
    undeclared = set(changed) - set(changes)
    if undeclared:
        issues.append(f"Undeclared changed conditions: {', '.join(sorted(undeclared))}")
    lq, rq = (
        _queries(baseline, "baseline", issues),
        _queries(candidate, "candidate", issues),
    )
    if set(lq) != set(rq):
        issues.append(
            "Executed question IDs differ; missing/aborted questions cannot be dropped from aggregates"
        )
    for identity in lq.keys() & rq.keys():
        if any(
            lq[identity].get(k, "") != rq[identity].get(k, "")
            for k in ("question", "expect")
        ):
            issues.append(f"Question/reference changed for ID {identity}")
    for label, report in (("baseline", baseline), ("candidate", candidate)):
        run = report.get("run", {})
        if run.get("only") != "index" and "question_count" in run and len(report.get("queries", [])) != run["question_count"]:
            issues.append(f"{label}: recorded questions do not cover the requested question count")
        if int((report.get("indexing") or {}).get("files_unchanged", 0)):
            issues.append(
                f"{label}: indexing reused tracked files; use --no-track with isolated targets for a full E2E comparison"
            )
        if report.get("outcome", {}).get("status") in {
            "running",
            "interrupted",
        } or report.get("fatal_error"):
            issues.append(f"{label}: run did not finish its requested phases")
    comparable = not issues
    before, after = _metrics(baseline), _metrics(candidate)
    metrics: dict[str, dict[str, Any]] = {}
    for name in sorted(before.keys() | after.keys()):
        b, a = before.get(name), after.get(name)
        delta = (
            round(a - b, 6) if comparable and a is not None and b is not None else None
        )
        interpretation = "descriptive delta; no automatic quality verdict"
        if "latency" in name:
            interpretation = "observed wall time; external conditions unverified, not a performance claim"
        elif "reference_contains" in name:
            interpretation = "literal reference containment proxy, not answer accuracy"
        elif name.startswith("usage."):
            interpretation = "unavailable; no usage/cost ledger was recorded"
        metrics[name] = asdict(MetricDelta(b, a, delta, interpretation))
    pairs = []
    for identity in sorted(lq.keys() | rq.keys()):
        bq, aq = lq.get(identity), rq.get(identity)
        bs, cs = (
            query_state(bq) if bq else "missing",
            query_state(aq) if aq else "missing",
        )
        same_question = bool(
            bq
            and aq
            and all(bq.get(k, "") == aq.get(k, "") for k in ("question", "expect"))
        )
        pairs.append(
            {
                "id": identity,
                "matched": same_question,
                "baseline_state": bs,
                "candidate_state": cs,
                "transition": f"{bs} -> {cs}",
                "baseline_answer": bq.get("answer") if bq else None,
                "candidate_answer": aq.get("answer") if aq else None,
                "baseline_error": bq.get("error") if bq else None,
                "candidate_error": aq.get("error") if aq else None,
                "baseline_missing_slots": bq.get("missing_slots") if bq else None,
                "candidate_missing_slots": aq.get("missing_slots") if aq else None,
            }
        )
    return {
        "schema_version": "seocho.run_comparison.v1",
        "comparable": comparable,
        "verdict": "descriptive_comparison" if comparable else "incomparable",
        "hypothesis": hypothesis,
        "declared_changes": sorted(set(changes)),
        "observed_changes": changed,
        "issues": issues,
        "matched_questions": sum(p["matched"] for p in pairs),
        "metrics": metrics,
        "questions": pairs,
        "limitations": [
            "Matched fingerprints are necessary, not sufficient, for causal inference.",
            "Graph contents, service versions, hardware, warmup and provider revisions are unverified.",
            "Answered/support/reference-containment metrics do not establish grounded correctness.",
            "A successful retry does not erase failures in the original run.",
        ],
    }


def render_comparison(report: Mapping[str, Any]) -> str:
    lines = [
        "# SEOCHO saved-run comparison",
        "",
        f"Verdict: {report['verdict']}",
        f"Hypothesis: {report['hypothesis'] or 'replication / diagnostics'}",
        f"Matched questions: {report['matched_questions']}",
        "",
    ]
    lines.extend(f"- {issue}" for issue in report["issues"])
    lines += ["", "| Metric | Baseline | Candidate | Delta |", "|---|---:|---:|---:|"]
    for name, metric in report["metrics"].items():
        values = [
            "unavailable" if metric[k] is None else str(metric[k])
            for k in ("baseline", "candidate", "delta")
        ]
        lines.append(f"| {name} | {' | '.join(values)} |")
    lines += ["", "## Question transitions", ""]
    for pair in report["questions"]:
        lines.append(
            f"- {pair['id']}: {pair['transition']} (matched={pair['matched']})"
        )
    lines += ["", *[f"- {limitation}" for limitation in report["limitations"]], ""]
    return "\n".join(lines)
