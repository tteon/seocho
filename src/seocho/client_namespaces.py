"""Grouped views over the `Seocho` facade.

`Seocho` carries 80 public methods. Grouped by what they actually do, 24 are
ontology governance, 8 write, 8 agents, 8 platform lifecycle, 9 read — and 23
fall into no category at all (`advanced`, `get`, `execute`, `react`, `router`,
`semantic`, `extract`, …). That last bucket is the diagnosis rather than a
classification failure: the names are accretion, not a vocabulary, so a caller
cannot predict the method from the task (`seocho-6yf`).

These namespaces give the largest coherent groups a predictable prefix:

    sc.index.file("a.pdf")          instead of  sc.index_file("a.pdf")
    sc.governance.score()           instead of  sc.coverage_stats()
    sc.platform.graphs()            instead of  sc.graphs()

Additive on purpose. Every existing method still exists, unchanged and
un-warned. A deprecation pass belongs after these names have been reviewed —
emitting 80 warnings that push users onto names still under discussion would be
worse than the flat list.

`query` and `agents` are deliberately not namespaced. Both are already public
method names, so a namespace of the same name would have to shadow a callable
and carry dual call/attribute semantics. That is doable — the package already
does it for `seocho.agents` at module level — but it is a behavioural change to
a public method rather than an addition, and it belongs in its own change with
its own deprecation cycle.

Namespaces resolve lazily and hold only a reference to the owning client, so
constructing `Seocho` costs nothing extra.
"""

from __future__ import annotations

from typing import Any, Dict, List


class _Namespace:
    """A read-only view that forwards to methods on the owning facade.

    Bound methods are fetched from the owner on each access rather than cached,
    so anything that patches or wraps a facade method — `monkeypatch.setattr`,
    a tracing decorator applied at runtime — is still seen through the
    namespace. A cache here would silently serve the pre-patch callable, which
    is the same failure mode that made the ontology package use `sys.modules`
    aliases instead of forwarding shims.
    """

    __slots__ = ("_owner",)

    #: short name on the namespace -> method name on the facade
    _METHODS: Dict[str, str] = {}

    def __init__(self, owner: Any) -> None:
        object.__setattr__(self, "_owner", owner)

    def __getattr__(self, name: str) -> Any:
        target = self._METHODS.get(name)
        if target is None:
            raise AttributeError(
                f"{type(self).__name__!r} has no attribute {name!r}; "
                f"available: {', '.join(sorted(self._METHODS))}"
            )
        return getattr(self._owner, target)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"{type(self).__name__} is a view over the client; "
            f"set {name!r} on the client itself"
        )

    def __dir__(self) -> List[str]:
        return sorted(self._METHODS)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} of {type(self._owner).__name__}>"


class IndexNamespace(_Namespace):
    """Writing into the graph: ingestion, re-indexing, deletion, schema setup."""

    _METHODS = {
        "add": "add",
        "batch": "add_batch",
        "graph": "add_graph",
        "with_details": "add_with_details",
        "file": "index_file",
        "directory": "index_directory",
        "reindex": "reindex",
        "delete": "delete",
        "delete_source": "delete_source",
        "extract": "extract",
        "raw": "raw_ingest",
        "migrate": "migrate",
        "ensure_constraints": "ensure_constraints",
        "ensure_fulltext_indexes": "ensure_fulltext_indexes",
    }


class OntologyNamespace(_Namespace):
    """Ontology definition, artifacts, profiles, signals, and curation.

    The largest group by far — 24 of the facade's 80 methods — and the one
    whose flat names were least predictable: `coverage_stats`,
    `qualify_graph` and `project_canonical_graph` all read as generic graph
    operations while being ontology governance.
    """

    _METHODS = {
        # definition
        "register": "register_ontology",
        "get": "get_ontology",
        "score": "coverage_stats",
        "prompt_context": "prompt_context_from_ontology",
        "qualify_graph": "qualify_graph",
        "project_canonical_graph": "project_canonical_graph",
        # artifacts
        "artifacts": "list_artifacts",
        "artifact": "get_artifact",
        "draft_artifact": "create_artifact_draft",
        "draft_from_ontology": "artifact_draft_from_ontology",
        "approved_from_ontology": "approved_artifacts_from_ontology",
        "approve_artifact": "approve_artifact",
        "promote_artifact": "promote_artifact",
        "deprecate_artifact": "deprecate_artifact",
        "validate_artifact": "validate_artifact",
        "diff_artifacts": "diff_artifacts",
        "apply_artifact": "apply_artifact",
        # profiles
        "upsert_profile": "upsert_ontology_profile",
        "profiles": "list_ontology_profiles",
        "profile": "get_ontology_profile",
        "promote_profile": "promote_ontology_profile",
        "compile_profile": "compile_ontology_profile",
        "select_profile": "select_ontology_profile",
        "evaluate_profile": "evaluate_ontology_profile",
        # signals and curation
        "create_signal": "create_ontology_signal",
        "signals": "list_ontology_signals",
        "curation_cases": "list_curation_cases",
        "preview_curation": "preview_curation_decision",
        "apply_curation": "apply_curation_decision",
    }


class PlatformNamespace(_Namespace):
    """Deployment-shell surface: what exists, whether it is healthy, teardown."""

    _METHODS = {
        "graphs": "graphs",
        "resolve_graphs": "resolve_graphs",
        "databases": "databases",
        "health": "health",
        "semantic_runs": "semantic_runs",
        "semantic_run": "semantic_run",
        "export_bundle": "export_runtime_bundle",
        "close": "close",
    }


class SessionNamespace(_Namespace):
    """Conversation state on the platform session store."""

    _METHODS = {
        "history": "session_history",
        "reset": "reset_session",
        "chat": "platform_chat",
    }


__all__ = [
    "IndexNamespace",
    "OntologyNamespace",
    "PlatformNamespace",
    "SessionNamespace",
]
