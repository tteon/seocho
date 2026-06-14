"""DataHub connector (PoC, seocho-qxj Phase A): export a SEOCHO Ontology to a
DataHub Business Glossary.

Decision (user, 2026-06-14): the ambiguity-mapping surface / distribution target
is DataHub, not a bespoke Streamlit app — couple to an existing metadata
ecosystem. SEOCHO stays the authoring/quality engine (scorecard + OntoClean,
which DataHub lacks); DataHub provides the glossary tree, search, and approval
workflow we ride instead of rebuilding.

This module is **pure and offline**: it maps an Ontology to a list of DataHub
Metadata Change Proposals (MCPs) as plain dicts (the same shape the
``datahub`` SDK's ``MetadataChangeProposalWrapper`` serializes to). Emission to a
live GMS is optional and guarded behind an import, so the connector is fully
testable without DataHub installed or a server running. URNs are deterministic
(``<package_id>.<label>``) so re-export is an idempotent UPSERT.

Mapping:
- one ``glossaryNode`` per ontology package (the container);
- one ``glossaryTerm`` per class, with definition, parentNode = the package node,
  and customProperties carrying aliases / same_as / identity_keys / version;
- ``glossaryRelatedTerms.isRelatedTerms`` for each ``broader`` (is-a) edge
  (DataHub's "Is A" relationship);
- relationship types as terms under a ``<package> Relationships`` child node,
  with source/target/cardinality in customProperties.

NOTE: exact aspect field names follow DataHub's documented model; verify against
the target ``datahub`` version when wiring live emit (Phase C).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .ontology import Ontology


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s).strip())


def _node_urn(node_id: str) -> str:
    return f"urn:li:glossaryNode:{_slug(node_id)}"


def _term_urn(term_id: str) -> str:
    return f"urn:li:glossaryTerm:{_slug(term_id)}"


def _mcp(entity_type: str, urn: str, aspect_name: str, aspect: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "entityType": entity_type,
        "entityUrn": urn,
        "changeType": "UPSERT",
        "aspectName": aspect_name,
        "aspect": aspect,
    }


def ontology_to_glossary_mcps(ontology: Ontology) -> List[Dict[str, Any]]:
    """Map an Ontology to DataHub glossary MCPs (pure; deterministic URNs)."""
    pkg = ontology.package_id or ontology.name
    pkg_node_id = pkg
    rel_node_id = f"{pkg}.Relationships"
    mcps: List[Dict[str, Any]] = []

    # package container node
    mcps.append(_mcp("glossaryNode", _node_urn(pkg_node_id), "glossaryNodeInfo", {
        "name": ontology.name,
        "definition": (ontology.description or f"SEOCHO ontology '{ontology.name}'").strip(),
        "id": _slug(pkg_node_id),
    }))

    # classes → terms
    for label, nd in ontology.nodes.items():
        term_id = f"{pkg}.{label}"
        custom: Dict[str, str] = {"seocho_class": label, "ontology_version": str(ontology.version)}
        aliases = [str(a) for a in (getattr(nd, "aliases", []) or [])]
        if aliases:
            custom["aliases"] = ", ".join(aliases)
        if getattr(nd, "same_as", None):
            custom["same_as"] = str(nd.same_as)
        ik = nd.effective_identity_keys
        if ik:
            custom["identity_keys"] = ", ".join(ik)
        mcps.append(_mcp("glossaryTerm", _term_urn(term_id), "glossaryTermInfo", {
            "name": label,
            "definition": (str(getattr(nd, "description", "") or "").strip() or f"{label} (no definition)"),
            "termSource": "INTERNAL",
            "parentNode": _node_urn(pkg_node_id),
            "customProperties": custom,
        }))
        # broader (is-a) → glossaryRelatedTerms.isRelatedTerms
        parents = [p for p in (getattr(nd, "broader", []) or []) if p in ontology.nodes]
        if parents:
            mcps.append(_mcp("glossaryTerm", _term_urn(term_id), "glossaryRelatedTerms", {
                "isRelatedTerms": [_term_urn(f"{pkg}.{p}") for p in parents],
            }))

    # relationships → terms under a Relationships sub-node
    if ontology.relationships:
        mcps.append(_mcp("glossaryNode", _node_urn(rel_node_id), "glossaryNodeInfo", {
            "name": f"{ontology.name} Relationships",
            "definition": "Relationship types declared by this ontology.",
            "id": _slug(rel_node_id),
            "parentNode": _node_urn(pkg_node_id),
        }))
        for rtype, rd in ontology.relationships.items():
            rterm_id = f"{pkg}.rel.{rtype}"
            mcps.append(_mcp("glossaryTerm", _term_urn(rterm_id), "glossaryTermInfo", {
                "name": rtype,
                "definition": (str(getattr(rd, "description", "") or "").strip() or f"{rtype} relationship"),
                "termSource": "INTERNAL",
                "parentNode": _node_urn(rel_node_id),
                "customProperties": {
                    "source": str(getattr(rd, "source", "Any")),
                    "target": str(getattr(rd, "target", "Any")),
                    "cardinality": str(getattr(rd, "cardinality", "MANY_TO_MANY")),
                },
            }))
    return mcps


def export_summary(mcps: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "mcp_count": len(mcps),
        "glossary_nodes": sum(1 for m in mcps if m["entityType"] == "glossaryNode"),
        "glossary_terms": len({m["entityUrn"] for m in mcps if m["entityType"] == "glossaryTerm"}),
        "is_a_edges": sum(len(m["aspect"].get("isRelatedTerms", [])) for m in mcps
                          if m["aspectName"] == "glossaryRelatedTerms"),
    }


def emit_to_datahub(
    mcps: List[Dict[str, Any]],
    *,
    gms_server: Optional[str] = None,
    token: Optional[str] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Emit MCPs to a DataHub GMS if the ``datahub`` SDK and a server are
    available; otherwise return the dry-run payload. Idempotent (UPSERT by URN)."""
    if dry_run or not gms_server:
        return {"emitted": False, "mode": "dry_run", "summary": export_summary(mcps), "mcps": mcps}
    try:
        from datahub.emitter.mce_builder import make_glossary_term_urn  # noqa: F401
        from datahub.emitter.mcp import MetadataChangeProposalWrapper  # noqa: F401
        from datahub.emitter.rest_emitter import DatahubRestEmitter
    except Exception as exc:  # datahub not installed
        return {"emitted": False, "mode": "unavailable", "error": f"datahub SDK not available: {exc}",
                "summary": export_summary(mcps), "mcps": mcps}
    emitter = DatahubRestEmitter(gms_server=gms_server, token=token)
    sent = 0
    for m in mcps:
        emitter.emit_mcp(MetadataChangeProposalWrapper(
            entityUrn=m["entityUrn"], aspectName=m["aspectName"], aspect=m["aspect"],
        ))
        sent += 1
    return {"emitted": True, "mode": "live", "sent": sent, "gms_server": gms_server,
            "summary": export_summary(mcps)}


