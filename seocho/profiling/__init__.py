"""SEOCHO internal profiling — operationalizes the CLAUDE.md §21 native-acceleration gate.

Two separable jobs (do not conflate):
  - DISCOVERY  (`discovery.py`)  — attribution-profile the offline data plane to
    rank where Python-CPU time actually goes, i.e. FIND candidates.
  - ADJUDICATION (`gate.py`, phase 2) — run the §21 gate on a specific candidate
    to DECIDE go/no-go.

Both compose the measurement primitives in `harness.py`. Profiling is $0 by
contract (no LLM/embedding API — `harness.no_external_network()` enforces it) and
reproducible (fixed seed, warmup, GC-off, min-reporting, provenance persisted to
the SQLite span store in `store.py`).
"""
from .harness import (
    Sample,
    MarshalSplit,
    ParityResult,
    DeterminismResult,
    timed,
    marshaling_split,
    parity,
    determinism,
    no_external_network,
)

__all__ = [
    "Sample",
    "MarshalSplit",
    "ParityResult",
    "DeterminismResult",
    "timed",
    "marshaling_split",
    "parity",
    "determinism",
    "no_external_network",
]
