"""Offline HTML inspection of saved evidence; module maps never imply traces."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
import json
from typing import Any

from .run_outcomes import query_state

_STYLE = """
:root{color-scheme:light;--ink:#172438;--muted:#50627a;--line:#cfdae6;--accent:#075c75}
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:var(--ink);font:16px/1.6 system-ui,sans-serif}
main{max-width:1200px;margin:auto;padding:36px 24px}h1{font-size:clamp(26px,4vw,40px);margin:6px 0}
h2{margin:0 0 16px;font-size:24px}h3{margin:0 0 8px}p{margin:8px 0}a{color:var(--accent)}
nav{display:flex;gap:24px;flex-wrap:wrap;margin:24px 0}section{margin:28px 0}
.cards,.modules{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}
.card,article,details.panel{background:white;border:1px solid var(--line);border-radius:12px;padding:20px}
.value{font-size:30px;font-weight:700;display:block}.muted,small{color:var(--muted)}
.notice{padding:16px 20px;border-left:4px solid var(--accent);background:#e4f0f4;border-radius:4px}
.state{font-weight:700}.state.error,.state.failed,.state.interrupted{color:#a31e30}
.state.empty,.state.partial,.state.skipped{color:#805000}.state.answered,.state.completed{color:#126045}
summary{cursor:pointer;font-weight:650}details+details{margin-top:12px}.modules details+details{margin-top:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace}
code{overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}th,td{padding:10px;text-align:left;border-bottom:1px solid var(--line)}
.scroll{overflow:auto}.questions{display:grid;gap:14px}.toolbar{display:flex;gap:14px;flex-wrap:wrap;margin:16px 0}
input,select{font:inherit;padding:10px;border:1px solid #78899e;border-radius:6px;background:white;color:var(--ink)}
input{min-width:240px}.question[hidden]{display:none}meter{width:100%;height:16px}article{overflow-wrap:anywhere}
button:focus-visible,a:focus-visible,summary:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #b14b00;outline-offset:3px}
@media print{body{background:white}nav,.toolbar{display:none}details{break-inside:avoid}}
"""

_SCRIPT = """
const search = document.getElementById('search');
const state = document.getElementById('state');
function filterQuestions() {
  let visible = 0;
  for (const card of document.querySelectorAll('.question')) {
    card.hidden = !card.textContent.toLowerCase().includes(search.value.toLowerCase()) ||
      (state.value !== 'all' && card.dataset.state !== state.value);
    if (!card.hidden) visible += 1;
  }
  document.getElementById('visible-count').textContent = visible + ' questions shown';
}
search.addEventListener('input', filterQuestions);
state.addEventListener('change', filterQuestions);
filterQuestions();
"""

# Architectural ownership, not inferred execution spans or a full SDK call graph.
MODULES = (
    (
        "Preflight",
        "run_preflight.py",
        "Checks files, configuration and the configured graph target.",
        "preflight",
    ),
    (
        "Ontology",
        "ontology/",
        "Defines entity and relationship constraints; offline governance owns reasoning.",
        None,
    ),
    (
        "Indexing",
        "index/",
        "Reads documents, extracts and validates graph facts, and reports write outcomes.",
        "indexing",
    ),
    (
        "Graph store",
        "store/graph.py",
        "Executes graph reads and writes against the selected backend.",
        None,
    ),
    (
        "Query & answering",
        "query/",
        "Retrieves graph evidence and produces answers with available support metadata.",
        "queries",
    ),
    (
        "Agent orchestration",
        "agent/ · integrations/openai_agents.py",
        "Connects tools and the configured agent execution path.",
        None,
    ),
    (
        "Evidence",
        "run_evidence.py · run_comparison.py",
        "Records conditions and compares matched saved experiments.",
        "reproducibility",
    ),
    (
        "Reports",
        "run_reporting.py · run_visualization.py",
        "Preserves receipts and presents the recorded evidence locally.",
        None,
    ),
)


def _text(value: object) -> str:
    return escape(str(value if value is not None else "unavailable"), quote=True)


def _json(value: object) -> str:
    return _text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def _state(value: str) -> str:
    known = {
        "completed",
        "failed",
        "partial",
        "running",
        "interrupted",
        "answered",
        "error",
        "empty",
        "skipped",
    }
    css = value if value in known else "unknown"
    return f'<span class="state {css}">{_text(value)}</span>'


def render_run_view(
    report: Mapping[str, Any], comparison: Mapping[str, Any] | None = None
) -> str:
    """Render escaped data with fixed scripts; no external assets or uploads."""
    import base64
    import hashlib

    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    run = report.get("run", {})
    queries = report.get("queries", [])
    index = report.get("indexing", {})
    outcome = report.get("outcome", {}).get("status", "unknown")
    answered = sum(query_state(q) == "answered" for q in queries)
    cards = [
        ("Run status", _state(str(outcome))),
        ("Answered / recorded", f"{answered} / {len(queries)}"),
        ("Failed documents", _text(index.get("files_failed"))),
        ("Cost / tokens", "unavailable"),
    ]
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        f"<title>SEOCHO experiment · {_text(run.get('name', 'Saved run'))}</title>",
        f"<style>{_STYLE}</style></head><body><main>",
        "<small>SEOCHO · EXPERIMENT INSPECTOR</small>",
        f"<h1>{_text(run.get('name', 'Saved run'))}</h1>",
        f'<p class="muted">Workspace: {_text(run.get("workspace_id"))} · Database: {_text(run.get("database"))}</p>',
        '<nav aria-label="Report sections"><a href="#overview">Overview</a><a href="#comparison">Comparison</a><a href="#questions">Questions</a><a href="#modules">Module map</a></nav>',
        '<p class="notice">This file stays local. Completion and answer rates describe execution, not answer correctness. No service connection is made.</p>',
        '<section id="overview"><h2>Run overview</h2><div class="cards">',
        *[
            f'<div class="card"><small>{label}</small><span class="value">{value}</span></div>'
            for label, value in cards
        ],
        "</div>",
        f"<p>Last stage: <strong>{_text(report.get('active_stage'))}</strong> · Requested questions: {_text(run.get('question_count'))}</p>",
    ]
    if report.get("active_question"):
        parts.append(
            f'<p class="notice">Question active at checkpoint: {_text(report["active_question"].get("id"))}. Its completion is not recorded.</p>'
        )
    diagnostics = report.get("diagnostics", [])
    if diagnostics:
        parts.append("<h3>Diagnostics</h3>")
        for d in diagnostics:
            parts.append(
                f"<article><strong>{_text(d.get('stage'))} / {_text(d.get('code'))}</strong><p>{_text(d.get('message'))}</p><p>Next: {_text(d.get('action'))}</p></article>"
            )
    parts.append(
        '<details class="panel"><summary>Recorded conditions and limitations</summary><pre>'
        + _json(report.get("reproducibility", {}))
        + "</pre></details></section>"
    )
    parts.append('<section id="comparison"><h2>Before / after</h2>')
    if comparison is None:
        parts.append(
            '<p class="muted">No baseline supplied. Add --baseline to compare this run using the saved condition fingerprints.</p>'
        )
    else:
        parts.append(
            f'<p class="notice">{_text(comparison["verdict"])} — {_text(comparison.get("hypothesis") or "replication / diagnostics")}</p>'
        )
        parts.extend(f"<p>{_text(issue)}</p>" for issue in comparison["issues"])
        parts.append(
            '<p>Delta = candidate − baseline. No automatic quality verdict; latency is observed wall time.</p><div class="scroll"><table><thead><tr><th>Metric</th><th>Baseline</th><th>Candidate</th><th>Delta</th></tr></thead><tbody>'
        )
        for name, values in comparison["metrics"].items():
            parts.append(
                f'<tr><th scope="row">{_text(name)}</th>'
                + "".join(
                    f"<td>{_text(values[k])}</td>"
                    for k in ("baseline", "candidate", "delta")
                )
                + "</tr>"
            )
        parts.append("</tbody></table></div>")
        parts.extend(
            f'<p class="muted">{_text(item)}</p>'
            for item in comparison.get("limitations", [])
        )
        parts.append(
            '<details class="panel"><summary>Question transitions, including missing questions</summary>'
        )
        for pair in comparison["questions"]:
            parts.append(
                f"<article><strong>{_text(pair['id'])} · {_text(pair['transition'])}</strong><p>Baseline: {_text(pair['baseline_answer'])}</p><p>Candidate: {_text(pair['candidate_answer'])}</p></article>"
            )
        parts.append("</details>")
    parts.append(
        '</section><section id="questions"><h2>Question evidence</h2><div class="toolbar"><label>Search <input id="search" type="search" placeholder="Question, answer or evidence"></label><label>State <select id="state"><option value="all">All states</option>'
        + "".join(
            f'<option value="{s}">{s}</option>'
            for s in ("answered", "error", "empty", "skipped")
        )
        + '</select></label></div><p id="visible-count" aria-live="polite"></p><div class="questions">'
    )
    for q in queries:
        state = query_state(q)
        parts.append(
            f'<article class="question" data-state="{state}"><h3>{_text(q.get("id"))} · {_text(q.get("question"))}</h3>{_state(state)}<p>{_text(q.get("answer"))}</p><small>Support: {_text(q.get("support_status"))} · Time: {_text(q.get("latency_s"))} s</small>'
        )
        if q.get("error"):
            parts.append(f'<p class="notice">{_text(q["error"])}</p>')
        parts.append(
            f"<details><summary>Reference, graph evidence and full record</summary><pre>{_json(q)}</pre></details></article>"
        )
    parts.append(
        '</div></section><section id="modules"><h2>SEOCHO module map</h2><p class="muted">Architectural responsibilities, not an execution trace. A recorded section is not proof that every module or substep ran. Module-level timings are unavailable.</p><div class="modules">'
    )
    for title, path, description, key in MODULES:
        observed = (
            "Recorded section available"
            if key and key in report
            else "Architecture reference only"
        )
        parts.append(
            f'<details class="panel"><summary>{title}</summary><p>{description}</p><code>src/seocho/{path}</code><p class="muted">{observed}</p>'
        )
        if key and key in report:
            parts.append("<pre>" + _json(report[key]) + "</pre>")
        parts.append("</details>")
    parts.append(
        f'</div></section><footer class="muted">Derived from saved receipts. Original reports remain unchanged.</footer></main><script>{_SCRIPT}</script></body></html>'
    )
    return "\n".join(parts)
