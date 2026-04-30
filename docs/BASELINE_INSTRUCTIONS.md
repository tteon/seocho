# SEOCHO Baseline Instructions: Robustness, Performance, Scalability

System-level baseline for SEOCHO agent and SDK behavior. These are the
**defaults** that ship out of the box; every section names the override
hook so users can customize for their own deployment.

This file pairs with:

- `docs/SDK_CONTRACT.md` — what the SDK guarantees today vs. target
- `seocho/tests/test_user_facing_edge_cases.py` — regression anchors
- `CLAUDE.md` §6 / §8 / §9 — runtime, graph, and observability guardrails

> **Status note (2026-05-01):** several middleware hooks below are
> **target architecture**, not current code. Aspirational sections are
> marked 🚧. Today, SEOCHO has a primitive in-process system-prompt
> cache (`seocho/query/strategy.py`) and no formal middleware chain.
> This document is the design contract that future work should land
> against. See open issues in `.beads` (filter `area-sdk`).

## 0. How to read this document

Each baseline rule has three parts:

1. **Default** — what happens out of the box
2. **Why** — the constraint or invariant that motivates it
3. **Override** — the env var, config knob, or hook to customize

If you find yourself fighting the default, override it explicitly rather
than monkeypatching. If the override doesn't exist yet, file a bd issue
under `area-sdk` so it gets a first-class knob.

## 1. Robustness baseline

### 1.1 Silent-fallback discipline

**Default:** when an SDK or runtime path fails, raise an explicit
exception. Never silently substitute a degraded path and return success.

**Why:** users who opt into a specific execution mode (`agent`,
`supervisor`, streaming, Opik tracing) need to know when that mode is
actually running. Silent degradation produces correct-looking output
that masks real failures and erodes the "I asked for X" contract.

**Override:** opt into degraded fallback with an explicit flag.

```python
sess = Session(
    ontology=onto,
    graph_store=store,
    llm=llm,
    agent_config=AgentConfig(
        execution_mode="agent",
        on_failure="raise",      # baseline default
        # on_failure="degrade",  # opt in to today's silent fallback
    ),
)
```

**Today's gap:** `seocho-1zck`, `seocho-8k1h` pin the bug. Fix work flips
the default from degrade to raise.

### 1.2 Idempotency

**Default:** every write operation that can be retried safely is
idempotent. `Session.add()` is keyed by the content hash; re-running
with the same payload does not duplicate nodes/relationships.

**Why:** retry middleware (1.3 below) requires idempotency; without it
retries become a foot-gun.

**Override:** pass `dedup=False` or a custom `idempotency_key=...` to
opt out or scope the idempotency window.

### 1.3 Retry semantics

**Default:** transient upstream failures (HTTP 5xx, timeouts, rate-limit
429) retry with exponential backoff up to 3 attempts. Permanent failures
(4xx other than 429, validation errors, `InvalidDatabaseNameError`) do
not retry — they raise immediately.

**Why:** retrying a 400 wastes time and tokens. Retrying a 429 with
backoff is the correct response.

**Override:**

```python
from seocho.middleware import RetryMiddleware  # 🚧 target API

sess = Session(
    middleware=[
        RetryMiddleware(
            max_attempts=5,
            backoff="exponential",
            transient_status={429, 500, 502, 503, 504},
        ),
    ],
    ...
)
```

### 1.4 Atomic writes for governance state

**Default:** any state transition that affects governance — rule profile
approvals, semantic artifact approvals, ontology promotion — must be
atomic. Use SQLite-backed storage or `tempfile + os.replace()`. Never
read-modify-write JSON in place.

**Why:** governance state is shared between users and processes.
Concurrent calls without atomicity produce lost updates, mid-read
crashes, or corrupted on-disk state. See `seocho-35n4`.

**Override:** none. This is a hard rule.

### 1.5 Failure-mode classification

**Default:** every error that escapes to the user is one of:

