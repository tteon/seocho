"""Composite entity identity for cross-document node merging (seocho-uxs).

The "distinguishing point" (구분점) for dimension-bearing entities. The graph
stores merge a node onto an existing one when their identity matches. With a
single name-unique property that collapses homonyms: two documents that each
mention a "Total revenue" ``FinancialMetric`` (one PTC's, one Tesla's) become
ONE node, and the second write silently overwrites the first value.

When a node type declares ``identity_keys`` (e.g. ``["name", "company",
"year"]``), this module rewrites each node's ``id`` to a deterministic
composite of those property values *before* the write, and remaps relationship
endpoints to match. PTC's revenue and Tesla's revenue then key on distinct
ids and stay separate nodes.

Opt-in: a node type without ``identity_keys`` (the default) is untouched, so
existing ontologies behave exactly as before.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["compute_node_identity", "apply_identity_keys"]


def _normalize_segment(value: Any) -> str:
    """One identity-tuple member as a stable, comparable string."""
    return " ".join(str(value if value is not None else "").strip().lower().split())


def compute_node_identity(
    label: str, properties: Dict[str, Any], identity_keys: Sequence[str]
) -> Optional[str]:
    """Deterministic composite id from a node's identity-key values.

    Returns ``None`` when no identity keys are given, or when every identity
    value is empty (manufacturing an id from nothing would merge unrelated
    blank-keyed nodes — worse than leaving the original id alone).
    """
    if not identity_keys:
        return None
    segments = [_normalize_segment(properties.get(k)) for k in identity_keys]
    if not any(segments):
        return None
    return label.lower() + "|" + "|".join(segments)


def apply_identity_keys(
    ontology: Any,
    nodes: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Rewrite node ids to composite identities and remap relationship endpoints.

    Mutates the node/relationship dicts in place (and also returns them).
    Only nodes whose label declares ``identity_keys`` are affected; the rest
    keep their original ids. When two nodes in the same payload resolve to the
    same identity, both point at that id — the store's MERGE then folds them,
    which is the intended same-entity behavior.
    """
    node_defs = getattr(ontology, "nodes", {}) or {}
    id_remap: Dict[str, str] = {}

    for node in nodes:
        if not isinstance(node, dict):
            continue
        label = str(node.get("label", "") or "")
        nd = node_defs.get(label)
        identity_keys = list(getattr(nd, "identity_keys", []) or []) if nd else []
        if not identity_keys:
            continue
        props = node.get("properties") or {}
        new_id = compute_node_identity(label, props, identity_keys)
        if not new_id:
            continue
        old_id = str(node.get("id") or props.get("name") or "")
        if old_id:
            id_remap[old_id] = new_id
        node["id"] = new_id
        props["id"] = new_id
        node["properties"] = props

    if id_remap:
        for rel in relationships:
            if not isinstance(rel, dict):
                continue
            src, tgt = rel.get("source"), rel.get("target")
            if src in id_remap:
                rel["source"] = id_remap[src]
            if tgt in id_remap:
                rel["target"] = id_remap[tgt]

    return nodes, relationships
