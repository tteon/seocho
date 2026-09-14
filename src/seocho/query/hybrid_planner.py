"""Route a question between validated generation and the pattern catalog (seocho-4bi).

SEOCHO's default is to assemble Cypher deterministically from intent + ontology
(ADR-0097). That remains the floor: it cannot hallucinate a label, its cost is
predictable, and it is auditable. The measured problem is coverage — the catalog's
patterns target entity lookup, one-hop relationships and financial metrics, so an
analytical question outside that set is still answered, with a plausible row count
and the wrong content. That is the hardest failure mode to notice, and no
answer-level signal reveals it.

Measured on the FinBench graph at SF1000 (same ontology, questions and scoring):

    arm         accuracy    sargable    dbHits total
    template    67% (6/9)        88%      43,235,371
    generated   78% (7/9)       100%             265

Generation covered exactly the gap (variable-length hops, edge-property access) and
produced cheaper plans, while one question was answered only by the template. The
arms are complementary, so this routes rather than replaces:

    generated_first   try validated generation; fall back to the catalog when it is
                      rejected or unavailable
    template_first    the historical order (default — unchanged behaviour)

Generation is fail-closed by construction: ``generate_validated_cypher`` refuses
undeclared labels/relationships and unbounded paths, requires tenant scope and a row
budget, and EXPLAINs before execution. A rejection therefore falls through to the
catalog instead of running something unvalidated.
"""

from __future__ import annotations

from dataclasses import replace

import asyncio
import logging
import os
from typing import Any, Dict, Mapping, Optional

from .contracts import QueryPlan
from .planner import DeterministicQueryPlanner

logger = logging.getLogger(__name__)

PRECEDENCE_ENV = "SEOCHO_QUERY_PRECEDENCE"
_VALID_PRECEDENCE = {"template_first", "by_route", "generated_first"}


def query_precedence() -> str:
    """Resolve the routing order. Defaults to the historical template-first order.

    Three settings, and the middle one is the intended production shape:

    * ``template_first``  — historical order (default; unchanged behaviour).
    * ``by_route``        — consult the RouteProfile catalog per question, so
      escalation is route-conditional exactly as ADR-backed exp5 concluded for the
      planner. Only routes whose declared planner is ``VALIDATED_GENERATION`` use
      generation; cheap lookups stay on the deterministic single pass.
    * ``generated_first`` — try generation for every question. Useful as a
      measurement switch (it is how the arm comparison was run), blunt as a policy.
    """
    value = str(os.getenv(PRECEDENCE_ENV, "template_first") or "template_first").strip().lower()
    return value if value in _VALID_PRECEDENCE else "template_first"


