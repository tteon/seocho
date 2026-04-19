"""
Parallel Debate Orchestrator

Implements the Society-of-Mind pattern: all mapped agents answer
the user's question independently and in parallel, then a Supervisor
synthesises the results into a single coherent response.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agents import Agent

from agents_runtime import get_agents_runtime
from shared_memory import SharedMemory
from tracing import track, update_current_span, update_current_trace

logger = logging.getLogger(__name__)


_NO_DATA_RE = re.compile(
    r"\b(no data|no available data|no matching|no relevant|not find|could not find|"
    r"cannot find|do not have|don't have|not available|no available information|"
    r"no (?:specific )?information(?:\s+recorded|\s+about|\s+regarding)?|"
    r"no information provided(?:\s+about)?|no detailed information(?:\s+regarding)?|"
    r"no records(?:\s+of)?|do not have records|did not yield any results|"
    r"no (?:specific )?factors(?:\s+linked)?|no recorded .{1,80}?|"
    r"no .{1,80}? listed|could not retrieve any data|do not see any (?:nodes|data)|"
    r"unable to answer|outside (?:the )?current scope|"
    r"does not show any information|do(?:es)? not explicitly record|no .{1,80}? found|"
    r"does not provide information|lack[s]? (?:revenue|growth|requested|relevant|specific|valid|"
    r"supporting|source)? ?(?:data|information)|outside (?:my|the) graph)\b",
    re.IGNORECASE,
)


@dataclass
class DebateResult:
    """Result from a single agent in the debate."""
    agent_name: str
    graph_id: str
    db_name: str
    response: str
    trace_steps: List[Dict[str, Any]] = field(default_factory=list)
    semantic_reused: bool = False
    semantic_route: str = ""
    support_assessment: Dict[str, Any] = field(default_factory=dict)
    evidence_bundle: Dict[str, Any] = field(default_factory=dict)
    strategy_decision: Dict[str, Any] = field(default_factory=dict)
    reasoning_cycle: Dict[str, Any] = field(default_factory=dict)


class DebateOrchestrator:
    """Parallel Debate pattern orchestrator.

    Flow:
    1. Fan-out: all agents execute the query in parallel (asyncio.gather).
    2. Collect: results are stored in SharedMemory.
    3. Synthesise: Supervisor receives all results and produces final answer.
    """

    def __init__(
        self,
        agents: Dict[str, Agent],
        supervisor: Agent,
        shared_memory: SharedMemory,
        agents_runtime=None,
    ):
        self.agents = agents          # {graph_id: Agent}
        self.supervisor = supervisor
        self.shared_memory = shared_memory
        self._agents_runtime = agents_runtime or get_agents_runtime()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @track("debate.run_debate")
    async def run_debate(
        self, query: str, context: Any
    ) -> Dict[str, Any]:
        """Execute full debate cycle: fan-out → collect → synthesise."""

        agent_names = [a.name for a in self.agents.values()]
        graph_ids = list(self.agents.keys())
        update_current_trace(
            metadata={"query": query[:200], "mode": "parallel_debate"},
            tags=["debate"],
        )
        update_current_span(
            metadata={
                "phase": "orchestration",
                "agent_count": len(self.agents),
                "agent_names": agent_names,
                "graph_ids": graph_ids,
            },
        )

        # 1. Parallel execution (fan-out)
        tasks = [
            self._run_single_agent(graph_id, agent, query, context)
            for graph_id, agent in self.agents.items()
        ]
        debate_results: List[DebateResult] = await asyncio.gather(*tasks)

        # 2. Store results in shared memory (collect)
        for result in debate_results:
            self.shared_memory.put(
                f"agent_result:{result.graph_id}", result.response
            )

        direct_semantic = self._direct_semantic_resolution(debate_results)
        if direct_semantic is not None:
            all_trace_steps = self._build_debate_trace(
                debate_results,
                supervisor_result=None,
                bypass_reason="single_supported_semantic_reuse",
            )
            return {
                "response": direct_semantic.response,
                "trace_steps": all_trace_steps,
                "debate_results": [
                    {
                        "agent": r.agent_name,
                        "graph": r.graph_id,
                        "db": r.db_name,
                        "response": r.response,
                    }
                    for r in debate_results
                ],
                "reasoning_cycle": direct_semantic.reasoning_cycle,
            }

        # 3. Synthesise with Supervisor
        supervisor_result = await self._run_supervisor(
            query, debate_results, context
        )

        # 4. Build unified trace for UI
        all_trace_steps = self._build_debate_trace(
            debate_results, supervisor_result
        )

        return {
            "response": str(supervisor_result.final_output),
            "trace_steps": all_trace_steps,
            "debate_results": [
                {
                    "agent": r.agent_name,
                    "graph": r.graph_id,
                    "db": r.db_name,
                    "response": r.response,
                }
                for r in debate_results
            ],
            "reasoning_cycle": self._collect_reasoning_cycle(debate_results),
        }

    # ------------------------------------------------------------------
    # Single agent execution (error-isolated)
    # ------------------------------------------------------------------

    @track("debate.run_single_agent")
    async def _run_single_agent(
        self, graph_id: str, agent: Agent, query: str, context: Any
    ) -> DebateResult:
        db_name = str(getattr(agent, "graph_database", graph_id))
        update_current_span(
            metadata={
                "phase": "fan-out",
                "graph_id": graph_id,
                "agent_name": agent.name,
            },
            tags=[f"graph:{graph_id}", "debate-agent"],
        )
        semantic_result = await self._run_semantic_graph_flow(
            db_name=db_name,
            query=query,
            context=context,
        )
        semantic_support = self._semantic_support_summary(semantic_result)
        preflight_reasoning_cycle = dict(semantic_support.get("reasoning_cycle", {}))
        if semantic_support["supported"]:
            response_text = semantic_support["response"]
            update_current_span(
                output={"response_preview": response_text[:300]},
                metadata={"semantic_preflight_used": True},
            )
            return DebateResult(
                agent_name=agent.name,
                graph_id=graph_id,
                db_name=db_name,
                response=response_text,
                trace_steps=[
                    {
                        "id": "semantic-preflight",
                        "type": "DETERMINISTIC_PREFLIGHT",
                        "role": "system",
                        "content": (
                            "SemanticAgentFlow provided supported graph evidence before "
                            "graph-agent debate execution."
                        ),
                        "tool_names": ["semantic_agent_flow"],
                        "metadata": {
                            "graph": graph_id,
                            "db": db_name,
                            "support_status": semantic_support["status"],
                            "records": semantic_support["records"],
                            "preflight_reason": "supported_graph_evidence",
                        },
                    }
                ],
                semantic_reused=True,
                semantic_route=str(semantic_support.get("route", "")),
                support_assessment=dict(semantic_support.get("support_assessment", {})),
                evidence_bundle=dict(semantic_support.get("evidence_bundle", {})),
                strategy_decision=dict(semantic_support.get("strategy_decision", {})),
                reasoning_cycle=preflight_reasoning_cycle,
            )
        try:
            with self._agents_runtime.trace(f"Debate:{agent.name}"):
                result = await self._agents_runtime.run(
                    agent=agent,
                    input=query,
                    context=context,
                )
            response_text = str(result.final_output)
            trace_steps = self._extract_trace(result)
            fallback = await self._semantic_graph_fallback(
                graph_id=graph_id,
                db_name=db_name,
                query=query,
                response_text=response_text,
                context=context,
            )
            if fallback is not None:
                response_text = fallback["response"]
                trace_steps.extend(fallback["trace_steps"])
            update_current_span(
                output={"response_preview": response_text[:300]},
            )
            return DebateResult(
                agent_name=agent.name,
                graph_id=graph_id,
                db_name=db_name,
                response=response_text,
                trace_steps=trace_steps,
                reasoning_cycle=(
                    dict(fallback.get("reasoning_cycle", {}))
                    if fallback is not None
                    else preflight_reasoning_cycle
                ) or preflight_reasoning_cycle,
            )
        except Exception as e:
            logger.error("Agent %s failed: %s", agent.name, e)
            update_current_span(
                metadata={"error": str(e)},
                tags=["error"],
            )
            fallback = await self._semantic_graph_fallback(
                graph_id=graph_id,
                db_name=db_name,
                query=query,
                response_text=f"Error: {e}",
                context=context,
                force=True,
            )
            if fallback is not None:
                return DebateResult(
                    agent_name=agent.name,
                    graph_id=graph_id,
                    db_name=db_name,
                    response=fallback["response"],
                    trace_steps=fallback["trace_steps"],
                    semantic_reused=True,
                    semantic_route=str(fallback.get("semantic_route", "")),
                    support_assessment=dict(fallback.get("support_assessment", {})),
                    evidence_bundle=dict(fallback.get("evidence_bundle", {})),
                    strategy_decision=dict(fallback.get("strategy_decision", {})),
                    reasoning_cycle=dict(fallback.get("reasoning_cycle", {})) or preflight_reasoning_cycle,
                )
            return DebateResult(
                agent_name=agent.name,
                graph_id=graph_id,
                db_name=db_name,
                response=f"Error: {e}",
                trace_steps=[],
                reasoning_cycle=preflight_reasoning_cycle,
            )

    async def _run_semantic_graph_flow(
        self,
        *,
        db_name: str,
        query: str,
        context: Any,
    ) -> Optional[Dict[str, Any]]:
        semantic_flow = getattr(context, "semantic_agent_flow", None)
        if semantic_flow is None:
            return None

        try:
            result = await asyncio.to_thread(
                semantic_flow.run,
                question=query,
                databases=[db_name],
                entity_overrides={},
                workspace_id=str(getattr(context, "workspace_id", "default")),
                reasoning_mode=False,
                repair_budget=0,
                reasoning_cycle=dict(getattr(context, "reasoning_cycle", {}) or {}),
            )
        except Exception as exc:
            logger.debug(
                "Semantic graph flow skipped for database %s: %s",
                db_name,
                exc,
                exc_info=True,
            )
            return None
        return result if isinstance(result, dict) else None

    @staticmethod
    def _semantic_support_summary(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {
                "response": "",
                "status": "",
                "supported": False,
                "records": 0,
                "route": "",
                "support_assessment": {},
                "evidence_bundle": {},
                "strategy_decision": {},
            }

        support = result.get("support_assessment", {})
        status = str(support.get("status", "")).lower() if isinstance(support, dict) else ""
        supported = bool(support.get("supported")) if isinstance(support, dict) else False
        lpg_payload = result.get("lpg_result") or {}
        rdf_payload = result.get("rdf_result") or {}
        lpg_records = lpg_payload.get("records", []) if isinstance(lpg_payload, dict) else []
        rdf_records = rdf_payload.get("records", []) if isinstance(rdf_payload, dict) else []
        response = str(result.get("response", "")).strip()
        evidence_bundle = result.get("evidence_bundle") or result.get("evidence_bundle_preview") or {}
        strategy_decision = result.get("strategy_decision") or {}
        reasoning_cycle = result.get("reasoning_cycle") or result.get("semantic_context", {}).get("reasoning_cycle") or {}
        return {
            "response": response,
            "status": status,
            "supported": bool(response) and (supported or status == "supported"),
            "records": len(lpg_records) + len(rdf_records),
            "has_records": bool(lpg_records or rdf_records),
            "route": str(result.get("route", "") or ""),
            "support_assessment": dict(support) if isinstance(support, dict) else {},
            "evidence_bundle": dict(evidence_bundle) if isinstance(evidence_bundle, dict) else {},
            "strategy_decision": dict(strategy_decision) if isinstance(strategy_decision, dict) else {},
            "reasoning_cycle": dict(reasoning_cycle) if isinstance(reasoning_cycle, dict) else {},
        }

    async def _semantic_graph_fallback(
        self,
        *,
        graph_id: str,
        db_name: str,
        query: str,
        response_text: str,
        context: Any,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if not force and not self._should_fallback_to_semantic(response_text):
            return None

        result = await self._run_semantic_graph_flow(
            db_name=db_name,
            query=query,
            context=context,
        )
        if result is None:
            return None

        semantic_support = self._semantic_support_summary(result)
        if not semantic_support["response"] or not (
            semantic_support["supported"] or semantic_support["has_records"]
        ):
            return None

        return {
            "response": semantic_support["response"],
            "trace_steps": [
                {
                    "id": "semantic-fallback",
                    "type": "DETERMINISTIC_FALLBACK",
                    "role": "system",
                    "content": (
                        "Graph agent returned an ungrounded/no-data answer; "
                        "SemanticAgentFlow supplied deterministic graph evidence."
                    ),
                    "tool_names": ["semantic_agent_flow"],
                    "metadata": {
                        "graph": graph_id,
                        "db": db_name,
                        "support_status": semantic_support["status"],
                        "records": semantic_support["records"],
                        "fallback_reason": "agent_error" if force else "no_data_response",
                    },
                }
            ],
            "semantic_route": semantic_support["route"],
            "support_assessment": semantic_support["support_assessment"],
            "evidence_bundle": semantic_support["evidence_bundle"],
            "strategy_decision": semantic_support["strategy_decision"],
            "reasoning_cycle": semantic_support["reasoning_cycle"],
        }

    @staticmethod
    def _collect_reasoning_cycle(debate_results: List[DebateResult]) -> Dict[str, Any]:
        graph_reports = [
            {
                "graph": result.graph_id,
                "database": result.db_name,
                "report": result.reasoning_cycle,
            }
            for result in debate_results
            if result.reasoning_cycle
        ]
        if not graph_reports:
            return {}
        if len(graph_reports) == 1:
            return dict(graph_reports[0]["report"])

        primary = dict(graph_reports[0]["report"])
        primary["graph_reports"] = graph_reports
        primary["status"] = "anomaly_detected"
        primary["next_phase"] = "abduction"
        return primary

    @staticmethod
    def _should_fallback_to_semantic(response_text: str) -> bool:
        return bool(_NO_DATA_RE.search(str(response_text or "")))

    # ------------------------------------------------------------------
    # Supervisor synthesis (traced)
    # ------------------------------------------------------------------

    @track("debate.supervisor_synthesis")
    async def _run_supervisor(
        self, query: str, debate_results: List[DebateResult], context: Any
    ):
        update_current_span(
            metadata={
                "phase": "synthesis",
                "input_agent_count": len(debate_results),
                "input_agents": [r.agent_name for r in debate_results],
            },
            tags=["supervisor"],
        )
        synthesis_input = self._format_for_supervisor(query, debate_results)
        with self._agents_runtime.trace("Supervisor Synthesis"):
            result = await self._agents_runtime.run(
                agent=self.supervisor,
                input=synthesis_input,
                context=context,
            )
        update_current_span(
            output={"synthesis_preview": str(result.final_output)[:300]},
        )
        return result

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_for_supervisor(
        query: str, results: List[DebateResult]
    ) -> str:
        parts = [f"Original Question: {query}\n\nAgent Responses:\n"]
        for r in results:
            header = f"--- {r.agent_name} (graph={r.graph_id}, database={r.db_name}) ---"
            parts.append(header)
            if r.semantic_reused:
                parts.append("Semantic evidence reused: yes")
            support_status = str(r.support_assessment.get("status", "")).strip()
            if support_status:
                parts.append(f"Support status: {support_status}")
            evidence_summary = DebateOrchestrator._summarize_evidence_bundle(r.evidence_bundle)
            if evidence_summary:
                parts.append(f"Evidence bundle: {json.dumps(evidence_summary, ensure_ascii=False, sort_keys=True)}")
            parts.append(r.response)
            parts.append("")
        parts.append(
            "\nSynthesize these responses into a single, coherent answer. "
            "Highlight agreements and note disagreements."
        )
        return "\n".join(parts)

    @staticmethod
    def _summarize_evidence_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(bundle, dict):
            return {}
        summary: Dict[str, Any] = {}
        for key in ("intent_id", "grounded_slots", "missing_slots"):
            value = bundle.get(key)
            if value:
                summary[key] = value
        slot_fills = bundle.get("slot_fills")
        if isinstance(slot_fills, dict) and slot_fills:
            summary["slot_fills"] = slot_fills
        selected = bundle.get("selected_triples")
        if isinstance(selected, list) and selected:
            summary["selected_triples"] = selected[:3]
        return summary

    @staticmethod
    def _direct_semantic_resolution(results: List[DebateResult]) -> Optional[DebateResult]:
        if len(results) != 1:
            return None
        result = results[0]
        status = str(result.support_assessment.get("status", "")).lower()
        if result.semantic_reused and status == "supported" and result.response:
            return result
        return None

    @staticmethod
    def _extract_trace(result) -> List[Dict[str, Any]]:
        """Extract detailed trace steps from a Runner result.

        Captures each message's role, content, tool calls, and tool names
        so the UI can show the agent's full reasoning chain.
        """
        history = getattr(result, "chat_history", [])
        if not history:
            history = getattr(result, "messages", [])
        steps = []
        for i, msg in enumerate(history):
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "") or ""

            # Determine step type and extract tool info
            step_type = "UNKNOWN"
            tool_names = []
            if role == "user":
                step_type = "THOUGHT"  # agent's internal prompt
            elif role == "assistant":
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    step_type = "TOOL_CALL"
                    tool_names = [tc.function.name for tc in tool_calls]
                    content = f"Calling: {', '.join(tool_names)}"
                else:
                    step_type = "REASONING"
            elif role == "tool":
                step_type = "TOOL_OUTPUT"

            steps.append({
                "id": str(i),
                "type": step_type,
                "role": role,
                "content": str(content),
                "tool_names": tool_names,
            })
        return steps

    # ------------------------------------------------------------------
    # Trace construction for Streamlit visualization
    # ------------------------------------------------------------------

    @staticmethod
    def _build_debate_trace(
        debate_results: List[DebateResult],
        supervisor_result,
        *,
        bypass_reason: str = "",
    ) -> List[Dict[str, Any]]:
        """Build a trace structure for the Streamlit agent flow graph.

        Expanded topology (each agent's internal reasoning is visible):

            FANOUT
              ├── DEBATE: Agent_kgnormal
              │     ├── TOOL_CALL: get_schema
              │     ├── TOOL_OUTPUT: {schema...}
              │     ├── TOOL_CALL: query_db
              │     ├── TOOL_OUTPUT: [{results...}]
              │     └── REASONING: "Based on the results..."
              ├── DEBATE: Agent_kgfibo
              │     └── ...
              └── ...
            COLLECT
            SYNTHESIS: Supervisor
        """
        steps: List[Dict[str, Any]] = []
        step_id = 0

        # Fan-out node
        fanout_node_id = f"node_fanout_{step_id}"
        steps.append({
            "id": str(step_id),
            "type": "FANOUT",
            "agent": "DebateOrchestrator",
            "content": "Parallel debate started",
            "metadata": {
                "node_id": fanout_node_id,
                "phase": "orchestration",
                "agents": [r.agent_name for r in debate_results],
                "full_content": (
                    f"Dispatching query to {len(debate_results)} agents: "
                    + ", ".join(r.agent_name for r in debate_results)
                ),
            },
        })
        step_id += 1

        # Each agent: DEBATE header + internal sub-steps
        last_step_per_agent: List[str] = []  # last step id for each agent branch

        for r in debate_results:
            # DEBATE header node
            debate_node_id = f"node_debate_{step_id}"
            steps.append({
                "id": str(step_id),
                "type": "DEBATE",
                "agent": r.agent_name,
                "content": r.response[:80],
                "metadata": {
                    "node_id": debate_node_id,
                    "parent_id": fanout_node_id,
                    "phase": "fan-out",
                    "graph": r.graph_id,
                    "db": r.db_name,
                    "full_content": r.response,
                },
            })
            step_id += 1

            # Internal trace sub-steps (chained under DEBATE node)
            prev_sub_id = debate_node_id
            for ts in r.trace_steps:
                sub_id = f"node_step_{step_id}"

                # Map internal types to display types
                sub_type = ts.get("type", "UNKNOWN")
                sub_content = ts.get("content", "")
                sub_metadata = ts.get("metadata", {})
                if not isinstance(sub_metadata, dict):
                    sub_metadata = {}

                steps.append({
                    "id": str(step_id),
                    "type": sub_type,
                    "agent": r.agent_name,
                    "content": sub_content[:120],
                    "metadata": {
                        **sub_metadata,
                        "node_id": sub_id,
                        "parent_id": prev_sub_id,
                        "phase": "fan-out",
                        "graph": r.graph_id,
                        "db": r.db_name,
                        "full_content": sub_content,
                        "tool_names": ts.get("tool_names", []),
                    },
                })
                prev_sub_id = sub_id
                step_id += 1

            last_step_per_agent.append(prev_sub_id)

        # Collect node — edges come from last step of each agent branch
        collect_id = f"node_collect_{step_id}"
        steps.append({
            "id": str(step_id),
            "type": "COLLECT",
            "agent": "DebateOrchestrator",
            "content": f"Collecting {len(debate_results)} results",
            "metadata": {
                "node_id": collect_id,
                "parent_ids": last_step_per_agent,
                "phase": "orchestration",
                "full_content": "All agent responses collected for supervisor synthesis.",
            },
        })
        step_id += 1

        # Supervisor synthesis
        if supervisor_result is None:
            steps.append({
                "id": str(step_id),
                "type": "SYNTHESIS_BYPASSED",
                "agent": "DebateOrchestrator",
                "content": "Deterministic semantic evidence reused directly.",
                "metadata": {
                    "node_id": f"node_synthesis_{step_id}",
                    "parent_id": collect_id,
                    "phase": "synthesis",
                    "full_content": "Supervisor synthesis was bypassed because a single graph returned supported semantic evidence.",
                    "bypass_reason": bypass_reason,
                },
            })
            return steps

        supervisor_output = str(
            getattr(supervisor_result, "final_output", "")
        )
        steps.append({
            "id": str(step_id),
            "type": "SYNTHESIS",
            "agent": "Supervisor",
            "content": supervisor_output[:120],
            "metadata": {
                "node_id": f"node_synthesis_{step_id}",
                "parent_id": collect_id,
                "phase": "synthesis",
                "full_content": supervisor_output,
            },
        })

        return steps
