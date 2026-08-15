"""AgentOS: the common operating layer, exposed through two interfaces.

The thesis this productizes: agentic systems need a common operating layer
for memory, execution, scheduling, governance, and resource management. In
SEOCHO every pillar's *mechanism* already exists — the workspace guardrail
and Cypher validation on the Bolt side, the ontology guardrail, admission
control, token budgets, the two-namespace memory contract. What agents
lacked was one object that binds them to the interfaces agents actually use:

- **Bolt-aware**: every tool an ``AgentOS`` hands out reads the graph
  through the governed store path — workspace scoping, ontology validation,
  fail-closed queries. The agent cannot reach the database around the layer.
- **OpenAI-compatible**: the layer speaks the OpenAI Agents SDK's own
  extension points (verified on openai-agents 0.10.3) — memory as the
  ``Session`` protocol, execution/resource control as ``RunHooks``,
  governance as ``tool_input_guardrail`` (via the existing adapter).
  ``Runner.run(agent, msg, session=os_session.sdk_session,
  hooks=os_session.hooks)`` is the whole integration.

Pillars → components:

| pillar     | component                                                    |
|------------|--------------------------------------------------------------|
| memory     | ``AgentOSSession.sdk_session`` — conversation namespace per   |
|            | the #494 contract (private per session); shared knowledge     |
|            | flows only through the governed graph tools                   |
| governance | ontology guardrail + enforcement mode (existing adapter)      |
| execution  | shared ``QueryAdmissionController`` gating every tool call;   |
|            | over-budget runs stop with ``BudgetExceededError`` — the      |
|            | structured-unknown semantics of ADR-0153, never a silent      |
|            | truncation                                                    |
| resource   | ``TokenBudgetTracker`` per session, fed from ``on_llm_end``   |
| scheduling | admission permits are shared across *all* sessions of one     |
|            | AgentOS — the process-local contention point; ``priority``    |
|            | is recorded per session for the fairness work (S1/vdw.11)     |

Scale is the design constraint, not an afterthought: nothing here holds
per-request state beyond the session objects, admission is O(1) per call,
and the scalability probe (scripts/agentos/scale_probe.py) exercises the
layer across the FinBench SF axis under concurrent sessions.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .budget import BudgetExceededError, TokenBudgetTracker
from .query.query_proxy import QueryAdmissionController

__all__ = [
    "AgentOS",
    "AgentOSSession",
    "BudgetExceededError",
    "SeochoSDKSession",
]


class SeochoSDKSession:
    """Conversation-namespace memory, speaking the SDK ``Session`` protocol.

    Implements ``agents.memory.Session`` (add_items/get_items/pop_item/
    clear_session) so ``Runner.run(..., session=...)`` persists turns here.
    Items live per (workspace_id, session_id) and are reachable only through
    this object — the private half of the two-namespace contract
    (docs/MEMORY_SHARING.md). Shared knowledge never travels this channel;
    it goes through the governed graph tools.

    v1 keeps items in process memory behind a lock (correct under the
    SDK's asyncio concurrency and our threaded probes). Durable backends
    (the PG authoritative-memory path) plug in behind the same protocol
    without touching callers.
    """

    def __init__(self, session_id: str, workspace_id: str) -> None:
        self.session_id = session_id
        self.workspace_id = workspace_id
        self._items: List[Any] = []
        self._lock = threading.Lock()

    async def add_items(self, items: List[Any]) -> None:
        with self._lock:
            self._items.extend(items)

    async def get_items(self, limit: Optional[int] = None) -> List[Any]:
        with self._lock:
            if limit is None or limit <= 0:
                return list(self._items)
            return list(self._items[-limit:])

    async def pop_item(self) -> Optional[Any]:
        with self._lock:
            return self._items.pop() if self._items else None

    async def clear_session(self) -> None:
        with self._lock:
            self._items.clear()


def _usage_tokens(usage: Any) -> int:
    for name in ("total_tokens",):
        value = getattr(usage, name, None)
        if isinstance(value, int):
            return value
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    return int(input_tokens) + int(output_tokens)


@dataclass
class AgentOSSession:
    """One agent's handle on the operating layer.

    Hand ``sdk_session`` and ``hooks`` to ``Runner.run``; build tools/agents
    through the parent ``AgentOS`` so every graph access stays governed.
    """

    os: "AgentOS"
    session_id: str
    priority: str = "normal"
    user_id: Optional[str] = None          # session/log plane only — never
    #                                        persisted into the graph (#494)
    sdk_session: SeochoSDKSession = field(init=False)
    budget: TokenBudgetTracker = field(init=False)

    def __post_init__(self) -> None:
        self.sdk_session = SeochoSDKSession(self.session_id, self.os.workspace_id)
        self.budget = TokenBudgetTracker(
            budget=self.os.token_budget, scope=f"session={self.session_id}")

    @property
    def hooks(self) -> Any:
        """RunHooks wiring execution + resource control into the SDK loop."""
        from agents import RunHooks

        session = self

        class _OSHooks(RunHooks):
            async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    # Raising here stops the run at a turn boundary: the
                    # ADR-0153 posture — a budget exhaustion is a structured
                    # outcome the caller sees, never a silently clipped answer.
                    session.budget.charge(completion=_usage_tokens(usage))

            # Admission is deliberately NOT taken here: the gate lives
            # inside the governed tool itself, so it holds even for callers
            # that bypass hooks — and a second acquisition here would double-
            # count permits per tool call.

        return _OSHooks()


class AgentOS:
    """The operating layer: one instance per (workspace, database) pair.

    All sessions created from one ``AgentOS`` share its admission permits —
    that shared pool *is* the scheduling substrate: N agents contending for
    the same graph go through one gate, whatever framework each runs on.
    """

    def __init__(
        self,
        *,
        ontology: Any,
        graph_store: Any,
        database: str,
        workspace_id: str = "default",
        enforcement: str = "guided",
        max_inflight: int = 8,
        admission_wait_s: float = 5.0,
        token_budget: int = 0,
        row_cap: int = 50,
    ) -> None:
        self.ontology = ontology
        self.graph_store = graph_store
        self.database = database
        self.workspace_id = workspace_id
        self.enforcement = enforcement
        self.token_budget = token_budget
        self.row_cap = row_cap
        self._admission = QueryAdmissionController(
            max_inflight=max_inflight, wait_seconds=admission_wait_s)
        self._sessions: Dict[str, AgentOSSession] = {}
        self._lock = threading.Lock()

    # -- memory -----------------------------------------------------------

    def session(self, session_id: Optional[str] = None, *,
                priority: str = "normal",
                user_id: Optional[str] = None) -> AgentOSSession:
        session_id = session_id or uuid.uuid4().hex[:12]
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = AgentOSSession(
                    os=self, session_id=session_id,
                    priority=priority, user_id=user_id)
            return self._sessions[session_id]

    # -- execution / scheduling -------------------------------------------

    def _admit(self, session: AgentOSSession) -> None:
        if not self._admission.acquire():
            from .query.query_proxy import QueryAdmissionRejected

            raise QueryAdmissionRejected(
                f"admission denied for session={session.session_id} "
                f"(priority={session.priority}): no capacity within deadline")

    def _release(self) -> None:
        self._admission.release()

    # -- governance / the Bolt-aware tool surface --------------------------

    def execute_query(self, session: AgentOSSession, cypher: str,
                      params_json: str = "{}") -> str:
        """The governed read path every AgentOS tool call funnels through.

        Order matters and is part of the contract: parse, pin tenancy,
        admit, execute fail-closed, release, cap-and-disclose. Admission
        rejection propagates (the SDK reports it as a tool failure the model
        sees); execution errors return as structured error payloads.
        """
        import json as _json

        try:
            params = _json.loads(params_json or "{}")
        except (TypeError, ValueError):
            return _json.dumps({"error": "params_json was not valid JSON"})
        if not isinstance(params, dict):
            return _json.dumps({"error": "params_json must be a JSON object"})
        params["workspace_id"] = self.workspace_id   # pinned, not trusted
        params["ws"] = self.workspace_id
        self._admit(session)
        try:
            rows = self.graph_store.query(
                cypher, params=params, database=self.database,
                enforce_workspace_filter=True)
        except Exception as exc:
            return _json.dumps({"error": type(exc).__name__,
                                "message": str(exc)[:300]})
        finally:
            self._release()
        capped = rows[: self.row_cap]
        return _json.dumps({"rows": capped, "row_count": len(capped),
                            "row_cap": self.row_cap,
                            "truncated": len(rows) > self.row_cap},
                           default=str)

    def make_graph_tool(self, session: AgentOSSession) -> Any:
        """The governed graph tool: pinned tenancy, gated execution.

        Two properties the plain adapter tool does not have, and which are
        exactly what an operating layer owes its tenants:

        - **Workspace pinning.** The model's ``workspace_id``/``ws``
          parameters are overwritten with this AgentOS's workspace before
          execution — the same "parameters the model must not be trusted
          with" rule the FinBench harness enforced. A prompt-injected
          workspace value cannot cross the boundary.
        - **Admission inside the call.** The shared permit is taken around
          the actual query, so the gate holds even for callers that skip
          the RunHooks path.

        Reads go through ``graph_store.query(..., enforce_workspace_filter=
        True)`` — fail-closed: Cypher that forgets the tenant scope is
        refused, not leaked.
        """

        from agents import function_tool

        os_layer = self

        @function_tool(
            name_override="graph_query",
            description_override=(
                "Run a read-only Cypher query against the governed graph. "
                "Pass values as named parameters ($name) via params_json. "
                "Include a LIMIT. The tenant scope is enforced by the layer."),
        )
        def graph_query(cypher: str, params_json: str = "{}") -> str:
            return os_layer.execute_query(session, cypher, params_json)

        from .integrations.openai_agents import make_ontology_guardrail

        graph_query.tool_input_guardrails = [
            make_ontology_guardrail(self.ontology)]
        return graph_query

    def build_agent(self, session: AgentOSSession, *,
                    name: str = "seocho_agent",
                    model: Optional[Any] = None,
                    extra_tools: Sequence[Any] = ()) -> Any:
        """An openai-agents Agent whose only graph access is this layer.

        Instructions carry the same schema block the deterministic planner
        uses (one rulebook), plus the truncation-honesty rule the FinBench
        disclosure study motivated.
        """
        import json as _json

        from agents import Agent

        from .query.hybrid_planner import policy_from_ontology, schema_for_prompt

        policy = policy_from_ontology(self.ontology)
        schema = schema_for_prompt(self.ontology, policy)
        instructions = (
            "You are an analyst querying a graph database with Cypher.\n\n"
            "Schema (use only these labels, relationship types and parameters):\n"
            + _json.dumps(schema, indent=2, default=str)
            + "\n\nRules:\n"
            "- Every matched node must carry the tenant scope shown in the schema.\n"
            "- Include a LIMIT; the tool caps rows regardless.\n"
            "- If the tool reports `truncated: true`, say so instead of presenting "
            "a partial result as complete.\n"
            "- If the schema cannot express the question, say what is missing "
            "instead of inventing labels.")
        tools = [self.make_graph_tool(session), *extra_tools]
        agent_kwargs: Dict[str, Any] = {"name": name,
                                        "instructions": instructions,
                                        "tools": tools}
        if model is not None:
            agent_kwargs["model"] = model
        return Agent(**agent_kwargs)

    # -- observability ------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "workspace_id": self.workspace_id,
                "database": self.database,
                "sessions": len(self._sessions),
                "max_inflight": self._admission.max_inflight,
                "token_budget": self.token_budget,
            }
