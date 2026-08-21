# Cross-Agent Memory Sharing

How multiple agents — separate processes, different frameworks — read and
write the same SEOCHO graph memory.

**The contract in one sentence: conversations are private, knowledge is
shared.** An entity written by agent A is readable by agent B the moment the
write returns, if both point at the same backend and workspace; neither ever
sees the other's session state.

Every statement on this page is held by an executable test in
the runtime/API compatibility suite. If the tests pass, this page
is true.

## The two namespaces

| Namespace | Scoped by | What lives there |
|---|---|---|
| **Conversation** (private) | `session_id` — each `Session` object owns one | `SessionContext`: indexed sources, query history, the entity/relationship working set, the query cache. One agent's context object is structurally invisible to another. |
| **Entity** (shared) | `workspace_id` — the tenancy boundary, propagated end-to-end | Everything in the graph: nodes, relationships, provenance (`_sources`), ontology context stamps. Any agent on the same backend and workspace reads what any other agent wrote. |

There is no copy step, no message bus, and no cache invalidation between
agents: sharing is a property of pointing at the same graph, and privacy is
a property of session state never being written where another session reads.

## Tenancy boundaries

- The shared boundary is the **workspace** (`workspace_id`), enforced along
  the whole write/compute path (a runtime guardrail, not a convention).
  Different workspaces on the same backend are fully isolated.
- Multi-tenant deployments opt into **fail-closed reads**: with
  `enforce_workspace_filter=True` (applied at the runtime query boundary), a
  query that does not reference `$workspace_id` is refused rather than run —
  a forgotten filter is an error, never a cross-tenant read.

## User identity stays out of the graph

A deliberate divergence from designs that stamp a `user_identifier` property
onto every node: **SEOCHO never persists user-level identity into the data
plane.** The graph is scoped by `workspace_id` alone.

- `Session(user_id=...)` carries the end-user identity on the session
  object, and it is recorded in the trace/log plane (trace-schema v1 JSONL,
  observability) — where experiments and audits read it.
- Per-user *answering* restrictions are an authorization concern, filtered
  at answer time from the ACL plane (the H4 authorized-answering track,
  seocho-vdw.7), not by node properties.

Why: one scope on the data plane keeps every Cypher validation and guardrail
simple to reason about, and keeps end-user identifiers (PII-adjacent) out of
a store whose contents flow into prompts.

## When agents collide

Two agents can write "the same" entity concurrently. The write path resolves
identity through the ontology's **composite identity keys** (declared per
label), and surfaces non-identical concurrent writes as `merge_conflicts`
instead of silently overwriting. Ambiguous or out-of-vocabulary entities are
routed to the **quarantine / ambiguity-review loop** rather than being
force-merged — review decisions feed back into the ontology, which is a
stronger loop than a bare flagged-pair edge.

Threshold-based auto-merge/flag configuration is tracked in seocho-zrt.

## Read-your-writes

Within one backend, a completed graph write is visible to the next read —
the first contract test holds exactly that. For the authoritative-memory
path (PostgreSQL → projection), visibility follows the projection watermark;
exposing that sequence as a consistency token an agent can wait on is
tracked in seocho-zrt, and the cross-system benchmark is seocho-vdw.3 (H2).

## A typical multi-agent shape

| Agent | Owns | Shares |
|---|---|---|
| Ingestion (cron) | its own `Session` / `session_id` | writes entities into the workspace graph |
| Chat (user-facing) | its sessions, keyed per conversation | reads the same workspace graph on every turn |
| Analytics (nightly) | nothing conversational | read-only queries over the shared graph |

None of them import each other; they cooperate solely through the shared
entity namespace, and each keeps its working memory to itself.
