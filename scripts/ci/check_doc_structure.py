#!/usr/bin/env python3
"""Doc structure / drift linter (seocho-b01.4).

check-doc-contracts.sh asserts required docs are PRESENT; this asserts they are
STRUCTURALLY sound and not drifting:

  1. relative markdown links resolve (no link to a moved/renamed/deleted file)
  2. CLAUDE.md / AGENTS.md read-order files exist
  3. a few key docs carry their required sections

Pure stdlib, deterministic, no network. Exit non-zero (naming each offender) on
any failure so CI blocks doc drift. Run:
    python3 scripts/ci/check_doc_structure.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Markdown surfaces to lint: root agent/entry docs + everything under docs/.
ROOT_DOCS = ["README.md", "AGENTS.md", ".AGENTS.md", "CLAUDE.md", "docs/README.md"]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# read-order seeds (CLAUDE.md "Read Order"): these must always resolve.
READ_ORDER = [
    "README.md", "AGENTS.md", "docs/REPOSITORY_LAYOUT.md", "docs/WORKFLOW.md",
    "docs/decisions/DECISION_LOG.md",
]
# required sections per key doc (substring match on a heading line).
REQUIRED_SECTIONS = {
    "docs/internal/ARCHITECTURE_HEALTH.md": ["Architecture Health Scorecard", "How to use this"],
    "docs/MODULE_OWNERSHIP_MAP.md": ["Ownership Table"],
}


def _md_files() -> list[Path]:
    files = [ROOT / p for p in ROOT_DOCS if (ROOT / p).exists()]
    files += sorted((ROOT / "docs").rglob("*.md"))
    return files


def _is_external(target: str) -> bool:
    t = target.strip()
    return (t.startswith(("http://", "https://", "mailto:", "#"))
            or t.startswith("<") or not t)


def check_links(errors: list[str]) -> None:
    for f in _md_files():
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in LINK_RE.finditer(text):
            target = m.group(1).split()[0]          # drop optional "title"
            if _is_external(target):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:                        # pure in-page anchor
                continue
            resolved = (f.parent / path_part).resolve()
            if not resolved.exists():
                rel = f.relative_to(ROOT)
                errors.append(f"broken link in {rel}: ({target})")


def check_read_order(errors: list[str]) -> None:
    for rel in READ_ORDER:
        if not (ROOT / rel).exists():
            errors.append(f"read-order file missing: {rel}")


def check_sections(errors: list[str]) -> None:
    for rel, needed in REQUIRED_SECTIONS.items():
        p = ROOT / rel
        if not p.exists():
            errors.append(f"required doc missing: {rel}")
            continue
        text = p.read_text(encoding="utf-8")
        for section in needed:
            if section not in text:
                errors.append(f"{rel}: missing required section '{section}'")


def main() -> int:
    errors: list[str] = []
    check_links(errors)
    check_read_order(errors)
    check_sections(errors)
    if errors:
        print("Doc structure/drift check FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    n = len(_md_files())
    print(f"Doc structure check passed: {n} markdown files, links + read-order + "
          f"sections OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
