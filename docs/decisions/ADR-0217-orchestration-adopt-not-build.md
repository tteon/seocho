# ADR-0217: Orchestration — adopt, don't build; control the loop, don't trust it

- Status: accepted
- Date: 2026-08-17
- Tickets: seocho-5ny; relates to seocho-ihg (MCP-first ecosystem decision)
- Related: ADR-0214 (deterministic path = accuracy), ADR-0215 (loop
  non-convergence), ADR-0216 (couple at orchestration plane), #606 (Phase 0
  guardrail wiring), #607 (Phase 1 controlled query agent)

## Context

Question raised (owner: hadry): for multi-agent orchestration, do we adopt a
framework (OpenAI Agents SDK, LangGraph, Swarms) or build our own — and is a
state-machine model the right shape? SEOCHO is an "OS" for ontology-aligned graph
memory, which sharpens the question: is building an orchestrator worth it?

## Decision 1 — adopt, do not build

SEOCHO's moat is the **data plane**: typed graph memory, ontology-grounded
deterministic query, workspace scope, ontology guardrails, provenance,
observability. Orchestration (the agent loop, hand-offs, planning) is **not** the
moat — it is the scheduler/userland that sits on top of the OS. Precisely because
SEOCHO is the OS, orchestration is what we **consume**, not build: an OS provides
syscalls (our tools / guardrails / memory), it does not ship the only shell.
Building our own orchestrator is pure maintenance cost with no differentiation.

## Decision 2 — the failure was loop control, not the framework (spike-verified)

ADR-0215 found the SDK's autonomous multi-tool query loop did not converge with
the hosted reasoning model (`MaxTurnsExceeded`). A spike replaced the free-form
tool set with a single deterministic tool (`answer_from_graph`, the ADR-0214
path) under a bounded hand-off:

| | autonomous loop | controlled flow |
|---|---|---|
| convergence | ❌ MaxTurnsExceeded (both Qs) | ✅ both converged (last_agent=QueryAgent) |
| workspace isolation | n/a | held (B decoy never surfaced) |

So the fix is **control the loop, don't trust it** — shipped as Phase 1 (#607:
`create_controlled_query_agent`, one deterministic tool). This is orchestrator-
agnostic: it works under any framework because the determinism lives in SEOCHO's
tool, not the loop.

## Decision 3 — Agents SDK now; state-machine achievable without LangGraph; LangGraph stays gated

The OpenAI Agents SDK **can** be driven deterministically — the state-machine
attraction does not require LangGraph today:
- `handoff(input_filter=...)` — deterministic control of what context crosses.
- `max_turns` + restricted tools (planner-as-tool → effectively single-step).
- `Agent.as_tool` — sub-agent as a tool, control returns (more deterministic than
  a handoff transfer).
- Most deterministic: skip the autonomous `Runner` loop and orchestrate steps in
  plain Python (the SDK is Python-first) — a hand-rolled state machine over
  Agents/tools as callable units.

What the Agents SDK does **not** give first-class, and where LangGraph is
purpose-built: a persistent typed `StateGraph`, declarative conditional edges,
and checkpoint / durable-resume / time-travel / HITL-pause. So the decision
criterion is:

- **Bounded, mostly-linear controlled flows (today):** Agents SDK primitives +
  Python control. Chosen.
- **A durable, branching, resumable state machine as the programming model:**
  LangGraph is the right tool — and it composes with SEOCHO (LangGraph nodes can
  call SEOCHO tools and even wrap Agents-SDK agents; data plane unchanged).

This aligns with the recorded ecosystem decision (**seocho-ihg: MCP-first;
LangGraph behind a triple gate — MCP complete + BMT + real demand**). LangGraph is
therefore **deferred, not rejected**: reconsidered only when a first-class durable
state machine becomes a hard requirement and the gate is met. The state-machine
desire is met now via controlled SDK flow.

## Decision 4 — keep orchestration a thin, swappable adapter

The moat must never depend on the orchestrator. Orchestration stays behind the
`agent/factory.py` + `integrations/` seam so that swapping the loop framework
(SDK → LangGraph, or adding an MCP-driven client) is a contained change and the
data plane (memory, guardrails, deterministic query) is untouched.

## Consequences

- Confirmed: Agents SDK as the orchestration layer (ADR-0216), driven by
  controlled flow (Phase 1, #607); deterministic guardrails wired (Phase 0, #606).
- LangGraph: deferred behind seocho-ihg's gate; revisit if a durable state machine
  is required. Do not adopt speculatively.
- Own orchestrator: not built.
- Follow-ups (seocho-5ny): route the Supervisor to the controlled query agent by
  default; evaluate `Agent.as_tool` vs handoff for the investigation shape;
  `handoff(input_filter=...)` for context trimming; MCP server exposure (MCP-first).
