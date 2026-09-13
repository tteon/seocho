"""The structured query orchestrator — a PLAIN deterministic function over organ flags.

Per the orchestrator design review (go-with-fixes): NOT an LLM manager agent wrapping a
single specialist (that was inflated ceremony). It is plain OS code that reads the arm's
organ flags + the per-request run context and drives one honest flow:

    resolve schema  ->  generate Cypher (retrieve step)  ->  guardrail  ->  execute  ->  synthesize

Each organ is an independent flag (arm_config.ArmConfig):
- schema  : pinned frozen snapshot schema (resolver) vs REAL DB-introspected labels (B1/B4)
- guardrail: reject schema-violating / unscoped Cypher, policy from the SAME snapshot (B3)
- workspace: governed execute — force-pin workspace + enforce_workspace_filter (B2)
- pin     : whether the schema is read from a pinned frozen version at all
- intern  : canonical-address resolution strategy for the retrieve step

Retrieve-only specialist + one synthesizer owns the prose (B5): the Cypher generator returns
a query, execution returns rows, and the synthesizer alone writes the answer — so the
answer-quality metric attaches to ONE controlled surface. The LLM text2cypher and the graph
execution are injected SEAMS so the organ semantics are testable without live infra.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .arm_config import ArmConfig
from .hybrid_planner import policy_from_ontology
from .workload_compiler import validate_text2cypher_fallback


@dataclass
class StructuredQueryResult:
    answer: str
    cypher: str
    rows: List[Dict[str, Any]]
    schema_source: str                       # "pinned" | "introspected"
    pinned_version: str = ""
    workspace_enforced: bool = False
    guardrail_on: bool = False
    guardrail_violations: Tuple[str, ...] = ()
    guardrail_rejected: bool = False
    repair_attempts: int = 0
    arm: str = ""
    entity_resolutions: Dict[str, str] = field(default_factory=dict)
    stage_ms: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "arm": self.arm, "answer": self.answer, "cypher": self.cypher,
            "row_count": len(self.rows), "schema_source": self.schema_source,
            "pinned_version": self.pinned_version,
            "workspace_enforced": self.workspace_enforced,
            "guardrail_on": self.guardrail_on,
            "guardrail_violations": list(self.guardrail_violations),
            "guardrail_rejected": self.guardrail_rejected,
            "repair_attempts": self.repair_attempts,
            "entity_resolutions": dict(self.entity_resolutions),
            "stage_ms": dict(self.stage_ms),
        }


def _introspected_schema_text(schema: Dict[str, Any]) -> str:
    """A real DB-introspection schema block (labels + relationship types) — the
    honest 'schema organ OFF' baseline a bare system actually has.

    Shape-tolerant: get_schema returns LISTS of labels, but the engine's
    _get_schema_info returns pre-joined STRINGS — joining a string iterates its
    CHARACTERS ("D, i, s, e, a, s, e"), which silently hands the generator a
    garbage schema and corrupts every introspected arm (review blocker #1)."""

    def _join(value: Any) -> str:
        if isinstance(value, str):
            return value
        return ", ".join(value or [])

    labels = _join(schema.get("labels") or schema.get("node_labels"))
    rels = _join(schema.get("relationship_types"))
    return f"Node labels: {labels}\nRelationship types: {rels}"


class StructuredQueryOrchestrator:
    def __init__(
        self,
        *,
        arm: ArmConfig,
        graph_store: Any,
        ontology: Any,
        cypher_generator: Callable[[str, str], str],
        synthesizer: Callable[[str, List[Dict[str, Any]]], str],
        resolver: Any = None,                       # PinnedSchemaResolver (pin/schema organ)
        get_schema_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        database: str = "neo4j",
        row_cap: int = 50,
        repair_budget: int = 0,
    ) -> None:
        self.arm = arm
        self.graph_store = graph_store
        self.ontology = ontology
        self._gen = cypher_generator            # retrieve step (text2cypher SEAM)
        self._synth = synthesizer               # the ONLY prose writer (B5)
        self.resolver = resolver
        self._get_schema_fn = get_schema_fn or (lambda: graph_store.get_schema(database=database))
        self.database = database
        self.row_cap = row_cap
        self.repair_budget = max(0, int(repair_budget))   # guardrail-reject -> retry-with-feedback

    # -- schema organ (pinned frozen snapshot vs real introspection) ----------
    def _resolve_schema(self, run_context: Any) -> Tuple[str, Any, str, str]:
        """Return (schema_text, policy, source, pinned_version)."""
        if self.arm.schema_source == "pinned" and self.arm.pin and self.resolver is not None:
            resolved = self.resolver.resolve_for(run_context)
            if resolved is not None:
                # prompt schema AND guardrail policy from the SAME frozen snapshot (B3)
                return resolved.schema_text(), resolved.policy, "pinned", resolved.version
        # OFF baseline: real DB introspection; guardrail policy still from the ontology
        schema = self._get_schema_fn() or {}
        return _introspected_schema_text(schema), policy_from_ontology(self.ontology), "introspected", ""

    # -- workspace organ: deterministic scope strip (not prompt-dependent) -----
    @staticmethod
    def _strip_workspace_scope(cypher: str) -> str:
        """Remove every ``_workspace_id: $workspace_id`` clause from the Cypher so the
        workspace-OFF arm is a REAL un-scoped read regardless of whether the LLM
        honored the un-scoped prompt (it often re-adds the filter out of habit — the
        organ must be a system property, not a prompt suggestion)."""
        # `{_workspace_id: $workspace_id, name: $x}` -> `{name: $x}`
        c = re.sub(r"_workspace_id\s*:\s*\$workspace_id\s*,\s*", "", cypher)
        # `{name: $x, _workspace_id: $workspace_id}` -> `{name: $x}`
        c = re.sub(r"\s*,\s*_workspace_id\s*:\s*\$workspace_id", "", c)
        # `{_workspace_id: $workspace_id}` -> `` (drop the now-empty prop map)
        c = re.sub(r"\s*\{\s*_workspace_id\s*:\s*\$workspace_id\s*\}", "", c)
        return c

    # -- workspace organ (governed execute vs bare) ---------------------------
    def _execute(self, cypher: str, workspace_id: str,
                 gen_params: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self.arm.workspace_enforce:
            # governed: force-pin the tenant and let the store enforce the filter (B2).
            # The generator's value params ride along so a value never has to be
            # inlined (which the guardrail forbids) to be executable.
            return self.graph_store.query(
                cypher,
                params={**gen_params, "workspace_id": workspace_id, "limit": self.row_cap},
                database=self.database,
                workspace_id=workspace_id,
                enforce_workspace_filter=True,
            )
        # bare: no forced workspace, no DB-enforced filter (a real un-governed read).
        # Deterministically strip any tenant scope the LLM re-added despite the
        # un-scoped prompt, so the organ-OFF arm truly crosses the tenant boundary.
        # workspace_id still rides in params (harmless if now unreferenced) so a
        # residual $workspace_id can never crash on an unbound parameter.
        return self.graph_store.query(
            self._strip_workspace_scope(cypher),
            params={**gen_params, "workspace_id": workspace_id, "limit": self.row_cap},
            database=self.database,
        )

    # -- intern organ, READ side (seocho-zfe): string -> canonical entity ------
    def _resolve_entity_params(self, gen_params: Dict[str, Any], workspace_id: str
                               ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """Resolve the generator's entity-name params against the CANONICAL entity
        store before execution. The medical A/B exposed this as the binding gap:
        every generated query was structurally valid but `name = $param` exact
        string equality missed the canonical surface form ("BCC" vs "basal cell
        skin cancer") -> 90% abstain. With the intern organ ON, each string param
        is resolved (exact-normalized first, then containment, shortest match) to
        the stored canonical name; OFF keeps raw string equality — which makes the
        intern flag a REAL query-time ablation instead of a no-op."""
        if not self.arm.intern:
            return gen_params, {}
        resolved: Dict[str, Any] = dict(gen_params)
        notes: Dict[str, str] = {}
        # workspace organ gates the WHOLE tenant boundary: when off, name resolution
        # is un-scoped too (it can bind another tenant's canonical form), not just the
        # execute filter — otherwise a tenant-scoped resolve would silently re-isolate
        # a no-workspace arm and mask the leak (probe-1 confound).
        scope_ws = workspace_id if self.arm.workspace_enforce else None
        for key, value in gen_params.items():
            values = value if isinstance(value, list) else [value]
            if not values or not all(isinstance(x, str) and len(x) >= 3 for x in values):
                continue
            out = []
            for x in values:
                hit = self._resolve_one_name(x, scope_ws)
                out.append(hit if hit is not None else x)
                if hit is not None and hit != x:
                    notes[f"{key}={x}"] = hit
            resolved[key] = out if isinstance(value, list) else out[0]
        return resolved, notes

    def _resolve_one_name(self, text: str, workspace_id: Optional[str]) -> Optional[str]:
        """One mention -> the stored canonical name, or None. Fixed read-only
        templates with value params only (never interpolated). ``workspace_id`` None
        means un-scoped resolution (workspace organ off)."""
        ws_clause = "n._workspace_id = $ws AND " if workspace_id is not None else ""
        params = {"t": text}
        if workspace_id is not None:
            params["ws"] = workspace_id
        try:
            rows = self.graph_store.query(
                f"MATCH (n) WHERE {ws_clause}n.name IS NOT NULL "
                "AND toLower(n.name) = toLower($t) RETURN n.name AS name LIMIT 1",
                params=params, database=self.database)
            if rows:
                return rows[0]["name"]
            rows = self.graph_store.query(
                f"MATCH (n) WHERE {ws_clause}n.name IS NOT NULL AND "
                "(toLower(n.name) CONTAINS toLower($t) OR toLower($t) CONTAINS toLower(n.name)) "
                "RETURN n.name AS name ORDER BY size(n.name) ASC LIMIT 1",
                params=params, database=self.database)
            return rows[0]["name"] if rows else None
        except Exception:
            return None

    def _call_gen(self, question: str, schema_text: str, feedback: Any):
        """Call the generator, passing repair ``feedback`` when it accepts it
        (the grounded generator does; a 2-arg test double does not)."""
        try:
            gen_out = self._gen(question, schema_text, feedback=feedback)
        except TypeError:
            gen_out = self._gen(question, schema_text)
        if isinstance(gen_out, tuple):
            return (gen_out[0] or ""), (gen_out[1] or {})
        return (gen_out or ""), {}

    def answer(self, question: str, run_context: Any, *, workspace_id: str) -> StructuredQueryResult:
        # per-stage wall time (ms): the memory-plane analog of a serving trace's
        # prefill/decode split — quantifies the governance tax vs the LLM call.
        _t: Dict[str, float] = {}
        _t0 = time.perf_counter()
        schema_text, policy, source, version = self._resolve_schema(run_context)
        _t["resolve_schema"] = (time.perf_counter() - _t0) * 1000

        # Retrieve step (no prose), with a repair loop: when the guardrail rejects
        # the generated Cypher, feed the violation reasons back for a retry (up to
        # repair_budget) so a fixable non-conformance does not force an abstain —
        # the governed arm then differs by GOVERNANCE, not a generator defect.
        violations: Tuple[str, ...] = ()
        cypher, gen_params, feedback, attempts = "", {}, None, 0
        _gen_ms = _guard_ms = 0.0
        while True:
            _t1 = time.perf_counter()
            cypher, gen_params = self._call_gen(question, schema_text, feedback)
            _gen_ms += (time.perf_counter() - _t1) * 1000
            _t1 = time.perf_counter()
            if not self.arm.guardrail:
                violations = ()
                break
            violations = tuple(validate_text2cypher_fallback(
                cypher, params={**gen_params, "workspace_id": workspace_id, "limit": 1},
                policy=policy))
            if not self.arm.workspace_enforce:
                # workspace organ OFF: the generator intentionally emits an
                # UN-scoped read, so the tenant-scope rules must not fire —
                # otherwise this arm always rejects->abstains and measures the
                # guardrail, not the missing isolation (review blocker #2).
                violations = tuple(
                    v for v in violations
                    if v != "missing_workspace_scope_expression"
                    and v != "missing_parameter:workspace_id"
                    and v != "missing_parameter_value:workspace_id")
            _guard_ms += (time.perf_counter() - _t1) * 1000
            if not violations or attempts >= self.repair_budget:
                break
            feedback = {"prior_cypher": cypher, "violations": list(violations)}
            attempts += 1
        rejected = bool(violations)

        _t["generate_llm"] = _gen_ms
        _t["guardrail"] = _guard_ms
        resolutions: Dict[str, str] = {}
        exec_params = gen_params
        _t1 = time.perf_counter()
        if not rejected:
            exec_params, resolutions = self._resolve_entity_params(gen_params, workspace_id)
        _t["entity_resolve"] = (time.perf_counter() - _t1) * 1000
        _t1 = time.perf_counter()
        rows = [] if rejected else self._execute(cypher, workspace_id, exec_params)
        _t["execute_graph"] = (time.perf_counter() - _t1) * 1000
        _t1 = time.perf_counter()
        answer = self._synth(question, rows)              # the ONLY prose writer (B5)
        _t["synthesize_llm"] = (time.perf_counter() - _t1) * 1000

        return StructuredQueryResult(
            answer=answer, cypher=cypher, rows=rows, schema_source=source,
            pinned_version=version, workspace_enforced=self.arm.workspace_enforce,
            guardrail_on=self.arm.guardrail, guardrail_violations=violations,
            guardrail_rejected=rejected, repair_attempts=attempts, arm=self.arm.name,
            entity_resolutions=resolutions, stage_ms={k: round(v, 1) for k, v in _t.items()},
        )