| Class | Status code (HTTP) | Retry-safe |
|-------|-------------------|------------|
| `transient_upstream` (LLM down, DB unavailable) | 502 / 503 / 504 | yes |
| `client_input_invalid` (malformed body, bad DB name) | 400 | no |
| `policy_denied` (workspace mismatch, RBAC) | 403 | no |
| `not_found` | 404 | no |
| `conflict` (concurrent write, drift mismatch) | 409 | maybe |
| `unhandled` | 500 | no |

**Why:** a generic 500 forces ops to read logs every time. Classified
errors let clients drive recovery without humans in the loop.

**Override:** none. Add a new class via PR + ADR.

**Today's gap:** `seocho-qrxr` pins the generic-500 behavior in
`platform_ingest_raw`.

## 2. Performance baseline — KV-cache for multi-turn

### 2.1 The shape of the multi-turn problem

A `Session` typically runs multiple `ask()` / `add()` calls in sequence.
Each call ships a prompt that contains:

```
┌─────────────────────────────────────────────────────────┐
│ [STATIC]  System prompt + agent instructions            │  ← stable
│ [STATIC]  Tool definitions (extract_entities, ...)      │  ← stable
│ [STATIC]  Ontology context (compiled labels/rels)       │  ← stable per session
│ [GROW]    Conversation history (prior turns)            │  ← grows by turn
│ [DELTA]   New user message                              │  ← unique
└─────────────────────────────────────────────────────────┘
```

Without caching, every turn re-bills the entire static prefix. For a
session with a 4000-token system + tools + ontology block, that's
~4000 tokens × N turns of waste at full input price.

### 2.2 Default cache strategy

**Default:** SEOCHO inserts cache breakpoints at the boundaries of the
three static blocks above. The cache reuses across `ask()` calls within
the same `Session` and within the provider's TTL.

**Why:** the static prefix is the bulk of the prompt and changes rarely.
Caching it lets the marginal cost of turn N+1 approach `prior_history +
new_message` rather than `entire_prompt`.

**Provider behavior:**

| Provider | Mechanism | TTL | SEOCHO does |
|----------|-----------|-----|-------------|
| Anthropic | explicit `cache_control: {"type": "ephemeral"}` breakpoints | 5 min default; 1 hr extended-cache (beta) | inserts up to 4 breakpoints; opts into extended-cache via env |
| OpenAI | automatic prefix caching above 1024 tokens | provider-managed | structures prompt so prefix is stable; no flag needed |
| Kimi / others | varies | varies | falls back to no-op; user message ships as-is |

**Override:**

```python
import os
os.environ["SEOCHO_PROMPT_CACHE"] = "extended"   # 1-hour Anthropic cache
os.environ["SEOCHO_PROMPT_CACHE"] = "ephemeral"  # 5-min default
os.environ["SEOCHO_PROMPT_CACHE"] = "off"        # disable

# Per-session:
sess = Session(
    cache_policy=CachePolicy(
        breakpoints=("system", "tools", "ontology"),  # default
        ttl="ephemeral",
        max_breakpoints=4,
    ),
    ...
)
```

### 2.3 Cache hit budget

**Default target:** for any session with ≥3 turns, ≥85% of input tokens
should come from cache reads (not cache creation, not raw input). This
is the "stable prefix" budget.

**How to measure:** every Anthropic response carries
`usage.cache_read_input_tokens` and `usage.cache_creation_input_tokens`.
SEOCHO traces (when enabled) record both per turn.

```python
hit_ratio = cache_read_tokens / (
    cache_read_tokens + cache_creation_tokens + input_tokens
)
```

**Override:** lower the target if your sessions are short (<3 turns) or
the static prefix is small. Raise it if you want a regression alert.

```python
sess = Session(
    cache_policy=CachePolicy(min_hit_ratio=0.85, on_miss="warn"),
    ...
)
```

### 2.4 What never goes in cache

**Default:** the following are always *outside* the cached prefix:

- workspace-scoped data (entities, prior tool outputs)
- the user's new message
- any payload containing per-request secrets

**Why:** cache hits are matched by exact-prefix equality. Putting
volatile or sensitive content in the cached region either kills the hit
ratio or cross-contaminates between sessions.

