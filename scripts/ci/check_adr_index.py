#!/usr/bin/env python3
"""ADR index validation (seocho-b01.3).

Closes the "no automated index validation" gap: ADR IDs had silently
duplicated (two different ADRs grabbing one number) and DECISION_LOG.md is
updated by hand. This asserts:

  1. no NEW duplicate ADR IDs (two files claiming the same ADR-NNNN). Five
     historical duplicates predate automated validation and are allow-listed
     below with a standing TODO to renumber — which of each pair keeps the
     canonical number is an author decision, so they are flagged, not guessed.
  2. every ADR-NNNN referenced in docs/decisions/DECISION_LOG.md resolves to an
     actual ADR file (no dangling log entry).

Pure stdlib, deterministic. Exit non-zero naming each offender. Run:
    python3 scripts/ci/check_adr_index.py
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = ROOT / "docs" / "decisions"
DECISION_LOG = ADR_DIR / "DECISION_LOG.md"
ADR_ID_RE = re.compile(r"ADR-(\d{4})")

# Historical duplicates that predate this validator. New duplicates outside
# this set fail CI. Renumbering these (and rewiring DECISION_LOG) is a separate
# author decision — tracked, not auto-resolved, to avoid rewriting the wrong
# ADR's canonical number. See seocho-b01.3.
KNOWN_DUPLICATE_IDS: set = set()  # zero tolerance since the seocho-lpr renumber


def _adr_ids_by_file() -> dict[str, list[str]]:
    by_id: dict[str, list[str]] = defaultdict(list)
    for f in sorted(ADR_DIR.glob("ADR-*.md")):
        m = ADR_ID_RE.match(f.name)
        if m:
            by_id[m.group(1)].append(f.name)
    return by_id


def check_no_new_duplicates(errors: list[str]) -> None:
    for adr_id, files in _adr_ids_by_file().items():
        if len(files) > 1 and adr_id not in KNOWN_DUPLICATE_IDS:
            errors.append(f"duplicate ADR id {adr_id}: {files}")


def check_decision_log_resolves(errors: list[str]) -> None:
    if not DECISION_LOG.exists():
        errors.append("docs/decisions/DECISION_LOG.md missing")
        return
    have = set(_adr_ids_by_file())
    for adr_id in sorted(set(ADR_ID_RE.findall(DECISION_LOG.read_text(encoding="utf-8")))):
        if adr_id not in have:
            errors.append(f"DECISION_LOG references ADR-{adr_id} with no matching file")


def main() -> int:
    errors: list[str] = []
    check_no_new_duplicates(errors)
    check_decision_log_resolves(errors)
    # surface the standing historical-dup TODO without failing (visibility)
    still_dup = [i for i, fs in _adr_ids_by_file().items() if len(fs) > 1]
    if errors:
        print("ADR index check FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    n = len(list(ADR_DIR.glob("ADR-*.md")))
    print(f"ADR index check passed: {n} ADR files, no new duplicate ids, "
          f"DECISION_LOG references all resolve.")
    if still_dup:
        print(f"  NOTE: {len(still_dup)} historical duplicate id(s) allow-listed "
              f"pending author renumber (seocho-b01.3): {sorted(still_dup)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
