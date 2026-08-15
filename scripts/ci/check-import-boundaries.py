#!/usr/bin/env python3
"""Enforce the package boundaries `AGENTS.md` and `CLAUDE.md` declare.

Why this exists, and why it is AST rather than grep.

`check-runtime-shell-contract.sh` and `check-module-ownership-contract.sh` are
roughly ninety `grep -F` assertions pinning exact source lines from past
migrations. They enumerate history; they do not state a rule. The gap is
demonstrable: `check_absent "from middleware import"` is asserted for
`runtime/agent_server.py`, while `from config import`, `from debate import` and
`from rule_api import` in the same file go unchecked — and those are the imports
that reach into `extraction/`. A brand-new `src/seocho/foo.py` doing
`import extraction.debate` passes both scripts today.

Grep also cannot tell code from prose. `src/seocho/agent/runtime_factory.py`
contains `from runtime.ontology_registry import ...` inside a ``Usage::``
docstring — a line that looks like a violation to any text search and is not
one. Parsing is the only way to be right about that, and being wrong in that
direction is how a guard loses its credibility.

The contracts, in dependency order (later may import earlier, never the reverse):

    src/seocho/   the SDK. Imports neither runtime nor extraction.
    extraction/   legacy service. May import seocho. Never runtime.
    runtime/      deployment shell. May import both.

Plus one structural rule with no direction: nothing may import an
`extraction/` module under a bare top-level name. `runtime/__init__.py` puts
`extraction/` on `sys.path`, so `import config` and `import extraction.config`
both resolve to the same file and Python caches them as two distinct modules —
two `DatabaseRegistry` classes, two `db_registry` singletons (seocho-60u).
That rule is a ratchet: the current count is recorded below and may only fall.

Usage:
    python3 scripts/ci/check-import-boundaries.py           # check
    python3 scripts/ci/check-import-boundaries.py --baseline # reprint the ratchet
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, Iterator, List, NamedTuple, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]

# Bare-name imports that resolve into extraction/ because runtime/__init__.py
# injects that directory onto sys.path. Tracked as a ratchet, not a hard zero:
# removing them is seocho-60u, ~173 statements across ~69 files, and it must be
# one reviewable change rather than a drip. This number may fall, never rise.
FLAT_EXTRACTION_IMPORT_BUDGET = 95  # measured 2026-08-16: 66 in extraction/, 29 in runtime/

# Violations that exist today and are owned by a ticket. Each entry is
# (path, imported_module, ticket). An allowlisted violation still has to be a
# real import — if it disappears, the check tells you to delete the entry, so
# the list cannot rot into a permanent exemption.
ALLOWLIST: Dict[Tuple[str, str], str] = {
    # The only production edge pointing the wrong way. Deferred inside a bare
    # try/except, which is why it has survived: the cycle is invisible and its
    # failure is silent (returns "", read downstream as status=unknown).
    ("extraction/rule_api.py", "runtime.ontology_registry"): "seocho-4j2",
}


class Import(NamedTuple):
    path: str
    lineno: int
    module: str
    is_deferred: bool


def _top(module: str) -> str:
    return module.split(".", 1)[0]


def iter_imports(path: Path) -> Iterator[Import]:
    """Yield every import in a file, flagged with whether it is module-level.

    A deferred import (inside a function or method) is still an edge — it is
    how the one real cycle in this repo survives — so it is reported, not
    skipped. The flag lets a contract decide whether to care.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"  !! could not parse {path}: {type(exc).__name__}", file=sys.stderr)
        return

    deferred: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    deferred.add(id(inner))

    rel = str(path.relative_to(ROOT))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield Import(rel, node.lineno, alias.name, id(node) in deferred)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import: by construction it cannot cross a
            # top-level package boundary, so it is never a violation here.
            if node.level:
                continue
            if node.module:
                yield Import(rel, node.lineno, node.module, id(node) in deferred)


def owner_of(rel_path: str) -> str:
    if rel_path.startswith("src/seocho/"):
        return "seocho"
    if rel_path.startswith("runtime/"):
        return "runtime"
    if rel_path.startswith("extraction/"):
        return "extraction"
    return "other"