**Override:** none. Workspace boundaries cross all cache decisions.

### 2.5 Multi-turn invariant

**Default:** `Session.ask(q1)` followed by `Session.ask(q2)` reuses the
KV cache from `q1` for `q2`'s prefix. The conversation history is
appended *after* the cached blocks, not interleaved.

**Why:** any reordering of the static blocks breaks the prefix-equality
that prompt cache requires.

**Override:** if you need to change agent instructions mid-session,
construct a fresh `Session`. Don't mutate `agent_config` in place — that
silently invalidates the cache for the rest of the session.

## 3. Scalability baseline

### 3.1 Concurrent-write boundaries

**Default:** any user-facing write goes through one of two patterns:

- **SQLite-backed store** (e.g., `extraction/rule_profile_store.py`) —
  driver-level atomicity
- **JSON tempfile + atomic rename** (`os.replace()`) — POSIX atomicity
  guarantee

**Why:** see §1.4. Concurrent JSON read-modify-write produces lost
updates and corrupted state.

**Override:** none. New stores must follow one of the two patterns.

### 3.2 Workspace_id is the partition key

**Default:** every cache key, every store path, every trace record
includes `workspace_id`. Even though SEOCHO is a single-tenant MVP per
CLAUDE.md §1, the `workspace_id` partition is treated as load-bearing —
removing it requires an ADR.

**Why:** when multi-tenant lands, retrofitting workspace boundaries
into shared caches and stores is far more expensive than designing for
them now.

**Override:** none for new code. Existing code that doesn't propagate
`workspace_id` is a bug; file under `area-runtime` or `area-sdk`.

### 3.3 Cache key shape

**Default:** every cached value's key is structurally:

```
(workspace_id, database, ontology_identity_hash, content_hash)
```

| Field | Why |
|-------|-----|
| `workspace_id` | tenant boundary |
| `database` | prevent cross-DB poisoning (see `seocho-vncn`) |
| `ontology_identity_hash` | invalidate on schema drift |
| `content_hash` | the actual key for the value |

**Why:** missing any of these produces silent staleness or cross-tenant
bleed. The query cache bug `seocho-vncn` is exactly this — the key was
just `content_hash`.

**Override:** none. Caches that omit these fields are bugs.

### 3.4 Per-Session resource caps

**Default:** a single `Session` has soft ceilings:

| Resource | Default cap | Why |
|----------|-------------|-----|
| Conversation history tokens | 100k | keeps cache prefix stable; prevents unbounded context growth |
| Concurrent in-flight `ask()` calls | 1 | sessions are not async-safe by default |
| Open file handles (trace JSONL etc.) | bounded by GC; explicit `close()` recommended | resource leaks under long-running deployments |
| Streaming response time | 120s | prevents zombie streams |

**Override:**

```python
sess = Session(
    limits=SessionLimits(
        max_history_tokens=200_000,
        stream_timeout_s=300,
        allow_concurrent_asks=True,  # caller is responsible for thread safety
    ),
    ...
)
```

## 4. Middleware-aware design 🚧

> **Status:** this section describes the *target* architecture. Today
> SEOCHO has hardcoded paths inside `Session.ask()` / `Session.add()`.
> The middleware chain below is the design contract for landing the
> robustness, performance, and scalability rules above as composable
> components rather than inline code.

### 4.1 The chain

Every `Session.ask()` and `Session.add()` runs through an ordered
middleware chain. Default stack, in order:

```
[ Inbound ]
  ↓ ValidationMiddleware    (workspace_id, database name, payload shape)
  ↓ PolicyMiddleware        (RBAC / require_runtime_permission)
  ↓ CacheMiddleware         (key per §3.3; short-circuits on hit)
  ↓ BudgetMiddleware        (token / latency / cost ceilings)
  ↓ RetryMiddleware         (transient-only per §1.3)
  ↓ ObservabilityMiddleware (trace span open)
  ↓ ----- core: agent run -----
  ↓ ObservabilityMiddleware (trace span close, record cache stats)
  ↓ CacheMiddleware         (write-through on miss)
[ Outbound ]
```

