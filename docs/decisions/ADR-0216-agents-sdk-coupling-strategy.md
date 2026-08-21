# ADR-0216: Couple SEOCHO to the OpenAI Agents SDK — orchestration plane only

- Status: proposed
- Date: 2026-08-17
- Tickets: seocho-5ny (breakdown epic); supersedes the "agent runtime" line of
  the product consensus in CLAUDE.md if accepted
- Related: ADR-0214 (deterministic path = accuracy), ADR-0215 (hand-off +
  guardrail gap), `integrations/openai_agents.py`, `operating_layer.py`,
  `agent/factory.py`

## Context

The direction (owner: hadry): **strongly couple SEOCHO to the OpenAI Agents SDK**
(`openai-agents-python`). Its featured surface — Agents (instructions/tools/
guardrails/handoffs/built-in loop), Handoffs, Guardrails, Function tools,
Sessions (persistent memory), MCP server tool calling, Tracing, Sandbox agents,
Realtime/Voice, Human-in-the-loop — is close to what SEOCHO needs, and most of it
is model-provider-agnostic, so it runs on SEOCHO's OpenAI-compatible backend
(MARA MiniMax-M2.7 via `llm.to_agents_sdk_model`).

Two results from this session shape *how* to couple:
- **ADR-0214:** the **deterministic** query path (LLM → typed slots → planner
  builds Cypher) is the accuracy winner (1/8 → 5/8 with modeling; 0 silent-wrong).
- **ADR-0215:** the **SDK's LLM-driven loop did not converge** with the hosted
  model (MaxTurnsExceeded) when a tool emitted bad Cypher, *and* SEOCHO already
  wraps its deterministic guardrail into the SDK `tool_input_guardrail` slot
  (`integrations/openai_agents.py`) — but the factory agents don't wire it.

## Compatibility (verified against the SDK docs, 2026-08)

| SDK capability | provider-agnostic? | fits SEOCHO on MARA? |
|---|---|---|
| Agents / Runner, Handoffs, Function tools | yes | ✅ |
| Guardrails (input/output, `tool_input_guardrail`) | yes | ✅ (already adapted) |
| Sessions (SQLite/Redis/Mongo/SQLAlchemy/encrypted) | DB-agnostic | ✅ |
| MCP server tool calling | universal | ✅ |
| Human-in-the-loop | universal | ✅ |
| Tracing | **needs OpenAI key for some destinations** | ⚠️ keep vendor-neutral |
| Sandbox agents | OpenAI-compatible endpoints | ◐ evaluate |
| Realtime / Voice (`gpt-realtime-2.1`) | **OpenAI-only model** | ✗ not on MARA |

## Decision — the load-bearing principle

**Couple at the orchestration plane; keep the data plane deterministic and
SDK-agnostic.** The SDK supplies the *loop, hand-offs, guardrail slots, tool
schema, sessions, MCP, HITL, span emission*. SEOCHO supplies the *deterministic
bodies*: the ontology-grounded Cypher **planner is a function-tool** the agent
calls (not replaced by free-form generation), the **guardrail body** is SEOCHO's
ontology/Cypher/workspace validation (fail-closed), and **memory/scope** stay
graph-backed and workspace-scoped. This preserves ADR-0214's accuracy while
gaining the SDK's ergonomics, and it is why coupling is safe: the LLM-driven parts
are wrapped by deterministic guardrails and deterministic tools.

### Capability → SEOCHO mapping

- **Agents/loop:** adopt as the orchestration layer; the agent's primary tool is
  the **deterministic planner** (ADR-0214), so accuracy does not depend on the
  free-form loop. Bound `max_turns`.
- **Handoffs:** already used; add `input_filter` for context trimming; scope is
  preserved by tool construction (ADR-0215).
- **Guardrails:** wire `make_ontology_guardrail` (+ workspace/Cypher) onto the
  factory agents so bad Cypher is rejected pre-execution into a repair — the fix
  that makes the loop converge (ADR-0215 gap). SDK guardrails run in parallel and
  fail fast; SEOCHO's determinism is stronger than an LLM-judge guardrail for the
  data plane.
- **Function tools:** keep/expand `@function_tool` with Pydantic schemas.
- **Sessions:** use SDK Sessions for *conversation* memory, **composed with**
  SEOCHO's graph-backed memory/entity-cache — not replacing it (different layers).
- **MCP:** expose SEOCHO's graph tools as an MCP server (distribution) and consume
  remote MCP tools alongside function tools.
- **Tracing:** **do not route traces to OpenAI.** SEOCHO tracing is vendor-neutral
  (ADR-0144, Opik/OTLP); disable the SDK's default exporter
  (`set_tracing_disabled(True)`, the 401 seen in ADR-0215) or bridge SDK spans
  into SEOCHO's tracer. This is the one place coupling must stay loose.
- **Sandbox agents:** evaluate for isolated extraction/indexing workspaces (maps
  to SEOCHO workspace isolation).
- **Human-in-the-loop:** map to ontology curation review (`qualification.py`
  CurationPreview) and the propose_ontology draft-review loop (ADR-0214).
- **Realtime/Voice:** defer — OpenAI-only model, not core to graph-RAG.

## Tensions / risks (explicit)

1. **Non-determinism vs accuracy.** The SDK loop is LLM-driven; SEOCHO's accuracy
   comes from the deterministic planner. Mitigation: planner-as-tool + bounded
   loop + fail-fast guardrails. Do not let free-form text2cypher become the
   default path.
2. **Tracing lock-in.** Keep vendor-neutral; never depend on OpenAI's trace
   backend/key.
3. **Data-plane must stay fail-closed** regardless of SDK/LLM: workspace scope,
   Cypher read-safety, ontology enforcement live in tools/guardrails, never in a
   prompt the model may ignore.
4. **SDK churn.** `openai-agents-python` moves fast (0.13.x); pin the version and
   isolate the coupling behind SEOCHO's `agent/factory.py` + `integrations/` so a
   breaking SDK change touches one surface.
5. **Realtime/Sandbox** partially need OpenAI-proper features — treat as optional.

## Phased wiring plan

- **Phase 0 (now, cheap, unblocks ADR-0215):** wire deterministic guardrails onto
  factory agents; disable/bridge SDK tracing; re-run the hand-off isolation
  experiment → expect convergence + no cross-workspace leak.
- **Phase 1:** deterministic planner as a first-class SDK function-tool; expand
  Pydantic tool schemas; bound `max_turns`.
- **Phase 2:** SDK Sessions (conversation) composed with graph memory; expose
  SEOCHO as an MCP server.
- **Phase 3 (optional):** Sandbox agents (isolated extraction), HITL curation,
  Realtime/Voice only if a use case appears.

## Consequences

If accepted, this makes the Agents SDK the canonical orchestration layer (updates
the CLAUDE.md "agent runtime: OpenAI Agents SDK" consensus from aspiration to
committed), with SEOCHO's determinism as the load-bearing body. Follow-ups tracked
under seocho-5ny (Phase 0 = the ADR-0215 guardrail-wiring ticket).