def _run_coroutine(coro: Any) -> Any:
    """Await ``coro`` from sync code, whether or not a loop is already running.

    The planner is called from both the sync SDK path and the async runtime, so a
    bare ``asyncio.run`` would fail inside a live loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def policy_from_ontology(ontology: Any, *, workspace_property: str = "_workspace_id") -> Any:
    """State the ontology as generation limits.

    Only declared labels and relationships may appear, paths must be bounded, tenant
    scope and a row budget are mandatory. The generated arm therefore works from the
    same schema the templates consume rather than a looser contract.
    """
    from .workload_compiler import Text2CypherFallbackPolicy

    properties = {"limit", workspace_property}
    for node in getattr(ontology, "nodes", {}).values():
        properties.update(getattr(node, "properties", {}) or {})
    for rel in getattr(ontology, "relationships", {}).values():
        properties.update(getattr(rel, "properties", {}) or {})
    return Text2CypherFallbackPolicy(
        allowed_labels=tuple(sorted(getattr(ontology, "nodes", {}))),
        allowed_relationships=tuple(sorted(getattr(ontology, "relationships", {}))),
        allowed_properties=tuple(sorted(properties)),
        workspace_property=workspace_property,
        max_graph_hops=4,
        max_result_rows=50,
        max_repair_attempts=1,
    )


def schema_for_prompt(ontology: Any, policy: Any) -> Dict[str, tuple]:
    """Schema plus the tenant convention, stated explicitly.

    Without the convention the model invents a workspace node to satisfy the scope
    requirement and the guardrail refuses it — a rejection that reflects the prompt
    rather than the model.
    """
    schema: Dict[str, tuple] = {
        "__tenant_scope__": (
            f"every matched node must carry {{{policy.workspace_property}: $workspace_id}} "
            "inline in its pattern; do not introduce a workspace node or relationship",
        ),
    }
    schema.update({
        label: tuple(sorted(getattr(node, "properties", {}) or {}))
        for label, node in getattr(ontology, "nodes", {}).items()
    })
    for rtype, rel in getattr(ontology, "relationships", {}).items():
        schema[f"({rel.source})-[:{rtype}]->({rel.target})"] = tuple(
            sorted(getattr(rel, "properties", {}) or {})
        )
        # Endpoint *types* orient (Account)-[:USES_CHANNEL]->(Channel) unambiguously and
        # say nothing when both ends share a label. For TRANSFER: Account -> Account the
        # arrow carries the entire distinction between "sent to" and "received from", and a
        # schema block listing only labels leaves the model to guess — measured as 2-hop
        # counts inflated 21x on a small anchor because the traversal ran undirected.
        # The ontology names the roles; the prompt has to actually carry them.
        source_role = getattr(rel, "source_role", "") or ""
        target_role = getattr(rel, "target_role", "") or ""
        if source_role or target_role:
            schema[f"__direction__({rtype})"] = (
                f"the arrow points from {source_role or rel.source} to "
                f"{target_role or rel.target}; follow it in the declared direction and do "
                f"not use an undirected pattern unless the question is symmetric",
            )
        # Degree facts, when the ontology carries measured ones. Same reasoning as the
        # roles: a schema of labels and endpoint types cannot say that one node holds
        # 158,315 edges while the median holds six, so a planner has no basis for
        # preferring a bounded shape — and an aggregate anchored on a hub does not return
        # at all, where the same question on a median node costs 45 ms.
        hint = getattr(rel, "degree_hint", None) or {}
        if hint.get("heavy_tailed"):
            schema[f"__cardinality__({rtype})"] = (
                f"heavy-tailed: median out-degree {hint.get('median_out')}, "
                f"p99 {hint.get('p99_out')}, maximum {hint.get('max_out'):,}. Multi-hop "
                f"expansion from a high-degree node is unbounded work, so prefer a shape "
                f"that can stop early (DISTINCT ... LIMIT) and avoid an unbounded "
                f"aggregate or ORDER BY over the whole neighbourhood",
            )
    return schema


class HybridQueryPlanner(DeterministicQueryPlanner):
    """Deterministic planner that can consult validated generation first.

    Subclasses rather than wraps the deterministic planner so intent normalisation,
    schema hints and the repair path stay exactly as they are; only the order in
    which a plan is sought changes.
    """

    def __init__(
        self,
        *,
        ontology: Any,
        llm: Any,
        workspace_id: str,
        explain: Optional[Any] = None,
        model: str = "",
    ) -> None:
        super().__init__(ontology=ontology, llm=llm, workspace_id=workspace_id)
        self._explain = explain
        self._model = model
        self.last_route: Optional[str] = None
        self.last_rejection: Optional[str] = None
        self.last_route_class: Optional[str] = None

    def plan(self, question: str) -> QueryPlan:
        self.last_route, self.last_rejection = None, None
        precedence = query_precedence()

        if precedence == "template_first":
            self.last_route = "template"
            return super().plan(question)

        if precedence == "by_route":
            # The catalog already encodes that escalation is route-conditional, so
            # which arm builds the query is read from there rather than applied
            # globally: a lookup the templates answer well gains nothing from a model
            # round trip and a rejection risk.
            from .route_profile import prefers_validated_generation, select_route_profile

            profile = select_route_profile(question)
            self.last_route_class = profile.route_class
            if not prefers_validated_generation(question):
                self.last_route = f"template:{profile.route_class}"
                return super().plan(question)

        generated = self._plan_generated(question)
        if generated is not None:
            self.last_route = "generated"
            return generated

        # Fail-closed generation falls through to the deterministic floor.
        self.last_route = "template_fallback"
        return super().plan(question)

    def _plan_generated(self, question: str) -> Optional[QueryPlan]:
        if self._explain is None:
            self.last_rejection = "no_explain_callback"
            return None
        try:
            from .text2cypher import generate_validated_cypher

            policy = policy_from_ontology(self.ontology)
            params: dict[str, Any] = {
                "workspace_id": self.workspace_id,
                "limit": policy.max_result_rows,
            }
            # Offer the anchor as a parameter, and require the generated query to use it.
            # Without this the model has nothing to bind the anchor to, so it inlines a
            # literal — and one generation dropped the anchor altogether, matching every
            # Account and expanding two hops from each (38,867,373 db hits for a question
            # about one account). The validator already turns a required parameter into a
            # `missing_parameter` violation that feeds the repair loop, so this reuses that
            # path rather than adding a new check.
            #
            # Only an unambiguous single candidate is passed. Two numbers in a question means
            # the anchor is genuinely uncertain, and forcing the wrong one is worse than
            # leaving generation free.
            required = policy.required_parameters
            anchor_hint: Optional[tuple] = None
            try:
                from .cypher_builder import CypherBuilder

                anchor_label = next(
                    (lbl for lbl in ("Account", *self.ontology.nodes)
                     if lbl in self.ontology.nodes), "")
                candidates = CypherBuilder(self.ontology).identity_candidates(
                    question, anchor_label)
                if len(set(candidates)) == 1:
                    anchor_key, anchor_value = candidates[0]
                    params["anchor_value"] = anchor_value
                    required = required + ("anchor_value",)
                    anchor_hint = (anchor_label, anchor_key)
            except Exception:  # a missing anchor must not break generation
                pass
            policy = replace(policy, required_parameters=required)
            schema = dict(schema_for_prompt(self.ontology, policy))
            if anchor_hint is not None:
                lbl, key = anchor_hint
                # Same shape as __tenant_scope__: a convention the model cannot infer from
                # property names alone. Without it the parameter was bound to whichever
                # identifier looked plausible.
                schema["__anchor__"] = (
                    f"$anchor_value is the {lbl}.{key} of the account the question is about; "
                    f"match it as {{{key}: $anchor_value}} and do not compare it against any "
                    f"other property",
                )
            result = _run_coroutine(generate_validated_cypher(
                question=question,
                schema=schema,
                params=params,
                policy=policy,
                backend=self.llm,
                model=self._model or getattr(self.llm, "model", "") or "",
                explain=self._explain,
            ))
        except Exception as exc:
            # Rejections are expected and informative, not errors to surface.
            self.last_rejection = f"{type(exc).__name__}: {exc}"[:300]
            logger.debug("validated generation declined: %s", self.last_rejection)
            return None
        return QueryPlan(
            question=question,
            cypher=result.cypher,
            params=dict(result.params),
            intent_data={
                "intent": "generated",
                "generation_attempts": result.attempts,
                "prompt_version": result.prompt_version,
            },
        )


def build_explain_callback(graph_store: Any, database: str) -> Optional[Any]:
    """An EXPLAIN callback over ``graph_store``, or None if it exposes no driver."""
    driver = getattr(graph_store, "_driver", None) or getattr(graph_store, "driver", None)
    if driver is None:
        return None

    async def explain(cypher: str, params: Mapping[str, Any]) -> None:
        with driver.session(database=database) as session:
            session.run("EXPLAIN " + cypher, **dict(params)).consume()

    return explain


__all__ = [
    "HybridQueryPlanner",
    "build_explain_callback",
    "policy_from_ontology",
    "query_precedence",
    "schema_for_prompt",
]