### 4.2 Hook surface

Each middleware implements the same minimal protocol:

```python
class Middleware(Protocol):
    async def before(self, ctx: CallContext) -> Optional[CallResult]:
        """Return CallResult to short-circuit; None to continue."""

    async def after(self, ctx: CallContext, result: CallResult) -> CallResult:
        """Transform or replace the result."""

    async def on_error(self, ctx: CallContext, exc: BaseException) -> Optional[CallResult]:
        """Optional recovery path. Return CallResult to swallow; None to re-raise."""
```

### 4.3 Customization patterns

**Add a custom middleware:**

```python
class MyAuditMiddleware:
    async def before(self, ctx):
        emit_audit_log(ctx.workspace_id, ctx.action)
        return None
    async def after(self, ctx, result):
        return result
    async def on_error(self, ctx, exc):
        emit_audit_log(ctx.workspace_id, "error", str(exc))
        return None  # re-raise

sess = Session(
    middleware=[MyAuditMiddleware(), *Session.default_middleware()],
    ...
)
```

**Replace a default:**

```python
from seocho.middleware import RetryMiddleware
sess = Session(
    middleware=Session.default_middleware().replace(
        RetryMiddleware,
        RetryMiddleware(max_attempts=5),
    ),
    ...
)
```

**Disable a default (with explicit acknowledgement):**

```python
sess = Session(
    middleware=Session.default_middleware().drop(
        BudgetMiddleware,
        ack="i_have_my_own_cost_controls",
    ),
    ...
)
```

The `ack` argument forces users to write down *why* they're dropping a
default, so reviewers see the trade-off.

### 4.4 Why this shape

- **Composability** — each rule from §1–§3 maps to a single middleware
  unit, so users can replace or disable individually rather than
  forking the whole `Session`.
- **Testability** — each middleware unit-tests in isolation with a
  fake `CallContext`.
- **Discoverability** — `Session.default_middleware()` enumerates the
  baseline; users can see what they're getting without reading source.
- **Audit trail** — middleware order is part of the session's trace
  metadata, so debugging "why did this fail differently?" doesn't
  require diff-ing two `Session` constructors.

### 4.5 Tracking and ADR

The middleware chain is large enough that landing it should be an ADR,
not a single PR. Suggested split:

1. ADR: middleware contract + default stack
2. Land `ValidationMiddleware` + `PolicyMiddleware` (already exist
   inline; refactor into the chain)
3. Land `CacheMiddleware` (incorporates §2 + §3.3)
4. Land `RetryMiddleware` + `BudgetMiddleware`
5. Land `ObservabilityMiddleware` (subsumes current `@track` decorator)
6. Migrate `Session.ask()` / `Session.add()` to dispatch through chain

Each step lands behind a feature flag (`SEOCHO_MIDDLEWARE_CHAIN=1`) until
the migration is complete.

## 5. Agent system-prompt discipline (baseline prompts)

The robustness, performance, and scalability rules above are enforced
at the SDK and middleware layer. This section covers the **prompt
layer** — the actual text the LLM sees. These are the defaults shipped
with SEOCHO's indexing, query, and supervisor agents.

> Opinionated by design. Every rule below is a default the user can
> override via `AgentConfig(prompt_overrides=...)`. The point of writing
> them down is that invisible defaults are worse than wrong ones.

### 5.1 Output envelope discipline

**Default:** every tool and agent response is a JSON envelope, never a
free-form string. The envelope shape:

```json
{
  "status": "ok" | "error" | "refused",
  "data": { ... },
  "error_class": "transient_upstream | client_input_invalid | policy_denied | not_found | conflict | unhandled",
  "error_message": "<short, user-safe; no stack traces>",
  "metadata": { "workspace_id": "...", "ontology_identity_hash": "...", "trace_span_id": "..." }
}
```

**Why:** strings force the caller to parse intent (success? partial?
refused? failed?). Envelopes let middleware (§4) and traces (§9) read
status without prompt-sniffing. Maps 1:1 to the failure-mode
classification in §1.5.