def production_files() -> List[Path]:
    """Shipped and deployed code only.

    Tests, scripts and examples are excluded deliberately: a test importing
    across a boundary to set up a fixture is not an architecture violation, and
    treating it as one is what makes engineers add blanket ignores.
    """
    out: List[Path] = []
    for base in ("src/seocho", "runtime", "extraction"):
        for path in sorted((ROOT / base).rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            if "/tests/" in rel or rel.endswith("_test.py") or "/test_" in rel:
                continue
            out.append(path)
    return out


def extraction_module_names() -> Set[str]:
    """Top-level names that `extraction/*.py` would occupy on sys.path."""
    return {p.stem for p in (ROOT / "extraction").glob("*.py") if p.stem != "__init__"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true",
                        help="print the measured flat-import count and exit")
    args = parser.parse_args()

    flat_names = extraction_module_names()
    files = production_files()

    violations: List[str] = []
    flat_hits: List[Import] = []
    stale_allowlist = set(ALLOWLIST)

    for path in files:
        owner = owner_of(str(path.relative_to(ROOT)))
        for imp in iter_imports(path):
            top = _top(imp.module)
            key = (imp.path, imp.module)

            # Contract 4: bare-name imports resolving into extraction/.
            # Only meaningful from inside the repo's own packages, and a file
            # in extraction/ importing its own sibling is the same defect.
            if top in flat_names and owner in {"runtime", "extraction", "seocho"}:
                flat_hits.append(imp)
                continue

            broken = (
                (owner == "seocho" and top in {"runtime", "extraction"})
                or (owner == "extraction" and top == "runtime")
            )
            if not broken:
                continue
            if key in ALLOWLIST:
                stale_allowlist.discard(key)
                continue
            kind = "deferred" if imp.is_deferred else "module-level"
            violations.append(
                f"  {imp.path}:{imp.lineno}  {owner} -> {top}  ({kind}: {imp.module})"
            )

    if args.baseline:
        print(f"flat extraction imports in production code: {len(flat_hits)}")
        by_owner: Dict[str, int] = {}
        for hit in flat_hits:
            by_owner[owner_of(hit.path)] = by_owner.get(owner_of(hit.path), 0) + 1
        for owner in sorted(by_owner):
            print(f"  {owner}: {by_owner[owner]}")
        return 0

    failed = False

    if violations:
        failed = True
        print("Import boundary violations (see AGENTS.md dependency order):")
        for line in sorted(violations):
            print(line)
        print("\n  src/seocho must not import runtime or extraction;")
        print("  extraction must not import runtime.")

    if len(flat_hits) > FLAT_EXTRACTION_IMPORT_BUDGET:
        failed = True
        print(
            f"\nBare-name imports resolving into extraction/: {len(flat_hits)}, "
            f"budget {FLAT_EXTRACTION_IMPORT_BUDGET}."
        )
        print("  Each one loads the module a second time under a different name;")
        print("  see seocho-60u. The budget may fall, never rise.")
        for hit in sorted(flat_hits)[:15]:
            print(f"    {hit.path}:{hit.lineno}  import {hit.module}")
        if len(flat_hits) > 15:
            print(f"    ... and {len(flat_hits) - 15} more")
    elif len(flat_hits) < FLAT_EXTRACTION_IMPORT_BUDGET:
        failed = True
        print(
            f"\nBare-name extraction imports fell to {len(flat_hits)} "
            f"(budget {FLAT_EXTRACTION_IMPORT_BUDGET}). Lower the budget in "
            f"{Path(__file__).name} so the gain is locked in."
        )

    if stale_allowlist:
        failed = True
        print("\nAllowlist entries that no longer match a real import:")
        for path, module in sorted(stale_allowlist):
            print(f"  {path} -> {module}  ({ALLOWLIST[(path, module)]})")
        print("  Delete them; an exemption for a fixed problem is a lie.")

    if not failed:
        print(
            f"Import boundary contracts passed "
            f"({len(files)} production modules, "
            f"{len(flat_hits)} known flat extraction imports)."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
