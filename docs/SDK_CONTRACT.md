# SEOCHO SDK Contract

What `pip install seocho` promises, what it explicitly does not, and where
each promise is enforced. This document is the user-facing contract for
fine-grained SDK use — composing `Session`, individual tools, and stores
without going through the HTTP runtime.

This file pairs with `seocho/tests/test_user_facing_edge_cases.py`. Every
⚠️ row in the tables below has a corresponding test class that currently
**characterizes the gap** and a `.beads` issue tracking the fix. When a
fix lands, the matching test must be updated to assert the desired
behavior — that update is the signal to close the bd issue.

## 1. Status legend

| Mark | Meaning |
|------|---------|
| ✅   | Enforced today; test asserts the desired behavior |
| ⚠️   | Gap pinned by a regression anchor; bd issue open |
| 🚧   | Explicit non-goal (not a defect — not promised) |

## 2. Guarantees by surface

### 2.1 Execution mode selection (`Session(agent_config=...)`)

| Guarantee | Status | Test | Issue |
|-----------|:---:|------|-------|
| `execution_mode="pipeline"` is fully deterministic given a fixed LLM | ✅ | `test_session_agent.py` | — |
| `execution_mode="agent"`: tool runtime failures surface as explicit errors | ⚠️ | `TestSilentAgentModeFallback::test_agent_indexing_failure_returns_degraded_dict_without_raising` and `…test_agent_query_failure_returns_pipeline_answer_silently` | [seocho-1zck](../.beads) |
| `ask_stream()` in agent mode yields token deltas (not a single full chunk) | ⚠️ | `TestSilentAgentModeFallback::test_ask_stream_yields_single_full_chunk_when_streaming_fails` | seocho-1zck |
| Mode selection is explicit; no silent default to a different mode | ✅ | covered by `Session._execution_mode` property contract | — |

**What this means today:** if you choose agent mode and the agents runtime
fails (LLM down, missing API key, agents SDK import error), the SDK
silently falls back to the pipeline path and returns a `degraded=True`
dict. Your code must inspect `result["degraded"]`/`result["fallback_from"]`
to detect failure. After seocho-1zck lands, agent-mode failures raise.

### 2.2 Query cache (`SessionContext._query_cache`)

| Guarantee | Status | Test | Issue |
|-----------|:---:|------|-------|
| Cache key includes the target `database` (no cross-DB poisoning) | ⚠️ | `TestQueryCacheCrossDatabase::test_cached_answer_leaks_across_databases` | [seocho-vncn](../.beads) |
| Cache has a documented TTL (or explicit invalidation hook) | ⚠️ | covered alongside seocho-vncn | seocho-vncn |
| `Session.add()` invalidates relevant cache entries | ⚠️ | (target) | seocho-vncn |

**What this means today:** if you call `sess.ask(q, database="a")` then
`sess.ask(q, database="b")`, you receive `a`'s answer for `b`. Stale
results persist across mutations. Workaround until fix: pass distinct
question strings per database, or construct a fresh `Session` per
database.

### 2.3 Observability (`seocho.tracing`)

| Guarantee | Status | Test | Issue |
|-----------|:---:|------|-------|
| Trace backend init failure is observable to the caller | ⚠️ | `TestOpikBackendSilentInit::test_log_span_no_ops_when_opik_client_init_raises` | [seocho-8k1h](../.beads) |
| `JSONLBackend` writes are durable and parseable | ✅ | `test_session_agent.py` tracing coverage | — |
| Trace metadata carries `workspace_id` for runtime-aware records | ✅ | per CLAUDE.md §9 | — |

**What this means today:** if you configure `OpikBackend` with a bad API
key or unreachable workspace, init logs a warning and `log_span` becomes
a no-op — no traces are written, no exception is raised. Your code has
no way to detect this from the SDK surface. After seocho-8k1h lands,
init raises (or a `ready` property is exposed) under strict mode.

### 2.4 Ontology governance

| Guarantee | Status | Test | Issue |
|-----------|:---:|------|-------|
| Ontology identity hash mismatch is *surfaced* on rule profile reads | ✅ | `TestDriftGateAdvisoryOnly::test_get_rule_profile_returns_mismatch_block_without_raising` | — |
| Ontology identity hash mismatch *blocks* application of profiles/artifacts | ⚠️ | same test (pins advisory-only behavior) | [seocho-cimb](../.beads) |
| `Ontology.merge()` preserves identity-hash determinism for `union`/`left_wins`/`right_wins`/`strict` modes | ✅ | `test_ontology.py` | — |

