"""Backward-compatible alias — canonical location is ``seocho.ontology.snapshot_store``.

Kept because `seocho.ontology_snapshot_store` is an import path users, docs and tests already use.
The module moved when the sixteen flat `ontology*.py` files became a package;
see `seocho/ontology/__init__.py` for why.

This rebinds `sys.modules` rather than re-exporting names, so
``seocho.ontology_snapshot_store is seocho.ontology.snapshot_store`` — one module object, not two.
A forwarding shim would pass reads through and swallow writes, which silently
breaks `monkeypatch.setattr` against this path: the patch would land on the
shim while the code under test kept calling the original. The repository uses
the same rebinding for the `extraction` -> `runtime` aliases.
"""

from __future__ import annotations

import sys

from .ontology import snapshot_store as _canonical

sys.modules[__name__] = _canonical
