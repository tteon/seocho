# ADR-0157: AgentOS — the operating layer behind two interfaces

Date: 2026-08-15 · Status: accepted

## Context

The thesis this repository has been testing states that agentic systems
need a common operating layer for memory, execution, scheduling,
governance, and resource management. Every pillar's *mechanism* already
existed in SEOCHO — the workspace guardrail and Cypher validation at the
store layer, the ontology guardrail, admission control, token budgets, the
two-namespace memory contract (#494) — but an agent runtime had no single
object to plug into, and the adapter tool executed model-supplied
parameters directly, including the tenancy value.

## Decision

1. **One facade, two interfaces.** `seocho.agentos.AgentOS` binds the five
   pillars to the interfaces agents actually use: *Bolt-aware* — every tool
   it hands out reads through the governed store path (workspace pinning,
   ontology validation, `enforce_workspace_filter=True` fail-closed reads);
   *OpenAI-compatible* — memory as the Agents SDK `Session` protocol,
   resource control as `RunHooks`, governance as `tool_input_guardrail`
   (verified injection points on openai-agents 0.10.3:
   `Runner.run(session=..., hooks=...)`).
2. **Tenancy is pinned, never trusted.** The layer overwrites the model's
   `workspace_id`/`ws` *parameters* with its own before execution — the
   FinBench harness's "parameters the model must not be trusted with" rule
   becomes a product property. A prompt-injected workspace *parameter value*
   cannot cross the boundary (held by test).

   **Scope correction (security review 2026-08-15):** the pin covers the
   parameter value, not the query *semantics*. A crafted Cypher body can widen
   scope regardless of the pinned parameter (`... WHERE _workspace_id =
   $workspace_id OR true`), smuggle the required token in a comment, or issue a
   write/procedure on the nominal read path. The governed read path now runs
   `enforce_read_workspace_scope` (comment-strip → token-presence → reject
   widening tautologies / writes / procedures → **binding verification**
   (seocho-5zz): every RETURNed node variable must carry `_workspace_id =
   $workspace_id` (WHERE or inline), closing the "token present but constrains
   the wrong node" class (`MATCH (n),(m) WHERE m._workspace_id=$workspace_id
   RETURN n`). The binding pass is conservative — it declines to analyze
   WITH/UNION/UNWIND rather than risk a false rejection, so it only adds
   rejections it can prove) after opening the driver session in READ mode, with
   adversarial tests (`test_workspace_filter_enforcement.py`). That is still
   defense-in-depth, **not a full proof of workspace binding** — the binding
   pass is regex-structural and declines complex queries. The sound endgame
   remains parse/AST-level verification over every construct, or DB-side
   per-workspace databases/credentials (separate address spaces), tracked as a
   follow-up (seocho-5zz). The paper must claim the tested property, not the
   aspirational one.
3. **One gate, inside the tool.** Admission lives in `execute_query`, not
   in hooks, so the bound holds for hook-less callers and permits are never
   double-counted. All sessions of one AgentOS share the pool — that shared
   pool is the process-local scheduling substrate; per-session `priority`
   is recorded for the fairness work (S1/vdw.11).
4. **Budget exhaustion is structured, truncation is disclosed.** Token
   budgets raise `BudgetExceededError` at turn boundaries (ADR-0153
   semantics); row caps always ship `truncated: true/false` (the FinBench
   disclosure study's lesson, #478).
5. **user_id stays out of the graph** — carried on the session for the
   log/trace plane only, per the #494 contract decision.

## Validation

Scalability is the acceptance axis. `scripts/agentos/scale_probe.py` drives
the governed path across SF × concurrency on live FinBench graphs
(graphstack/dozerdb 5.26.3.0, measured 2026-08-15):

| database | sessions | served | p50 | p95 | store max-concurrency (cap 4) |
|---|---|---|---|---|---|
| finbenchl1 | 1 / 4 / 16 | 8 / 32 / 128 | ~9–10ms | 9–174ms | 1 / 4 / 4 |
| finbenchl10 | 1 / 4 / 16 | 8 / 32 / 128 | ~75–79ms | 78–1349ms | 1 / 4 / 4 |

Zero bound violations, zero rejections, zero errors across 336 calls; the
p95 tail under 16-way contention (1.35s on SF10) is the queueing signal the
scheduling pillar will optimize. Unit contracts (7 tests, CI-gated): SDK
session protocol round-trip, cross-session privacy, workspace pinning,
truncation disclosure, admission bounding under threads, budget stop,
agent/guardrail assembly.

> **Amended 2026-08-15 (seocho-dxe, final).** The layer is SEOCHO itself —
> no side class, no second session concept: ``Seocho.session()`` (the same
> Session that ``add()``/``ask()`` use) now carries ``sdk_session`` /
> ``hooks`` / ``priority``; ``build_agent()`` / ``execute_query()`` accept
> it directly, and operating controls are ``Seocho(...)`` constructor
> kwargs defaulting to off (explicit opt-in). ``SeochoOS`` / ``AgentOS`` /
> ``seocho.agentos`` remain importable for one release.

## Consequences

- The OS surface is additive: `Session`, the runtime API, and the adapter
  keep working; AgentOS composes existing parts and adds no second
  rulebook.
- Durable conversation backends (the PG authoritative-memory path) plug in
  behind the same `Session` protocol later without touching callers.
- Fairness (S1), model routing exposure, and the streaming path are the
  epic's remaining children (seocho-xdp).