**Override:** `AgentConfig(envelope_format="legacy_string")` — opt out
explicitly when integrating with a system that can't parse JSON.

### 5.2 Cache-friendly system prompt structure

**Default:** every agent's system prompt is ordered:

```
1. Agent identity              (e.g., "You are SEOCHO IndexingAgent")
2. Hard rules / non-negotiables (refusal contract, no-fabrication)
3. Tool catalog                (signatures + when to use each)
4. Ontology context            (labels, relationships, constraints)
5. Output envelope spec        (5.1)
6. Workspace + database scope  (workspace_id, target database)
```

**Why:** prefix-cache hits require byte-equal prefix across turns
(§2.1). Sections 1–5 above are stable across a `Session`'s lifetime;
section 6 changes only when the user explicitly switches database.
Putting volatile content (timestamps, UUIDs, per-call deltas) inside
sections 1–5 silently destroys the cache hit ratio.

**Override:** the order is enforced by `SystemPromptBuilder`; replace
the builder per agent if your provider has different requirements.

**Hard rule:** never inline `datetime.now()`, request UUIDs, or
per-call random data into sections 1–5. Append them after section 6 if
needed.

### 5.3 Tool-use parallelism

**Default:** when an agent issues multiple tool calls with no data
dependency between them, it must emit them in a single assistant turn,
not in serial turns.

**Why:** sequential tool turns multiply latency by N and re-bill the
prompt prefix N times. Parallel tool calls land in one round-trip; the
provider returns all results in the next assistant turn.

**Override:** `AgentConfig(parallel_tools=False)` — disable for
providers without parallel tool-call support, or for debugging.

### 5.4 Verify-after-write

**Default:** after any tool that mutates state (`write_to_graph`,
`upsert_entity`, `create_constraint`), the agent's next tool call is a
read-only probe that confirms the write landed. The probe result is
folded into the final answer's `metadata.verification`.

**Why:** tool returns describe what the tool *attempted*, not what
persisted. DozerDB write failures, race conditions, and partial
commits all silently produce a "success" return with no data on disk.
The probe is cheap and turns "I think we wrote it" into "I confirmed
the write".

**Override:** `AgentConfig(verify_writes=False)` — opt out when the
caller has its own verification layer, or for low-stakes ingest.

### 5.5 Conversation-history compaction

**Default:** prior tool outputs are summarized into structured deltas
before being included in the next turn's prompt. Raw tool output
verbatim is replaced with:

```json
{
  "tool": "extract_entities",
  "turn": 3,
  "summary": {"nodes_added": 12, "labels": ["Company", "Person"]},
  "ref": "trace_span_id://abc123"
}
```

**Why:** raw tool outputs are large, repetitive, and rarely needed in
full for the next reasoning step. Compaction keeps the conversation
under `SEOCHO_MAX_HISTORY_TOKENS` (§3.4) without losing the audit
trail (the `ref` points back to the full output in traces).

**Override:** `AgentConfig(history_compaction="off"|"aggressive")` —
`off` ships full outputs (debug only), `aggressive` summarizes earlier
than the default threshold.

### 5.6 No-fabrication rule

**Default:** entity IDs, node labels, relationship types, and property
names must come from one of:

1. The ontology context (section 4 of the system prompt)
2. A previous tool result in this conversation
3. The user's literal input

The agent **must not invent** identifiers. If a needed label or
property does not exist in the ontology, the agent issues a `refused`
envelope with `error_class="ontology_missing_term"`.

**Why:** fabricated IDs collide across documents (`seocho-b79a`),
break entity linking, and corrupt the graph. Refusal is cheaper than
cleanup.

**Override:** none for entity IDs and ontology terms. For free-text
properties (descriptions, summaries), generation is allowed.

### 5.7 Refusal contract

**Default:** the agent refuses (returns `status: "refused"`) in these
specific cases, mapped to specific error classes:

