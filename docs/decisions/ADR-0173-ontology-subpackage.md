# ADR-0173: `seocho.ontology` becomes a package

- Status: Accepted
- Date: 2026-08-16
- Ticket: `seocho-di8`
- Related: `ADR-0062` (staged package rename — the playbook this follows),
  `seocho-mzg` (import boundary contracts), `seocho-60u` (`extraction/` packaging)

## Context

`src/seocho/` had 82 modules and 30,423 lines sitting flat at its root — **46%
of the SDK with no internal decomposition**. Within that root, the largest
single cluster was `ontology.py` plus fifteen `ontology_*.py` siblings:
**16 modules, 7,872 lines**, bigger than every existing subpackage except
`query/` (13,072).

That cluster was already a package. It was spelled with underscores instead of
a directory, which cost three things a directory gives for free:

- **No declared surface.** No `__init__.py`, so nothing said which of the 123
  public names were the package's API and which were internals.
- **No single answer to "where does ontology governance live".** There were
  sixteen answers, and the prefix was the only thing grouping them.
- **No ownership boundary.** `scripts/ci/check-module-ownership-contract.sh`
  could pin individual files but had no unit to name.

The measured naming problem is stated in `seocho-di8`: the flat root's top
prefixes (`ontology_*` at 7,872 lines, `client_*` at 3,315) are packages that
were never given directories.

## Decision

Move all sixteen modules into `src/seocho/ontology/`, with `ontology.py`
becoming `ontology/core.py` and each `ontology_<name>.py` becoming
`ontology/<name>.py`. Keep every existing import path working.

`ontology_import.py` becomes `ontology/importers.py` — `import` is a keyword and
cannot be a module name reachable by `from . import import`.

## Nothing a caller can see has changed

`from seocho.ontology import Ontology` resolves exactly as before, and all
fifteen `seocho.ontology_<name>` paths still import. This is asserted, not
claimed: `tests/seocho/test_ontology_package_surface.py` checks that all 123
declared exports resolve and that each flat path is the **same object** as its
canonical module.

## Two implementation choices that are not incidental

**The package `__init__` is lazy, and that is a correctness requirement rather
than an optimisation.** `core` and `serialization` / `artifacts` / `versioning`
import each other. Those cycles predate this change and survive only because the
imports sit inside methods rather than at module level. An eager `__init__`
importing all sixteen submodules would convert them into an `ImportError` on
first touch. `src/seocho/__init__.py` already uses the same lazy `__getattr__`
for the same reason, so this is the house pattern, not a new one.

**The compatibility shims rebind `sys.modules` rather than re-exporting names.**
The first version of this change wrote forwarding shims — a module-level
`__getattr__` delegating to the canonical module — and `test_ontology_reasoner`
failed. The reason is worth recording, because the failure mode is quiet: a
forwarding shim passes attribute *reads* through and keeps its own module
object, so `monkeypatch.setattr(seocho.ontology_governance, "reason_consistency",
fake)` lands on the shim while `governance_gate` keeps calling the real
function. The test asserted a value that happened to match the unpatched
default, so one of the two affected tests passed by coincidence.

Rebinding `sys.modules[__name__]` makes `seocho.ontology_governance is
seocho.ontology.governance` true — one module object, not two. This is the same
mechanism `extraction/_runtime_alias.py` uses for the `extraction` → `runtime`
aliases, and the identity is now pinned by a test.

## Consequences

| | before | after |
|---|---|---|
| flat root | 82 modules, 30,423 LOC (46% of SDK) | 81 modules, 22,776 LOC (34%) |
| `ontology/` | — | 8,027 LOC, 2nd-largest subpackage |

Module *count* barely moved, because fifteen four-line shims replaced fifteen
large modules. The line count is the number that matters: a quarter of the flat
root now has a home, a declared surface, and a name that is a directory.

The shims are compatibility surface and should not grow. They are the same
pattern as `src/seocho/graph_store.py`; per `docs/SDK_CONTRACT.md` the public
surface is what `__init__` exposes, and these paths are kept because docs and
tests already use them, not because they are promised.

## Follow-ups

- `client_*` (4 modules, 3,315 LOC) is the same shape and the obvious next
  candidate; `client.py` alone is 3,688 lines with 80 public methods on `Seocho`.
- Deleting the fifteen shims requires a deprecation cycle under
  `docs/PLUGIN_SURFACE.md`; do not remove them in a patch release.