def glossary_mcps_to_json(mcps: List[Dict[str, Any]]) -> str:
    return json.dumps(mcps, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Phase B/C (ADR-0129): surface the ambiguity-review queue + SEOCHO governance
# (scorecard / numeric validation) in DataHub. Pure dict-MCP construction — no
# live `datahub` calls. Aspect field names follow DataHub's documented model;
# verify against the target datahub version before live emit.
# ---------------------------------------------------------------------------


def ambiguity_clusters_to_glossary_proposals(
    clusters: List[Dict[str, Any]],
    *,
    package_id: str,
    status: str = "PROPOSED",
) -> List[Dict[str, Any]]:
    """Render ambiguity-review clusters as PROPOSED glossary terms under a
    ``<package_id>.Proposed`` node — the review queue, visible in DataHub."""
    proposed_node_id = f"{package_id}.Proposed"
    mcps: List[Dict[str, Any]] = [_mcp("glossaryNode", _node_urn(proposed_node_id), "glossaryNodeInfo", {
        "name": f"{package_id} — Proposed (ambiguity review)",
        "definition": "Out-of-ontology mentions awaiting human mapping (SEOCHO ambiguity review).",
        "id": _slug(proposed_node_id),
        "parentNode": _node_urn(package_id),
    })]
    for c in clusters:
        surface = str(c.get("surface", ""))
        if not surface:
            continue
        term_id = f"{package_id}.proposed.{surface}"
        mcps.append(_mcp("glossaryTerm", _term_urn(term_id), "glossaryTermInfo", {
            "name": surface,
            "definition": ((c.get("examples") or [""])[0] or f"Proposed term '{surface}' (under review)")[:280],
            "termSource": "INTERNAL",
            "parentNode": _node_urn(proposed_node_id),
            "customProperties": {
                "review_status": status,
                "frequency": str(c.get("frequency", 0)),
                "signals": json.dumps(c.get("signals", {}), ensure_ascii=False),
                "candidate_labels": ", ".join(c.get("candidate_labels", []) or []),
            },
        }))
    return mcps


def scorecard_to_structured_properties(
    scorecard: Dict[str, Any],
    *,
    target_urn: str,
) -> List[Dict[str, Any]]:
    """Map an ``OntologyScorecard.to_dict()`` onto DataHub structuredProperties on
    ``target_urn`` (e.g. the package glossaryNode): overall score, grade, blocking,
    and each dimension score under ``seocho.scorecard.*`` keys."""
    props: List[Dict[str, Any]] = [
        {"propertyUrn": "urn:li:structuredProperty:seocho.scorecard.overall_score",
         "values": [scorecard.get("overall_score")]},
        {"propertyUrn": "urn:li:structuredProperty:seocho.scorecard.grade",
         "values": [scorecard.get("grade")]},
        {"propertyUrn": "urn:li:structuredProperty:seocho.scorecard.blocking",
         "values": [bool(scorecard.get("blocking"))]},
    ]
    for dim in scorecard.get("dimensions", []):
        name = dim.get("name")
        if name:
            props.append({
                "propertyUrn": f"urn:li:structuredProperty:seocho.scorecard.{name}",
                "values": [dim.get("score")],
            })
    entity_type = "glossaryNode" if ":glossaryNode:" in target_urn else "dataset"
    return [_mcp(entity_type, target_urn, "structuredProperties", {"properties": props})]


def numeric_validation_to_assertions(
    validation: Dict[str, Any],
    *,
    dataset_urn: str,
    confidence_threshold: float = 1.0,
) -> List[Dict[str, Any]]:
    """Map a ``NumericValidationResult.to_dict()`` onto DataHub assertion MCPs on
    ``dataset_urn``: an assertionInfo (the rule) + an assertionRunEvent (the
    result — SUCCESS iff confidence >= threshold and no warnings)."""
    confidence = float(validation.get("confidence", 1.0) or 0.0)
    warnings = [f for f in validation.get("findings", []) if f.get("severity") == "warn"]
    passed = confidence >= confidence_threshold and not warnings
    assertion_urn = f"urn:li:assertion:seocho.numeric_validation.{_slug(dataset_urn)}"
    return [
        _mcp("assertion", assertion_urn, "assertionInfo", {
            "type": "DATASET",
            "description": "SEOCHO numeric-fact validation (unit/scale/period/reconciliation; ADR-0127).",
            "datasetAssertion": {"dataset": dataset_urn, "scope": "DATASET_ROWS",
                                 "operator": "_NATIVE_", "nativeType": "seocho.numeric_validation"},
        }),
        _mcp("assertion", assertion_urn, "assertionRunEvent", {
            "assertionUrn": assertion_urn,
            "asserteeUrn": dataset_urn,
            "status": "COMPLETE",
            "result": {
                "type": "SUCCESS" if passed else "FAILURE",
                "nativeResults": {
                    "confidence": str(round(confidence, 4)),
                    "warning_count": str(len(warnings)),
                    "findings": json.dumps(validation.get("findings", []), ensure_ascii=False)[:900],
                },
            },
        }),
    ]


def datahub_glossary_to_mapping_spec(
    term_records: List[Dict[str, Any]],
    *,
    only_status: str = "APPROVED",
    ontology_name: str = "",
) -> Dict[str, Any]:
    """Round-trip: turn reviewed DataHub glossary terms back into a SEOCHO
    mapping-spec (consumable by ``ontology_ambiguity.apply_mapping_spec``), closing
    the human-approval loop. ``term_records`` is the normalized form a DataHub
    read yields after human edits: dicts with ``name`` and (from customProperties)
    ``review_status`` / ``action`` / ``target`` / ``parent`` / ``description``.
    Only terms whose status matches ``only_status`` become mappings.

    (A live DataHub GraphQL source adapter that produces these records is a
    follow-up; this function defines the offline contract and is fully tested.)"""
    mappings: List[Dict[str, Any]] = []
    for rec in term_records:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("review_status") or rec.get("status") or "").upper() != only_status.upper():
            continue
        name = str(rec.get("name", "")).strip()
        if not name:
            continue
        action = str(rec.get("action") or "new_class").strip()
        if action not in {"alias", "new_class", "same_as"}:
            continue
        entry: Dict[str, Any] = {"surface": name, "action": action}
        target = str(rec.get("target") or (name if action == "new_class" else "")).strip()
        if target:
            entry["target"] = target
        if action == "new_class":
            parent = str(rec.get("parent", "")).strip()
            if parent:
                entry["parent"] = parent
            desc = str(rec.get("description", "")).strip()
            if desc:
                entry["description"] = desc
        mappings.append(entry)
    return {"ontology": ontology_name, "mappings": mappings}
