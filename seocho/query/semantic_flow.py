from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..indexing_design import build_query_reasoning_cycle_report
from ..observability import StageTimer
from .answering import build_evidence_bundle
from .constraints import SemanticConstraintSliceBuilder
from .run_registry import RunMetadataRegistry
from .semantic_agents import (
    AnswerGenerationAgent,
    LPGAgent,
    QueryRouterAgent,
    RDFAgent,
    SemanticEntityResolver,
)
from .strategy_chooser import ExecutionStrategyChooser


class SemanticAgentFlow:
    """Orchestrate semantic layer, route agents, and answer synthesis."""

    def __init__(
        self,
        connector: Any,
        *,
        graph_targets: Optional[Sequence[Any]] = None,
    ):
        self.resolver = SemanticEntityResolver(connector)
        self.router = QueryRouterAgent()
        self.lpg_agent = LPGAgent(connector, graph_targets=graph_targets)
        self.rdf_agent = RDFAgent(connector)
        self.answer_agent = AnswerGenerationAgent()
        self.constraint_builder = SemanticConstraintSliceBuilder(graph_targets=graph_targets)
        self.strategy_chooser = ExecutionStrategyChooser()
        self.run_registry = RunMetadataRegistry()

    def run(
        self,
        question: str,
        databases: Sequence[str],
        entity_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
        workspace_id: str = "default",
        reasoning_mode: bool = False,
        repair_budget: int = 0,
        reasoning_cycle: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        trace_steps: List[Dict[str, Any]] = []
        timer = StageTimer()

        with timer.stage("semantic_resolve"):
            semantic_context = self.resolver.resolve(question, databases, workspace_id=workspace_id)
        semantic_context.setdefault("query_diagnostics", [])
        semantic_context["reasoning_cycle_config"] = dict(reasoning_cycle or {})
        with timer.stage("constraint_context"):
            constraint_slices = self.constraint_builder.build_for_databases(
                databases,
                workspace_id=workspace_id,
            )
        semantic_context["semantic_layer"] = {
            "databases": {
                database: LPGAgent._summarize_constraint_slice(constraint_slice)
                for database, constraint_slice in constraint_slices.items()
            }
        }
        self._apply_entity_overrides(semantic_context, entity_overrides or {})
        with timer.stage("support_preview"):
            support_ranked_matches = self.lpg_agent.preview_support(semantic_context, constraint_slices)
        trace_steps.append(
            {
                "id": "0",
                "type": "SEMANTIC",
                "agent": "SemanticLayer",
                "content": "Entity extraction and disambiguation completed.",
                "metadata": {
                    "entities": semantic_context.get("entities", []),
                    "unresolved_entities": semantic_context.get("unresolved_entities", []),
                    "overrides_applied": sorted(
                        list(semantic_context.get("overrides_applied", {}).keys())
                    ),
                    "reasoning_mode": reasoning_mode,
                    "repair_budget": max(0, int(repair_budget or 0)),
                    "support_status": semantic_context.get("preflight_support_assessment", {}).get("status"),
                },
            }
        )

        with timer.stage("route"):
            route = self.router.route(question)
        semantic_context["strategy_decision"] = self.strategy_chooser.choose_initial(
            route=route,
            reasoning_mode=reasoning_mode,
            repair_budget=repair_budget,
            support_assessment=semantic_context.get("preflight_support_assessment", {}),
            graph_count=len(databases),
            cross_graph_analysis=semantic_context.get("cross_graph_analysis"),
        )
        trace_steps.append(
            {
                "id": "1",
                "type": "ROUTER",
                "agent": "RouterAgent",
                "content": f"Question routed to {route}.",
                "metadata": {
                    "route": route,
                    "initial_mode": semantic_context["strategy_decision"].get("initial_mode"),
                },
            }
        )
        trace_steps.append(
            {
                "id": "2",
                "type": "STRATEGY",
                "agent": "StrategyChooser",
                "content": semantic_context["strategy_decision"].get("reason", ""),
                "metadata": semantic_context["strategy_decision"],
            }
        )

        lpg_result: Optional[Dict[str, Any]] = None
        rdf_result: Optional[Dict[str, Any]] = None

        if route in {"lpg", "hybrid"}:
            with timer.stage("lpg_retrieval"):
                lpg_result = self.lpg_agent.run(
                    question,
                    databases,
                    semantic_context,
                    workspace_id=workspace_id,
                    reasoning_mode=reasoning_mode,
                    repair_budget=repair_budget,
                    constraint_slices=constraint_slices,
                    ranked_matches=support_ranked_matches,
                )
            if isinstance(lpg_result.get("evidence_bundle"), dict):
                semantic_context["evidence_bundle_preview"] = lpg_result["evidence_bundle"]
            if isinstance(lpg_result.get("reasoning"), dict):
                semantic_context["reasoning"] = lpg_result["reasoning"]
            if isinstance(lpg_result.get("support_assessment"), dict):
                semantic_context["support_assessment"] = lpg_result["support_assessment"]
            if isinstance(lpg_result.get("query_diagnostics"), list):
                semantic_context["query_diagnostics"] = list(lpg_result["query_diagnostics"])
            trace_steps.append(
                {
                    "id": "3",
                    "type": "SPECIALIST",
                    "agent": "LPGAgent",
                    "content": lpg_result.get("summary", ""),
                    "metadata": {
                        "records": len(lpg_result.get("records", [])),
                        "reasoning_attempts": int(
                            lpg_result.get("reasoning", {}).get("attempt_count", 0)
                        ),
                        "terminal_reason": lpg_result.get("reasoning", {}).get("terminal_reason"),
                        "support_status": lpg_result.get("support_assessment", {}).get("status"),
                        "tool_calls": lpg_result.get("reasoning", {}).get("repair_trace", []),
                    },
                }
            )

        if route in {"rdf", "hybrid"}:
            with timer.stage("rdf_retrieval"):
                rdf_result = self.rdf_agent.run(question, databases, semantic_context)
            trace_steps.append(
                {
                    "id": "4",
                    "type": "SPECIALIST",
                    "agent": "RDFAgent",
                    "content": rdf_result.get("summary", ""),
                    "metadata": {"records": len(rdf_result.get("records", []))},
                }
            )

        with timer.stage("strategy_finalize"):
            semantic_context["strategy_decision"] = self.strategy_chooser.finalize(
                initial_decision=semantic_context.get("strategy_decision", {}),
                route=route,
                graph_count=len(databases),
                support_assessment=semantic_context.get("support_assessment", {}),
                reasoning=semantic_context.get("reasoning"),
                cross_graph_analysis=semantic_context.get("cross_graph_analysis"),
            )

        with timer.stage("generation"):
            response = self.answer_agent.synthesize(
                question=question,
                route=route,
                semantic_context=semantic_context,
                lpg_result=lpg_result,
                rdf_result=rdf_result,
            )
        with timer.stage("run_registry"):
            semantic_context["run_metadata"] = self.run_registry.record_run(
                question=question,
                workspace_id=workspace_id,
                route=route,
                semantic_context=semantic_context,
                lpg_result=lpg_result,
                rdf_result=rdf_result,
                response=response,
            )
        reasoning_cycle_report = build_query_reasoning_cycle_report(
            reasoning_cycle,
            support_assessment=semantic_context.get("support_assessment", {}),
            query_diagnostics=semantic_context.get("query_diagnostics", []),
        )
        if reasoning_cycle_report is not None:
            semantic_context["reasoning_cycle"] = reasoning_cycle_report
        timer.mark_total()
        latency_breakdown_ms = self._latency_breakdown(timer.to_dict())
        agent_pattern = self._agent_pattern_receipt(
            trace_steps=trace_steps,
            semantic_context=semantic_context,
            reasoning_mode=reasoning_mode,
            repair_budget=repair_budget,
        )
        usage_estimate = self._estimate_usage(
            question=question,
            response=response,
            semantic_context=semantic_context,
            lpg_result=lpg_result,
            rdf_result=rdf_result,
        )
        answer_envelope = {
            "schema_version": "answer_envelope.v1",
            "answer": response,
            "answer_source": "semantic_flow",
            "support_assessment": semantic_context.get("support_assessment", {}),
            "evidence_bundle": semantic_context.get("evidence_bundle_preview", {}),
            "query_diagnostics": semantic_context.get("query_diagnostics", []),
            "latency_breakdown_ms": latency_breakdown_ms,
            "token_usage": usage_estimate,
            "agent_pattern": agent_pattern,
        }
        semantic_context["latency_breakdown_ms"] = latency_breakdown_ms
        semantic_context["agent_pattern"] = agent_pattern
        semantic_context["answer_envelope"] = answer_envelope
        trace_steps.append(
            {
                "id": "5",
                "type": "GENERATION",
                "agent": "AnswerGenerationAgent",
                "content": response,
                "metadata": {
                    "support_status": semantic_context.get("support_assessment", {}).get("status"),
                    "next_mode_hint": semantic_context.get("strategy_decision", {}).get("next_mode_hint"),
                    "usage_estimate": usage_estimate,
                    "latency_breakdown_ms": latency_breakdown_ms,
                    "agent_pattern": agent_pattern,
                },
            }
        )

        return {
            "response": response,
            "trace_steps": trace_steps,
            "route": route,
            "semantic_context": semantic_context,
            "lpg_result": lpg_result,
            "rdf_result": rdf_result,
            "support_assessment": semantic_context.get("support_assessment", {}),
            "strategy_decision": semantic_context.get("strategy_decision", {}),
            "run_metadata": semantic_context.get("run_metadata", {}),
            "evidence_bundle": semantic_context.get("evidence_bundle_preview", {}),
            "query_diagnostics": semantic_context.get("query_diagnostics", []),
            "reasoning_cycle": reasoning_cycle_report or {},
            "latency_breakdown_ms": latency_breakdown_ms,
            "agent_pattern": agent_pattern,
            "answer_envelope": answer_envelope,
        }

    @staticmethod
    def _apply_entity_overrides(
        semantic_context: Dict[str, Any],
        entity_overrides: Dict[str, Dict[str, Any]],
    ) -> None:
        if not entity_overrides:
            return

        matches = semantic_context.setdefault("matches", {})
        unresolved = set(semantic_context.get("unresolved_entities", []))
        applied: Dict[str, Dict[str, Any]] = {}

        for question_entity, override in entity_overrides.items():
            if not question_entity:
                continue

            db_name = override.get("database")
            node_id = override.get("node_id")
            if db_name is None or node_id is None:
                continue

            candidate = {
                "database": str(db_name),
                "entity_text": question_entity,
                "node_id": node_id,
                "labels": override.get("labels", []),
                "display_name": override.get("display_name", question_entity),
                "base_score": 1.0,
                "source": "override",
                "index_name": None,
                "lexical_score": 1.0,
                "label_boost": 0.0,
                "alias_boost": 0.0,
                "final_score": 10.0,
            }

            existing = matches.get(question_entity, [])
            matches[question_entity] = [candidate] + [
                row for row in existing
                if not (row.get("database") == candidate["database"] and row.get("node_id") == candidate["node_id"])
            ]
            unresolved.discard(question_entity)
            applied[question_entity] = {
                "database": candidate["database"],
                "node_id": candidate["node_id"],
                "display_name": candidate["display_name"],
            }

        semantic_context["unresolved_entities"] = sorted(unresolved)
        if applied:
            semantic_context["overrides_applied"] = applied
            semantic_context["evidence_bundle_preview"] = build_evidence_bundle(
                question="",
                semantic_context=semantic_context,
                matched_entities=semantic_context.get("entities", []),
            )

    @staticmethod
    def _estimate_usage(
        *,
        question: str,
        response: str,
        semantic_context: Dict[str, Any],
        lpg_result: Optional[Dict[str, Any]],
        rdf_result: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        context_chars = len(str(semantic_context))
        context_chars += len(str(lpg_result or {}))
        context_chars += len(str(rdf_result or {}))
        input_tokens = max(1, round((len(question) + context_chars) / 4))
        output_tokens = max(1, round(len(response) / 4)) if response else 0
        return {
            "source": "estimated_char_count",
            "exact": False,
            "input_tokens_est": input_tokens,
            "output_tokens_est": output_tokens,
            "total_tokens_est": input_tokens + output_tokens,
        }

    @staticmethod
    def _latency_breakdown(timings: Dict[str, float]) -> Dict[str, float]:
        retrieval_keys = (
            "semantic_resolve_ms",
            "constraint_context_ms",
            "support_preview_ms",
            "route_ms",
            "lpg_retrieval_ms",
            "rdf_retrieval_ms",
            "strategy_finalize_ms",
        )
        payload = dict(timings)
        payload["retrieval_ms"] = round(
            sum(float(payload.get(key, 0.0) or 0.0) for key in retrieval_keys),
            2,
        )
        payload["generation_ms"] = round(float(payload.get("generation_ms", 0.0) or 0.0), 2)
        return payload

    @staticmethod
    def _agent_pattern_receipt(
        *,
        trace_steps: Sequence[Dict[str, Any]],
        semantic_context: Dict[str, Any],
        reasoning_mode: bool,
        repair_budget: int,
    ) -> Dict[str, Any]:
        strategy = semantic_context.get("strategy_decision", {})
        if not isinstance(strategy, dict):
            strategy = {}
        support = semantic_context.get("support_assessment", {})
        if not isinstance(support, dict):
            support = {}
        executed_mode = str(strategy.get("executed_mode") or strategy.get("initial_mode") or "").strip()
        support_status = str(support.get("status", "") or "").strip()
        if executed_mode in {"debate", "planning_multi_agent"}:
            pattern = "planning_multi_agent"
            reason = "strategy_escalation"
        elif reasoning_mode or support_status in {"partial", "unsupported"}:
            pattern = "reflection_chain"
            reason = "reasoning_or_partial_support"
        else:
            pattern = "semantic_direct"
            reason = "supported_direct_semantic_path"
        return {
            "schema_version": "agent_pattern_receipt.v1",
            "pattern": pattern,
            "reason": reason,
            "executed_mode": executed_mode or "semantic_direct",
            "turn_count": len(trace_steps) + 1,
            "tool_like_steps": sum(
                1 for step in trace_steps if step.get("type") in {"SPECIALIST", "STRATEGY", "SEMANTIC"}
            ),
            "repair_budget": max(0, int(repair_budget or 0)),
            "support_status": support_status,
        }


__all__ = ["SemanticAgentFlow"]
