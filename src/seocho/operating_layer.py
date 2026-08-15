"""The operating layer behind ``Seocho`` (memory/execution/scheduling/governance/resource).

Public surface: methods on ``seocho.Seocho`` itself — ``session()``,
``build_agent()``, ``execute_query()`` (seocho-dxe: no side brand, no
suffix). This module is the implementation; ``SeochoOS``/``AgentOS`` remain
importable one release for anything written against the earlier spellings.

The thesis this productizes: agentic systems need a common operating layer
for memory, execution, scheduling, governance, and resource management. In
SEOCHO every pillar's *mechanism* already exists — the workspace guardrail
and Cypher validation on the Bolt side, the ontology guardrail, admission
control, token budgets, the two-namespace memory contract. What agents
lacked was one object that binds them to the interfaces agents actually use:

- **Bolt-aware**: every tool a ``SeochoOS`` hands out reads the graph
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
| memory     | ``OSSession.sdk_session`` — conversation namespace per   |
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
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .budget import BudgetExceededError, TokenBudgetTracker

__all__ = [
    "AgentOS",
    "PriorityAdmission",
    "OSSession",
    "BudgetExceededError",
    "SeochoSDKSession",
]


class PriorityAdmission:
    """Two-class admission with a protected reserve — the scheduling pillar.

    ``high`` may use every permit; ``normal`` is capped at
    ``max_inflight - reserved_for_high``. The invariant that matters for
    fairness: a saturating crowd of normal sessions can never occupy the
    reserve, so a high-priority arrival always finds capacity within one
    release. ``reserved_for_high=0`` degrades to plain bounded admission,
    and ``max_inflight=0`` disables the gate (compat with the ungoverned
    posture). Condition-variable based so waiters block without spinning
    and every release wakes the queue exactly once.
    """

    def __init__(self, *, max_inflight: int = 0, reserved_for_high: int = 0,
                 wait_seconds: float = 5.0) -> None:
        if max_inflight < 0 or reserved_for_high < 0:
            raise ValueError("admission sizes must be non-negative")
        if max_inflight and reserved_for_high >= max_inflight:
            raise ValueError("the reserve must leave normal capacity")
        self.max_inflight = max_inflight
        self.reserved_for_high = reserved_for_high
        self.wait_seconds = wait_seconds
        self._in_use = {"high": 0, "normal": 0}
        self._cond = threading.Condition()

    def _admissible(self, priority: str) -> bool:
        total = self._in_use["high"] + self._in_use["normal"]
        if total >= self.max_inflight:
            return False
        if priority != "high":
            normal_cap = self.max_inflight - self.reserved_for_high
            if self._in_use["normal"] >= normal_cap:
                return False
        return True

    def acquire(self, priority: str = "normal") -> bool:
        if not self.max_inflight:
            return True
        key = "high" if priority == "high" else "normal"
        deadline = time.monotonic() + self.wait_seconds
        with self._cond:
            while not self._admissible(key):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            self._in_use[key] += 1
            self._report()
            return True

    def release(self, priority: str = "normal") -> None:
        if not self.max_inflight:
            return
        key = "high" if priority == "high" else "normal"
        with self._cond:
            self._in_use[key] -= 1
            self._report()
            self._cond.notify_all()

    def _report(self) -> None:
        try:
            from .metrics import get_metrics

            total = self._in_use["high"] + self._in_use["normal"]
            get_metrics().set(
                "seocho.retrieval.admission.available_permits",
                self.max_inflight - total, {"source": "agentos"})
        except Exception:  # metrics must never fail admission
            pass


class LaneScheduler:
    """Size-classed, class-aware, work-conserving admission (scheduler v2).

    Three principles, one gate (design note: scheduler-v2-design.md):

    - **Variance isolation.** Service times in graph agentic RAG are
      heavy-tailed (measured 100x: ~80ms vs ~8s on the same store), so
      permits are partitioned into a *light* and a *heavy* lane. A light
      query never queues behind a heavy one — the head-of-line blocking
      that made v1's FIFO admission *worsen* light-class p99 (1,767ms vs
      239ms ungoverned, E1) is removed structurally, not tuned away.
    - **Work conservation.** The high-priority reserve is borrowable: a
      normal call may take a reserved permit *iff no high-class caller is
      waiting*, and releases prefer high waiters. v1's static reserve
      taxed normal throughput even when the high class was idle; v2 only
      charges when protection is actually being used.
    - **Fast structured failure.** On arrival, the expected wait
      (waiters_ahead x lane EWMA service time / lane permits) is compared
      to the caller's deadline; a wait that cannot be met is rejected
      *immediately* instead of timing out later — the tail a queue would
      have added becomes a zero-wait signal an agent loop can act on.

    Classification uses the layer's unique asset: the graph gives cost
    signal per statement. Planner row estimates measured unreliable
    (FinBench), so lanes are assigned from an **observed EWMA per Cypher
    hash**; a statement never seen before goes to the heavy lane —
    "unknown means heavy" keeps the light lane's p99 pure.

    ``light_permits=0`` collapses to a single lane (v1 behaviour);
    ``max_inflight=0`` disables the gate entirely.
    """

    def __init__(self, *, max_inflight: int = 0, light_permits: int = 0,
                 reserved_for_high: int = 0, wait_seconds: float = 5.0,
                 light_threshold_ms: float = 500.0,
                 ewma_alpha: float = 0.3) -> None:
        if max_inflight < 0 or light_permits < 0 or reserved_for_high < 0:
            raise ValueError("scheduler sizes must be non-negative")
        if max_inflight and light_permits >= max_inflight:
            raise ValueError("the light lane must leave heavy capacity")
        if max_inflight and reserved_for_high >= max_inflight:
            raise ValueError("the reserve must leave normal capacity")
        self.max_inflight = max_inflight
        self.wait_seconds = wait_seconds
        self.light_threshold_ms = light_threshold_ms
        self.ewma_alpha = ewma_alpha
        self._permits = ({"light": light_permits,
                          "heavy": max_inflight - light_permits}
                         if light_permits else {"heavy": max_inflight})
        self._reserved = {lane: 0 for lane in self._permits}
        # The reserve protects interactive traffic where it lives; with
        # lanes enabled that is the light lane, otherwise the single lane.
        reserve_home = "light" if light_permits else "heavy"
        self._reserved[reserve_home] = reserved_for_high
        self._in_use = {lane: {"high": 0, "normal": 0} for lane in self._permits}
        self._waiters = {lane: {"high": 0, "normal": 0} for lane in self._permits}
        self._ewma_ms: Dict[str, float] = {}
        # What actually runs in each lane, not a per-statement max: the
        # wait predictor must reflect the lane's real traffic, or a single
        # heavy observation poisons every estimate (found live: fast-fail
        # + a contaminated global estimate starved the polite high class
        # to 0/85 — the probe that caught it is e2_s2_probe.py).
        self._lane_service_ms: Dict[str, float] = {}
        self._cond = threading.Condition()

    # -- classification ----------------------------------------------------

    def classify(self, statement_key: str) -> str:
        if "light" not in self._permits:
            return "heavy"
        observed = self._ewma_ms.get(statement_key)
        if observed is None:
            return "heavy"                      # unknown means heavy
        return "light" if observed <= self.light_threshold_ms else "heavy"

    def observe(self, statement_key: str, service_ms: float,
                lane: Optional[str] = None) -> None:
        with self._cond:
            prior = self._ewma_ms.get(statement_key)
            self._ewma_ms[statement_key] = (
                service_ms if prior is None
                else prior + self.ewma_alpha * (service_ms - prior))
            if lane in self._permits:
                lane_prior = self._lane_service_ms.get(lane)
                self._lane_service_ms[lane] = (
                    service_ms if lane_prior is None
                    else lane_prior + self.ewma_alpha * (service_ms - lane_prior))

    # -- admission ----------------------------------------------------------

    def _admissible(self, lane: str, cls: str) -> bool:
        used = self._in_use[lane]
        free = self._permits[lane] - used["high"] - used["normal"]
        if free <= 0:
            return False
        if cls == "high":
            return True
        headroom_for_high = max(0, self._reserved[lane] - used["high"])
        if self._waiters[lane]["high"] > 0:
            # Protection is in use: keep the reserve headroom for them.
            return free > headroom_for_high
        # Work conservation: nobody to protect, the reserve is borrowable.
        return True

    def _predicted_wait_s(self, lane: str) -> float:
        service_ms = self._lane_service_ms.get(lane, 0.0)
        if lane == "light":
            service_ms = min(service_ms, self.light_threshold_ms)
        waiters = self._waiters[lane]["high"] + self._waiters[lane]["normal"]
        permits = max(1, self._permits[lane])
        return (waiters * service_ms) / (permits * 1000.0)

    def acquire(self, *, lane: str = "heavy", priority: str = "normal",
                deadline_s: Optional[float] = None) -> bool:
        if not self.max_inflight:
            return True
        cls = "high" if priority == "high" else "normal"
        budget = self.wait_seconds if deadline_s is None else deadline_s
        with self._cond:
            if not self._admissible(lane, cls):
                # Fast structured failure: a wait that cannot be met is
                # rejected now, not timed out later.
                if self._predicted_wait_s(lane) > budget:
                    return False
            deadline = time.monotonic() + budget
            self._waiters[lane][cls] += 1
            try:
                while not self._admissible(lane, cls):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(remaining)
                self._in_use[lane][cls] += 1
                return True
            finally:
                self._waiters[lane][cls] -= 1

    def release(self, *, lane: str = "heavy", priority: str = "normal") -> None:
        if not self.max_inflight:
            return
        cls = "high" if priority == "high" else "normal"
        with self._cond:
            self._in_use[lane][cls] -= 1
            self._cond.notify_all()



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
class OSSession:
    """One agent's handle on the operating layer.

    Hand ``sdk_session`` and ``hooks`` to ``Runner.run``; build tools/agents
    through the parent ``SeochoOS`` so every graph access stays governed.
    """

    os: "SeochoOS"
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


class SeochoOS:
    """The operating layer: one instance per (workspace, database) pair.

    All sessions created from one ``SeochoOS`` share its admission permits —
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
        light_permits: int = 0,
        reserved_for_high: int = 0,
        admission_wait_s: float = 5.0,
        light_threshold_ms: float = 500.0,
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
        self._admission = LaneScheduler(
            max_inflight=max_inflight, light_permits=light_permits,
            reserved_for_high=reserved_for_high,
            wait_seconds=admission_wait_s,
            light_threshold_ms=light_threshold_ms)
        self._sessions: Dict[str, OSSession] = {}
        self._lock = threading.Lock()

    # -- memory -----------------------------------------------------------

    def session(self, session_id: Optional[str] = None, *,
                priority: str = "normal",
                user_id: Optional[str] = None) -> OSSession:
        session_id = session_id or uuid.uuid4().hex[:12]
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = OSSession(
                    os=self, session_id=session_id,
                    priority=priority, user_id=user_id)
            return self._sessions[session_id]

    # -- execution / scheduling -------------------------------------------

    def _admit(self, session: OSSession, lane: str = "heavy") -> None:
        if not self._admission.acquire(lane=lane, priority=session.priority):
            from .query.query_proxy import QueryAdmissionRejected

            raise QueryAdmissionRejected(
                f"admission denied for session={session.session_id} "
                f"(priority={session.priority}, lane={lane}): no capacity "
                f"within deadline")

    def _release(self, session: OSSession, lane: str = "heavy") -> None:
        self._admission.release(lane=lane, priority=session.priority)

    # -- governance / the Bolt-aware tool surface --------------------------

    def execute_query(self, session: OSSession, cypher: str,
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
        import hashlib as _hashlib
        import time as _time

        statement_key = _hashlib.blake2b(
            " ".join(cypher.split()).encode(), digest_size=8).hexdigest()
        lane = self._admission.classify(statement_key)
        self._admit(session, lane)
        started = _time.perf_counter()
        try:
            rows = self.graph_store.query(
                cypher, params=params, database=self.database,
                enforce_workspace_filter=True)
        except Exception as exc:
            return _json.dumps({"error": type(exc).__name__,
                                "message": str(exc)[:300]})
        finally:
            self._release(session, lane)
        # Feed the cost signal only on success: a failure's wall time says
        # nothing about the statement's service cost.
        self._admission.observe(
            statement_key, (_time.perf_counter() - started) * 1000.0, lane=lane)
        capped = rows[: self.row_cap]
        return _json.dumps({"rows": capped, "row_count": len(capped),
                            "row_cap": self.row_cap,
                            "truncated": len(rows) > self.row_cap},
                           default=str)

    def make_graph_tool(self, session: OSSession) -> Any:
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

    def _worked_examples(self, policy: Any) -> str:
        """Two worked, contract-conformant examples derived from THIS ontology.

        ADR-0169: describing the contract (the schema block) is not enough —
        a strong model reaches only ~66% conformance, a weak one ~16%; showing
        the exact conformant form takes both to 100% and removes the guardrail
        repair loop. So the governed agent ships examples, not just a schema.
        The form is the one the guardrail actually accepts: inline-map scope
        `{<ws>: $workspace_id}`, `LIMIT $limit`, and workspace_id + limit passed
        in params_json (the layer overwrites workspace_id — the value shown is a
        placeholder the pin makes safe).
        """
        ws = getattr(policy, "workspace_property", "_workspace_id")
        labels = list(getattr(self.ontology, "nodes", {}).keys())
        rels = getattr(self.ontology, "relationships", {}) or {}
        pj = f'{{"workspace_id": "{self.workspace_id}", "limit": 1}}'
        lines = ["\n\nWorked examples — copy this exact form (the tool rejects "
                 "anything off-contract):"]
        if labels:
            lines.append(
                f"- Count a label:\n"
                f"  MATCH (n:{labels[0]} {{{ws}: $workspace_id}}) "
                f"RETURN count(n) AS v LIMIT $limit\n"
                f"  params_json: {pj}")
        for _rt, rd in rels.items():
            src = getattr(rd, "source", None)
            tgt = getattr(rd, "target", None)
            if src and tgt:
                lines.append(
                    f"- Traverse a relationship (distinct):\n"
                    f"  MATCH (a:{src} {{{ws}: $workspace_id}})-[:{_rt}]->"
                    f"(:{tgt}) RETURN count(DISTINCT a) AS v LIMIT $limit\n"
                    f"  params_json: {pj}")
                break
        return "\n".join(lines)

    def build_agent(self, session: OSSession, *,
                    name: str = "seocho_agent",
                    model: Optional[Any] = None,
                    extra_tools: Sequence[Any] = ()) -> Any:
        """An openai-agents Agent whose only graph access is this layer.

        Instructions carry the same schema block the deterministic planner
        uses (one rulebook), the truncation-honesty rule the FinBench
        disclosure study motivated, and — since ADR-0169 — worked examples of
        the exact conformant query form (describing the contract is not enough).
        """
        import json as _json

        from agents import Agent

        from .query.hybrid_planner import policy_from_ontology, schema_for_prompt

        policy = policy_from_ontology(self.ontology)
        schema = schema_for_prompt(self.ontology, policy)
        ws = getattr(policy, "workspace_property", "_workspace_id")
        instructions = (
            "You are an analyst querying a graph database with Cypher.\n\n"
            "Schema (use only these labels, relationship types and parameters):\n"
            + _json.dumps(schema, indent=2, default=str)
            + "\n\nRules:\n"
            f"- Scope every matched node inline: (n:Label {{{ws}: $workspace_id}}) "
            "— a WHERE clause does not satisfy the check.\n"
            "- End the query with `LIMIT $limit`, and pass both `workspace_id` "
            "and `limit` in params_json.\n"
            "- If the tool reports `truncated: true`, say so instead of presenting "
            "a partial result as complete.\n"
            "- If the schema cannot express the question, say what is missing "
            "instead of inventing labels."
            + self._worked_examples(policy))
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


# The layer is SEOCHO, not a side brand (hadry, 2026-08-15; seocho-dxe).
# One release of grace for anything written against the #498 spelling.
AgentOS = SeochoOS
