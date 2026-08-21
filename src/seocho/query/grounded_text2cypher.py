"""Ontology-grounded text2cypher — the generator the guardrail will actually pass.

The structured engine's live smoke exposed the gap: the deterministic planner emits
Cypher with undeclared properties, inlined literals, and no tenant scope, so the
governed guardrail (rightly) REJECTS it and the arm abstains even when the answer
exists. This generator emits guardrail-conformant Cypher by construction, grounded
in the PINNED schema (ADR-0201 resolver): declared identifiers only, every value a
`$param` (never inlined), the tenant bound with `$workspace_id`, bounded by
`LIMIT $limit`. It is the "ontology grounds the query" thesis (ADR-0191) wired into
the runtime, not just measured in an ablation.

Returns `(cypher, params)` — the params are the LLM's value bindings, which the
orchestrator merges with `workspace_id` + `limit` for both the guardrail check and
execution (so a value never has to be inlined to be executable).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple

from ..store.llm import complete_with_task_hints

# NOTE: single braces — these constants are inserted as a `.format()` ARGUMENT
# (rule2=...), so their braces are NOT collapsed by the template's .format().
_RULE2_SCOPED = (
    "2. Bind `{_workspace_id: $workspace_id}` on EVERY node in the pattern — not "
    "just the anchor — so every node you MATCH or RETURN is tenant-scoped, e.g. "
    "`(c:Company {_workspace_id: $workspace_id})<-[:AFFECTS]-"
    "(i:Incident {_workspace_id: $workspace_id})`.\n"
)
# The workspace organ OFF: an honest un-governed read binds NO tenant filter, so it
# matches across every tenant's data (the leak the organ prevents), e.g.
# `(c:Company)<-[:AFFECTS]-(i:Incident)`.
_RULE2_UNSCOPED = (
    "2. Do NOT add any `_workspace_id` filter to any node — match nodes by their "
    "declared properties only, e.g. `(c:Company)<-[:AFFECTS]-(i:Incident)`.\n"
)

_SYSTEM = (
    "You translate the user's question into ONE read-only Cypher query for a "
    "{tenancy} property graph.\n"
    "HARD RULES (a query that breaks any of these is rejected):\n"
    "1. Use ONLY the node labels, relationship types, and properties in the SCHEMA "
    "below. Never invent an identifier.\n"
    "{rule2}"
    "3. Parameterize EVERY value as a `$name` parameter — never inline a string or "
    "number literal.\n"
    "4. End the query with `LIMIT $limit`.\n"
    "5. Read-only: no CREATE/MERGE/SET/DELETE.\n\n"
    "SCHEMA:\n{schema}\n\n"
    'Return ONLY JSON: {{"cypher": "<one query>", "params": {{"<name>": <value>, ...}}}}. '
    "Do not include $workspace_id or $limit in params (the runtime binds them)."
)


def build_text2cypher_system_prompt(schema_text: str, *, workspace_scoped: bool = True) -> str:
    return _SYSTEM.format(
        schema=schema_text,
        tenancy="tenant-scoped" if workspace_scoped else "property",
        rule2=_RULE2_SCOPED if workspace_scoped else _RULE2_UNSCOPED,
    )


def _extract_json(text: str) -> Dict[str, Any]:
    raw = re.sub(r"^```(json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else {}
            except (json.JSONDecodeError, ValueError):
                return {}
        return {}


def _feedback_note(feedback: Any) -> str:
    """A retry directive built from the guardrail's rejection of a prior attempt."""
    if not feedback:
        return ""
    prior = str((feedback or {}).get("prior_cypher", ""))[:400]
    viols = ", ".join(str(v) for v in (feedback or {}).get("violations", []))[:400]
    return ("\n\nYOUR PREVIOUS QUERY WAS REJECTED — fix it and try again.\n"
            f"Rejected query: {prior}\n"
            f"Violations (each MUST be fixed): {viols}\n"
            "Common fixes: use only SCHEMA identifiers; add "
            "`{_workspace_id: $workspace_id}` to EVERY node; replace every inlined "
            "literal with a `$param`; end with `LIMIT $limit`.")


def generate_grounded_cypher(
    llm: Any,
    question: str,
    schema_text: str,
    *,
    workspace_id: str = "default",
    limit: int = 50,
    feedback: Any = None,
    workspace_scoped: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    """Generate guardrail-conformant Cypher + its value params for ``question``,
    grounded in ``schema_text`` (the pinned-schema block). Returns
    ``(cypher, params)``; ``params`` excludes ``workspace_id``/``limit`` (the
    orchestrator binds those). ``feedback`` (a prior rejection) drives a repair
    retry that names the violations to fix.

    ``workspace_scoped`` follows the workspace organ: when False (the organ OFF —
    BARE / governed-no-workspace) the generator emits an un-governed read with NO
    tenant filter, so the arm actually exercises the missing isolation instead of
    crashing on an unbound ``$workspace_id`` (it would otherwise reference a param
    the un-governed execute path never binds)."""
    system = build_text2cypher_system_prompt(
        schema_text, workspace_scoped=workspace_scoped) + _feedback_note(feedback)
    try:
        resp = complete_with_task_hints(
            llm, system=system, user=question, temperature=0.0,
            response_format={"type": "json_object"}, reasoning_mode=False,
            task_hint="json_extraction",
        )
        text = resp.json() if hasattr(resp, "json") else None
        data = text if isinstance(text, dict) else _extract_json(getattr(resp, "text", "") or "")
    except Exception:
        data = {}
    cypher = str(data.get("cypher", "") or "").strip()
    params = data.get("params", {})
    if not isinstance(params, dict):
        params = {}
    # never let the model smuggle in the runtime-bound params
    params.pop("workspace_id", None)
    params.pop("limit", None)
    return cypher, params
