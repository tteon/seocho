"""GOPTS pattern catalog — externalized Cypher generation patterns (ADR-0097).

The catalog holds one PatternSpec per Cypher generation branch that the
CypherBuilder used to dispatch inline. G3 keeps the dispatch behavior 1:1
with the previous if/elif chain; G2 will later use ``match()`` to enumerate
multiple candidate patterns per intent and let the cost ranker pick one.

Registration is module-level via the ``@register_pattern`` decorator so
adding a new domain pattern is a one-decorator change instead of a
``CypherBuilder.build()`` edit. ADR-0090's ``cypher_template_lookup`` tool
contract is unchanged at the boundary — the catalog is an internal
refactor of how the builder discovers patterns.

template_factory signature: ``(builder, **build_kwargs) -> (cypher, params)``.
Factories receive the full set of ``build()`` kwargs and pull what they
need; unused kwargs are discarded via ``**_``. Factories call the
existing helper methods on the builder instance so behavior stays
bit-identical to the pre-G3 dispatcher.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from .contracts import PatternSpec


_REGISTRY: Dict[str, PatternSpec] = {}
_SHAPE_INDEX: Dict[str, str] = {}


def register_pattern(
    *,
    pattern_id: str,
    intent_id: str,
    cypher_shape: str,
    required_labels: Tuple[str, ...] = (),
    required_relations: Tuple[str, ...] = (),
    schema_preconditions: Tuple[str, ...] = (),
    cost_hints: Optional[Dict[str, Any]] = None,
) -> Callable[[Callable[..., Tuple[str, Dict[str, Any]]]], Callable[..., Tuple[str, Dict[str, Any]]]]:
    """Decorator: register a PatternSpec keyed by ``pattern_id``.

    Same ``pattern_id`` re-registration overwrites — supports test
    isolation and hot reload during development.
    """

    def decorator(fn: Callable[..., Tuple[str, Dict[str, Any]]]) -> Callable[..., Tuple[str, Dict[str, Any]]]:
        spec = PatternSpec(
            pattern_id=pattern_id,
            intent_id=intent_id,
            cypher_shape=cypher_shape,
            required_labels=required_labels,
            required_relations=required_relations,
            schema_preconditions=schema_preconditions,
            cost_hints=dict(cost_hints or {}),
            template_factory=fn,
        )
        _REGISTRY[pattern_id] = spec
        _SHAPE_INDEX[cypher_shape] = pattern_id
        return fn

    return decorator


def get_by_cypher_shape(cypher_shape: str) -> Optional[PatternSpec]:
    """G3-era 1:1 lookup keyed by the builder's dispatch string."""
    pattern_id = _SHAPE_INDEX.get(cypher_shape)
    if pattern_id is None:
        return None
    return _REGISTRY[pattern_id]


def match(intent_id: str) -> List[PatternSpec]:
    """G2-ready entry point. Returns every pattern registered under
    ``intent_id``; in G3 this is mostly singletons because the
    PatternSpec.intent_id field is best-effort against INTENT_CATALOG."""
    return [spec for spec in _REGISTRY.values() if spec.intent_id == intent_id]


def all_patterns() -> List[PatternSpec]:
    """Snapshot of every registered pattern. For introspection / tests."""
    return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# Pattern registrations — one per CypherBuilder dispatch branch.
#
# Each factory is a thin wrapper over the existing CypherBuilder helper.
# Behavior is bit-identical to the pre-G3 inline dispatch in build().
# ---------------------------------------------------------------------------


@register_pattern(
    pattern_id="pattern:entity_lookup_by_name",
    intent_id="entity_summary",
    cypher_shape="entity_lookup",
    required_labels=("Entity",),
    cost_hints={"prefers_indexed": ["name"]},
)
def _factory_entity_lookup(
    builder: Any,
    *,
    anchor_entity: str = "",
    anchor_label: str = "",
    workspace_id: str = "",
    limit: int = 20,
    **_: Any,
) -> Tuple[str, Dict[str, Any]]:
    return builder._entity_lookup(anchor_entity, anchor_label, workspace_id, limit)


@register_pattern(
    pattern_id="pattern:relationship_lookup_hop1",
    intent_id="relationship_lookup",
    cypher_shape="relationship_lookup",
    required_labels=("Entity",),
    required_relations=("RELATES_TO",),
    cost_hints={"hop_count": 1},
)
def _factory_relationship_lookup(
    builder: Any,
    *,
    anchor_entity: str = "",
    anchor_label: str = "",
    target_entity: str = "",
    target_label: str = "",
    relationship_type: str = "",
    workspace_id: str = "",
    limit: int = 20,
    **_: Any,
) -> Tuple[str, Dict[str, Any]]:
    return builder._relationship_lookup(
        anchor_entity,
        anchor_label,
        target_entity,
        target_label,
        relationship_type,
        workspace_id,
        limit,
    )


