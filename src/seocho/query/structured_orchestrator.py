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

from dataclasses import dataclass
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
        }


def _introspected_schema_text(schema: Dict[str, Any]) -> str:
    """A real DB-introspection schema block (labels + relationship types) — the
    honest 'schema organ OFF' baseline a bare system actually has."""
    labels = ", ".join(schema.get("labels", []) or schema.get("node_labels", []) or [])
    rels = ", ".join(schema.get("relationship_types", []) or [])
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
        # bare: no forced workspace, no DB-enforced filter (a real un-governed read)
        return self.graph_store.query(
            cypher, params={**gen_params, "limit": self.row_cap}, database=self.database
        )

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
        schema_text, policy, source, version = self._resolve_schema(run_context)

        # Retrieve step (no prose), with a repair loop: when the guardrail rejects
        # the generated Cypher, feed the violation reasons back for a retry (up to
        # repair_budget) so a fixable non-conformance does not force an abstain —
        # the governed arm then differs by GOVERNANCE, not a generator defect.
        violations: Tuple[str, ...] = ()
        cypher, gen_params, feedback, attempts = "", {}, None, 0
        while True:
            cypher, gen_params = self._call_gen(question, schema_text, feedback)
            if not self.arm.guardrail:
                violations = ()
                break
            violations = tuple(validate_text2cypher_fallback(
                cypher, params={**gen_params, "workspace_id": workspace_id, "limit": 1},
                policy=policy))
            if not violations or attempts >= self.repair_budget:
                break
            feedback = {"prior_cypher": cypher, "violations": list(violations)}
            attempts += 1
        rejected = bool(violations)

        rows = [] if rejected else self._execute(cypher, workspace_id, gen_params)
        answer = self._synth(question, rows)              # the ONLY prose writer (B5)

        return StructuredQueryResult(
            answer=answer, cypher=cypher, rows=rows, schema_source=source,
            pinned_version=version, workspace_enforced=self.arm.workspace_enforce,
            guardrail_on=self.arm.guardrail, guardrail_violations=violations,
            guardrail_rejected=rejected, repair_attempts=attempts, arm=self.arm.name,
        )
