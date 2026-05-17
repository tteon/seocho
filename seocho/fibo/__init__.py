"""FIBO-aware routing primitives for SEOCHO.

This package provides the deterministic selection layer that chooses which
FIBO modules govern a given indexing or query request. The compiled
``label_index`` data is produced offline (CLAUDE.md §6.3) and consumed at
request time without invoking owlready2.

Slice 1 (issue ``seocho-1dm8``) ships the catalog, a lexical selector,
and the ``run_with_fibo`` runtime entry point that produces a locked
:class:`FIBORunDescriptor` for trace and KV-cache use.
"""

from __future__ import annotations

from .catalog import FIBOCatalog, FIBOModule
from .runtime import (
    AUDIT_REFUSE_THRESHOLD,
    FIBORunDescriptor,
    FIBOSelectionRefused,
    RunMode,
    run_with_fibo,
)
from .selector import (
    FIBOSelector,
    LexicalSelector,
    SelectionPolicy,
    SelectionResult,
    SelectionStatus,
)

__all__ = [
    "AUDIT_REFUSE_THRESHOLD",
    "FIBOCatalog",
    "FIBOModule",
    "FIBORunDescriptor",
    "FIBOSelector",
    "FIBOSelectionRefused",
    "LexicalSelector",
    "RunMode",
    "SelectionPolicy",
    "SelectionResult",
    "SelectionStatus",
    "run_with_fibo",
]
