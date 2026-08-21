# ADR-0215: Multi-agent hand-off, and context/guardrails via the Agents SDK

- Status: experimental
- Date: 2026-08-17
- Tickets: seocho-5ny (breakdown epic)
- Related: ADR-0214 (deterministic path accuracy), `agent/factory.py`,
  `integrations/openai_agents.py`, `operating_layer.py`

## Context

Two questions, one investigation: (1) how does context hand-off between SEOCHO's
multi-agents behave, and (2) can context management and guardrails ride on the
OpenAI Agents SDK that SEOCHO already uses for hand-off?

SEOCHO's `create_supervisor_agent` (`agent/factory.py`) builds a Supervisor with
Agents-SDK `handoff()`s to an IndexingAgent and a QueryAgent.

## Hand-off mechanism (code-verified)

- **Workspace scope is preserved by construction, not by the LLM.** Each sub-agent
  is built with `create_query_tools(..., workspace_id=...)` / `create_indexing_tools(...)`
  (`agent/factory.py:168,199`); `workspace_id` is closed over inside the tools.
  So a hand-off cannot lose or leak the data-plane scope through the natural-
  language transfer — the target agent's tools physically query only that
  workspace. This is stronger than passing scope as a prompt field.
- **Conversation context crosses the boundary in full.** The `handoff()` calls
  (`factory.py:252,259`) set no `input_filter`, so the Agents SDK passes the
  entire conversation history to the target agent — earlier turns remain visible.
- **What is NOT carried:** any state the Supervisor computes but does not emit
  into the conversation (a pre-fetch, a scratch decision) is invisible to the
  target — by SDK design. Shared non-conversational state would need the SDK
  `context` object (`RunContextWrapper`), which the factory agents do not yet use.

## Live findings (MARA MiniMax-M2.7 + DozerDB, Agents SDK 0.13.6)

Ran the Supervisor bound to workspace A (Cornwall), with a decoy fact planted in
workspace B, asking (a) an A-answerable question and (b) a B-only question.

- **The SDK agentic hand-off loop did NOT converge** with the hosted reasoning
  model: `MaxTurnsExceeded` on both questions.
- Root cause observed: the **agent-mode query tool emitted malformed Cypher**
  (a Neo4j `SyntaxError`), the tool failed, the agent retried, and the loop ran
  out of turns. (This is the agent-mode tool path, distinct from the
  `DeterministicQueryPlanner` used in ADR-0214, which is reliable.)
- Workspace-B data never surfaced (`leaked=False`), but because no valid answer
  was produced, isolation is **not cleanly proven live** — only that no leak
  occurred during a non-converging run. The construction-level guarantee stands;
  the live confirmation is blocked on convergence.

**Contrast:** the single-agent deterministic path (ADR-0214) answers reliably;
the multi-agent SDK hand-off path is currently fragile with this model.

## Context / guardrails via the SDK — already partly built

The user's observation is correct, and SEOCHO has already started here:

- `integrations/openai_agents.py` wraps SEOCHO's **deterministic ontology/Cypher
  guardrail into the SDK's `tool_input_guardrail` slot** (`make_ontology_guardrail`),
  which runs BEFORE a tool executes and rejects bad Cypher with a message the
  model sees — plus a `GuardrailLedger` so rejections are measured, not anecdotal.
  The docstring is explicit: the pairing is additive, the guardrail *body is the
  same deterministic logic*.
- `operating_layer.py` maps the pillars (governance = ontology guardrail +
  enforcement; admission control; context) onto SDK mechanisms, noting "every
  pillar's mechanism already exists."

**The gap that caused the live failure:** the factory-built agents
(`agent/factory.py`) do **not** wire that guardrail onto their tools. So the
multi-agent path is unguarded — exactly why the malformed Cypher reached the DB
and spun the loop. The guardrail that would have rejected it pre-execution
(and fed a repair message) already exists; it is simply not attached.

## Decision

1. **The deterministic single-agent path is the production path** for accuracy
   (ADR-0214). Multi-agent hand-off is not yet reliable with the hosted model.
2. **Guardrails and context ride the SDK, with SEOCHO's deterministic logic as
   the body.** SDK `tool_input_guardrail` is the *slot*; SEOCHO's ontology/Cypher/
   workspace validation is the *content* — stronger than an LLM-judge guardrail for
   the data-plane because it is deterministic and fail-closed. Context: workspace
   via tool construction (done), conversation via SDK default (done), shared
   non-conversational state via `RunContextWrapper` and cross-boundary trimming
   via handoff `input_filter` (not yet used).
3. **To make hand-off reliable:** wire `make_ontology_guardrail` (+ workspace/
   Cypher guardrails) onto the factory agents' tools so bad Cypher is rejected
   pre-execution into a repair, not looped.

## Expected results (the design, once guarded)

- Bad Cypher → guardrail rejects pre-execution → model repairs → loop converges
  (no MaxTurnsExceeded from tool-error spins).
- Workspace isolation: the B-only question returns "cannot find" / A-scoped data,
  never B's decoy — guaranteed by tool construction, now confirmable because the
  run converges.
- Conversation fidelity: a fact stated to the Supervisor in an earlier turn is
  visible to the QueryAgent after hand-off (SDK passes full history).

## Caveats

Live isolation not cleanly proven (non-convergence); one model, one run; the
agent-mode Cypher-generation bug is itself worth a fix independent of hand-off.

## Follow-ups (seocho-5ny)

- Wire the existing deterministic guardrails (`make_ontology_guardrail` + workspace/
  Cypher) onto factory-built agents' tools; re-run the isolation experiment.
- Fix / harden the agent-mode query tool's Cypher generation (the `SyntaxError`).
- Evaluate handoff `input_filter` (context trimming) and `RunContextWrapper`
  (shared state) for the multi-agent path.
- Data: `ADR-0215-handoff.json`.