@register_pattern(
    pattern_id="pattern:finance_metric_value",
    intent_id="entity_summary",
    cypher_shape="financial_metric_lookup",
    required_labels=("Company", "FinancialMetric"),
    required_relations=("REPORTED",),
    cost_hints={"narrows_by_year": True},
)
def _factory_financial_metric_lookup(
    builder: Any,
    *,
    anchor_entity: str = "",
    target_entity: str = "",
    metric_name: str = "",
    metric_aliases: Optional[Any] = None,
    metric_scope_tokens: Optional[Any] = None,
    years: Optional[Any] = None,
    workspace_id: str = "",
    limit: int = 20,
    **_: Any,
) -> Tuple[str, Dict[str, Any]]:
    return builder._financial_metric_lookup(
        anchor_entity=anchor_entity,
        metric_name=metric_name or target_entity,
        metric_aliases=metric_aliases or (),
        metric_scope_tokens=metric_scope_tokens or (),
        years=years or (),
        workspace_id=workspace_id,
        limit=limit,
    )


@register_pattern(
    pattern_id="pattern:finance_metric_delta",
    intent_id="entity_summary",
    cypher_shape="financial_metric_delta",
    required_labels=("Company", "FinancialMetric"),
    required_relations=("REPORTED",),
    cost_hints={"narrows_by_year": True, "scans_multiple_years": True},
)
def _factory_financial_metric_delta(
    builder: Any,
    *,
    anchor_entity: str = "",
    target_entity: str = "",
    metric_name: str = "",
    metric_aliases: Optional[Any] = None,
    metric_scope_tokens: Optional[Any] = None,
    years: Optional[Any] = None,
    workspace_id: str = "",
    limit: int = 20,
    **_: Any,
) -> Tuple[str, Dict[str, Any]]:
    # Shares the same helper as the value lookup; separate PatternSpec
    # so G2's cost ranker can rank delta queries with their own cost hints.
    return builder._financial_metric_lookup(
        anchor_entity=anchor_entity,
        metric_name=metric_name or target_entity,
        metric_aliases=metric_aliases or (),
        metric_scope_tokens=metric_scope_tokens or (),
        years=years or (),
        workspace_id=workspace_id,
        limit=limit,
    )


@register_pattern(
    pattern_id="pattern:neighbors_one_hop",
    intent_id="entity_summary",
    cypher_shape="neighbors",
    required_labels=("Entity",),
    cost_hints={"hop_count": 1, "is_default_fallback": True},
)
def _factory_neighbors(
    builder: Any,
    *,
    anchor_entity: str = "",
    anchor_label: str = "",
    workspace_id: str = "",
    limit: int = 20,
    **_: Any,
) -> Tuple[str, Dict[str, Any]]:
    return builder._neighbors(anchor_entity, anchor_label, workspace_id, limit)


@register_pattern(
    pattern_id="pattern:shortest_path",
    intent_id="relationship_lookup",
    cypher_shape="path",
    required_labels=("Entity",),
    cost_hints={"unbounded_path": True},
)
def _factory_path(
    builder: Any,
    *,
    anchor_entity: str = "",
    target_entity: str = "",
    workspace_id: str = "",
    limit: int = 20,
    **_: Any,
) -> Tuple[str, Dict[str, Any]]:
    return builder._path(anchor_entity, target_entity, workspace_id, limit)


@register_pattern(
    pattern_id="pattern:label_count",
    intent_id="entity_summary",
    cypher_shape="count",
    cost_hints={"full_label_scan": True},
)
def _factory_count(
    builder: Any,
    *,
    anchor_label: str = "",
    workspace_id: str = "",
    **_: Any,
) -> Tuple[str, Dict[str, Any]]:
    return builder._count(anchor_label, workspace_id)


@register_pattern(
    pattern_id="pattern:label_list",
    intent_id="entity_summary",
    cypher_shape="list_all",
    cost_hints={"full_label_scan": True},
)
def _factory_list_all(
    builder: Any,
    *,
    anchor_label: str = "",
    workspace_id: str = "",
    limit: int = 20,
    **_: Any,
) -> Tuple[str, Dict[str, Any]]:
    return builder._list_all(anchor_label, workspace_id, limit)