| Condition | `error_class` | When |
|-----------|---------------|------|
| Caller's workspace_id doesn't match resource | `policy_denied` | enforced in middleware (§4); agent never sees out-of-scope data |
| Ontology hash mismatch on apply | `conflict` | until `seocho-cimb` lands, this is advisory; after, it's a hard refusal |
| Query cannot be answered with current ontology | `ontology_missing_term` | per §5.6 |
| Tool budget exhausted (token / latency / count) | `transient_upstream` | enforced by `BudgetMiddleware` |
| Drift between conversation history and current state | `conflict` | when graph state changed mid-session |

**Why:** silent partial answers erode trust faster than refusals.
Users can recover from a refusal (retry with different scope, fix the
ontology, expand the budget); they can't recover from a confidently
wrong answer they didn't know was wrong.

**Override:** refusal text and tone can be customized via
`AgentConfig(refusal_template=...)`, but the *conditions* and
`error_class` mapping are fixed.

### 5.8 Worked example (query agent system prompt skeleton)

```
You are SEOCHO QueryAgent.

[HARD RULES]
- Output every response as a JSON envelope per §5.1.
- Never invent entity IDs, labels, or relationship types (§5.6).
- Refuse cleanly per the refusal contract (§5.7); do not partial-answer.

[TOOLS]
- search_entities(query, label?) — retrieve candidate nodes
- expand_neighbors(entity_id, hops=1) — traverse relationships
- read_graph(cypher) — read-only Cypher (no writes)

[ONTOLOGY]
{ontology_context_block}        ← cache breakpoint (§2.2)

[OUTPUT FORMAT]
{envelope_spec}

[SCOPE]
workspace_id={workspace_id}, database={database}     ← stable per session

[CONVERSATION]
{compacted_history}              ← grows per §5.5
{new_user_message}
```

The `{ontology_context_block}` and `{envelope_spec}` are the cache
breakpoints. `{workspace_id}`/`{database}` change only when the user
switches scope. Compacted history grows linearly but stays bounded by
`SEOCHO_MAX_HISTORY_TOKENS`.

## 6. Customization knobs (env vars)

| Env var | Default | Effect |
|---------|---------|--------|
| `SEOCHO_AGENT_STRICT` | `1` (target) / `0` (today) | Agent-mode failures raise instead of silently falling back |
| `SEOCHO_PROMPT_CACHE` | `ephemeral` | `ephemeral` / `extended` / `off` |
| `SEOCHO_CACHE_TTL_SECONDS` | `300` | Soft TTL for SDK-side caches |
| `SEOCHO_TRACE_BACKEND` | `jsonl` | `jsonl` / `opik` / `console` / `off` |
| `SEOCHO_TRACE_STRICT` | `0` | Trace backend init failure raises (vs. silent no-op; see `seocho-8k1h`) |
| `SEOCHO_MIDDLEWARE_CHAIN` | `0` (today) / `1` (target) | Enable middleware chain dispatch |
| `SEOCHO_MAX_HISTORY_TOKENS` | `100000` | Per-session history cap |
| `SEOCHO_ENVELOPE_FORMAT` | `json` | `json` (§5.1) / `legacy_string` |
| `SEOCHO_PARALLEL_TOOLS` | `1` | Allow parallel tool calls in one turn (§5.3) |
| `SEOCHO_VERIFY_WRITES` | `1` | Probe-after-write contract (§5.4) |
| `SEOCHO_HISTORY_COMPACTION` | `default` | `off` / `default` / `aggressive` (§5.5) |

## 7. Cross-references

- SDK contract surface: `docs/SDK_CONTRACT.md`
- Regression anchors: `seocho/tests/test_user_facing_edge_cases.py`
- Runtime guardrails: `CLAUDE.md` §6, §8, §9
- Observability contract: `CLAUDE.md` §9, `docs/PHILOSOPHY.md`
- Active gaps tracked in `.beads`:
  - `seocho-1zck` (silent fallback) — robustness §1.1
  - `seocho-vncn` (cache cross-DB) — scalability §3.3
  - `seocho-8k1h` (Opik silent init) — robustness §1.1
  - `seocho-cimb` (drift gate advisory) — robustness §1.5
  - `seocho-35n4` (artifact race) — robustness §1.4
  - `seocho-qrxr` (generic 500) — robustness §1.5
