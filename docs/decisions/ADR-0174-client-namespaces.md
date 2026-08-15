# ADR-0174: Grouped namespaces on the client facade

- Status: Accepted
- Date: 2026-08-16
- Ticket: `seocho-6yf`
- Related: `ADR-0173` (ontology subpackage), `docs/SDK_CONTRACT.md`

## Context

`Seocho` carries 80 public methods on one object. Grouped by what they do:

| count | group |
|---|---|
| 24 | ontology governance |
| **23** | **no category at all** |
| 9 | read / query |
| 8 | lifecycle / platform |
| 8 | agents |
| 8 | write / index |

The 23-method bucket is the diagnosis, not a classification failure. It holds
`advanced`, `get`, `execute`, `react`, `router`, `semantic`, `extract`, `chat`,
`delete`, `migrate`, `qualify_graph`, `project_canonical_graph` and eleven more.
Those are accretion, not a vocabulary: a caller cannot predict the method name
from the task, and `coverage_stats` / `qualify_graph` read as generic graph
operations while being ontology governance.

`AsyncSeocho` had 56 methods to `Seocho`'s 80 — 55 shared, and **25 sync methods
with no async counterpart and no declared reason**: `index_file`,
`index_directory`, `reindex`, `plan`, `agent`, `build_agent`, `session`,
`execute_query`, `close` and more. That is a correctness liability, since 55
pairs were kept behaviourally identical by hand.

## Decision

Add four grouped views — `index`, `governance`, `platform`, `sessions` —
covering 54 of the 80 methods, and generate the async surface from the sync one.

## Additive, and not yet deprecating

Every flat method still exists, unchanged and un-warned. A deprecation pass
belongs *after* these names have been reviewed: emitting warnings that push
users onto names still under discussion is worse than the flat list they
already know.

## Three things the implementation was forced into, each an improvement

**`governance`, not `ontology`.** The first attempt used `sc.ontology` and
failed at construction — `self.ontology` is already the registered `Ontology`
object (`client.py:286`). The collision produced a better split than the one
planned: the noun stays the thing, the namespace is what you do to it.

**`query` and `agents` are excluded.** Both are existing public method names, so
a namespace of the same name must shadow a callable and carry dual
call/attribute semantics. The package already does that for `seocho.agents` at
module level, so it is possible — but it is a behavioural change to a public
method rather than an addition, and it needs its own deprecation cycle.

**Namespaces resolve through the owner on every access, never caching bound
methods.** A cache would keep serving the pre-patch callable after
`monkeypatch.setattr` or a runtime-applied decorator — the same failure mode
that made `ADR-0173` use `sys.modules` aliases instead of forwarding shims. The
namespaces bind to whichever facade owns them, so the same four classes yield
coroutines on `AsyncSeocho` with no second mapping table.

## The async surface is generated

`_fill_async_surface()` walks `Seocho` and adds a `to_thread` delegate for every
public method `AsyncSeocho` has not defined itself. Hand-written coroutines keep
winning, so anything needing real async behaviour rather than thread offload is
unaffected.

Deliberately skipped, and asserted as such: constructors (`local`, `remote`,
`from_*`) build a `Seocho`, and an async factory returning a sync client would
misdescribe the object; `close` must not offload teardown to a worker thread
while the loop may still hold references; properties read state, so awaiting one
would make a field look like an operation.

The gap is now pinned by a test comparing the two surfaces, so it cannot reopen.

## Consequences

- 54 of 80 methods have a predictable prefix; 26 remain flat, of which
  `query`/`agents` are deliberate and the rest are the read/agent groups that
  need the callable-namespace decision first.
- `AsyncSeocho` covers the sync surface minus seven documented exclusions.
- Zero public API removed. `pip install seocho` users see additions only.

## Follow-ups

- Deprecation pass on the flat aliases once the names settle.
- The callable-namespace decision for `query` and `agents`.
- `client.py` is still 3,688 lines; `ADR-0173`'s follow-up to split `client_*`
  into mixins is unaffected by this change and still open.