**What this means today:** drift detection is **advisory-only**. Reads
expose `artifact_ontology_mismatch.mismatch=True` but do not block
ingest or rule application. After seocho-cimb lands, apply paths require
either matching hashes or an explicit `force_drift=True` override with
audit trail.

### 2.5 Persistence atomicity (artifact + profile stores)

| Guarantee | Status | Test | Issue |
|-----------|:---:|------|-------|
| Concurrent `approve_semantic_artifact` calls serialize cleanly (no lost updates, no corruption) | ⚠️ | `TestArtifactApprovalRace::test_concurrent_approvals_lose_one_writer_silently` | [seocho-35n4](../.beads) |
| `rule_profile_store` writes are atomic via SQLite | ✅ | `test_rule_constraints.py` | — |

**What this means today:** the artifact store is a JSON-per-file layout
with read-modify-write semantics; concurrent approvals from two clients
can produce (a) a lost-update outcome, (b) a `JSONDecodeError` mid-read
crash, or (c) an on-disk file with corrupted JSON from interleaved
writes. Use the rule profile store pattern (SQLite-backed) for
collision-prone state, or serialize approvals at the application layer
until seocho-35n4 lands.

## 3. Explicit non-goals

These are **not** promised by the SDK and should not be inferred from
its current behavior:

- 🚧 **Automatic recovery from upstream LLM failures** (rate limits, quota,
  timeouts). The SDK propagates these as exceptions; retry/backoff is the
  caller's responsibility.
- 🚧 **Convergence guarantees on agent loops**. The agents SDK governs
  loop termination; SEOCHO does not impose a max-step bound beyond what
  `agent_config` carries.
- 🚧 **Cross-process locking on user-managed stores**. `graph_store`,
  `vector_store`, and any user-supplied persistence are treated as opaque;
  multi-process safety is the user's contract with their store.
- 🚧 **Multi-tenant isolation at the SDK layer**. Per CLAUDE.md §1, this
  is a single-tenant MVP. `workspace_id` is propagated and validated, but
  it is not a security boundary against malicious in-process callers.
- 🚧 **Backwards compatibility on internal modules** (`seocho.agent.*`,
  `seocho.query.*`, anything not re-exported from `seocho/__init__.py`).
  Public surface is what `__init__` exposes.

## 4. Reading the test map

Every ⚠️ entry above is enforced by a test in
`seocho/tests/test_user_facing_edge_cases.py`. **Today, those tests pin
the buggy current behavior** so that drift is impossible — you cannot
silently regress further. When a fix lands:

1. Update the matching test to assert the desired contract (the row's
   guarantee text becomes the new assertion).
2. Update this document: flip the row from ⚠️ to ✅, drop the
   "What this means today" caveat.
3. Mark the bd issue resolved.

The test file is wired into `scripts/ci/run_basic_ci.sh` and runs on
every push and pull request via `.github/workflows/ci-basic.yml`.

## 5. SDK-first usage pattern

Fine-grained SDK control trades framework abstraction for observability
and determinism. The recommended pattern for users who want predictable
behavior **today** (with current ⚠️ rows in mind):

```python
from seocho import Session, AgentConfig

sess = Session(
    ontology=onto,
    graph_store=store,
    llm=llm,
    agent_config=AgentConfig(execution_mode="pipeline"),  # avoids seocho-1zck
    workspace_id="my-ws",
)

# One Session per database avoids seocho-vncn cross-DB cache poison.
result = sess.add(text, database="my-db")

# Inspect degraded markers explicitly until seocho-1zck fix lands.
if result.get("degraded"):
    raise RuntimeError(f"degraded path: {result.get('fallback_reason')}")
```

After the HIGH issues land, this checklist collapses: agent mode raises
on its own, cache is database-scoped, and the user does not need to
inspect dict markers.

## 6. Cross-references

- Test file: `seocho/tests/test_user_facing_edge_cases.py`
- CI wiring: `scripts/ci/run_basic_ci.sh`, `.github/workflows/ci-basic.yml`
- Operating model: `docs/PHILOSOPHY.md`, `docs/ARCHITECTURE.md`
- Related runtime contract: CLAUDE.md §6 (Runtime/API Guardrails)
- Issue tracker: `.beads/` (filter by `area-sdk` for SDK-scoped items)
