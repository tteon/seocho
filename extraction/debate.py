"""
Parallel Debate Orchestrator

Implements the Society-of-Mind pattern: all mapped agents answer
the user's question independently and in parallel, then a Supervisor
synthesises the results into a single coherent response.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from agents import Agent, Runner, trace

from shared_memory import SharedMemory
from tracing import track, update_current_span, update_current_trace

logger = logging.getLogger(__name__)


@dataclass
class DebateResult:
    """Result from a single agent in the debate."""
    agent_name: str
    db_name: str
    response: str
    trace_steps: List[Dict[str, Any]] = field(default_factory=list)


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
    ):
        self.agents = agents          # {db_name: Agent}
        self.supervisor = supervisor
        self.shared_memory = shared_memory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @track("debate.run_debate")
    async def run_debate(
        self, query: str, context: Any
    ) -> Dict[str, Any]:
        """Execute full debate cycle: fan-out → collect → synthesise."""

        agent_names = [a.name for a in self.agents.values()]
        db_names = list(self.agents.keys())
        update_current_trace(
            metadata={"query": query[:200], "mode": "parallel_debate"},
            tags=["debate"],
        )
        update_current_span(
            metadata={
                "phase": "orchestration",
                "agent_count": len(self.agents),
                "agent_names": agent_names,
                "db_names": db_names,
            },
        )

        # 1. Parallel execution (fan-out)
        tasks = [
            self._run_single_agent(db_name, agent, query, context)
            for db_name, agent in self.agents.items()
        ]
        debate_results: List[DebateResult] = await asyncio.gather(*tasks)

        # 2. Store results in shared memory (collect)
        for result in debate_results:
            self.shared_memory.put(
                f"agent_result:{result.db_name}", result.response
            )

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
                    "db": r.db_name,
                    "response": r.response,
                }
                for r in debate_results
            ],
        }

    # ------------------------------------------------------------------
    # Single agent execution (error-isolated)
    # ------------------------------------------------------------------

    @track("debate.run_single_agent")
    async def _run_single_agent(
        self, db_name: str, agent: Agent, query: str, context: Any
    ) -> DebateResult:
        update_current_span(
            metadata={
                "phase": "fan-out",
                "db_name": db_name,
                "agent_name": agent.name,
            },
            tags=[f"db:{db_name}", "debate-agent"],
        )
        try:
            with trace(f"Debate:{agent.name}"):
                result = await Runner.run(
                    agent=agent, input=query, context=context
                )
            response_text = str(result.final_output)
            update_current_span(
                output={"response_preview": response_text[:300]},
            )
            return DebateResult(
                agent_name=agent.name,
                db_name=db_name,
                response=response_text,
                trace_steps=self._extract_trace(result),
            )
        except Exception as e:
            logger.error("Agent %s failed: %s", agent.name, e)
            update_current_span(
                metadata={"error": str(e)},
                tags=["error"],
            )
            return DebateResult(
                agent_name=agent.name,
                db_name=db_name,
                response=f"Error: {e}",
                trace_steps=[],
            )

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
        with trace("Supervisor Synthesis"):
            result = await Runner.run(
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
            parts.append(
                f"--- {r.agent_name} ({r.db_name}) ---\n{r.response}\n"
            )
        parts.append(
            "\nSynthesize these responses into a single, coherent answer. "
            "Highlight agreements and note disagreements."
        )
        return "\n".join(parts)

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

                steps.append({
                    "id": str(step_id),
                    "type": sub_type,
                    "agent": r.agent_name,
                    "content": sub_content[:120],
                    "metadata": {
                        "node_id": sub_id,
                        "parent_id": prev_sub_id,
                        "phase": "fan-out",
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
