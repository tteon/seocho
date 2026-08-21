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
import string
from typing import Any, Dict, Iterable, List, Optional

from .ontology import Ontology

# Safe passthrough for URN ids: alphanumerics plus '.' and '-'. Deliberately
# EXCLUDES '_', which is reserved as the escape sentinel below.
_SLUG_SAFE = frozenset(string.ascii_letters + string.digits + ".-")


def _slug(s: str) -> str:
    """URN-safe, INJECTIVE encoding of an arbitrary identifier.

    Output uses only ``[A-Za-z0-9.-]`` plus ``_`` as an escape sentinel: safe
    characters pass through, and every other character (including a literal
    ``_``) is escaped as ``_`` followed by two hex digits per UTF-8 byte. Because
    ``_`` only ever appears as an escape prefix and no safe character produces
    one, distinct inputs can never collide.

    This fixes the silent glossary-term merge where the old replace-with-``_``
    slug mapped e.g. ``"Total Revenue"`` and ``"Total_Revenue"`` to the same URN
    (idempotent UPSERT keys on the URN, so a collision overwrote another term —
    worst in the review queue the URNs exist to serve). Class labels without
    ``_`` are unchanged; identifiers containing ``_`` or non-safe characters get
    a new, collision-free URN."""
    out = []
    for ch in str(s).strip():
        if ch in _SLUG_SAFE:
            out.append(ch)
        else:
            out.extend(f"_{b:02x}" for b in ch.encode("utf-8"))
    return "".join(out)


def _node_urn(node_id: str) -> str:
    return f"urn:li:glossaryNode:{_slug(node_id)}"


def _term_urn(term_id: str) -> str:
    return f"urn:li:glossaryTerm:{_slug(term_id)}"


def package_term_urn_prefix(package_id: str) -> str:
    """URN prefix shared by every glossary term SEOCHO emits for one package.

    ``_slug`` encodes char-by-char and ``.`` is slug-safe, so the prefix is
    stable regardless of what follows it — the pull side uses this to scope a
    GMS-wide search to one ontology's terms."""
    return f"urn:li:glossaryTerm:{_slug(str(package_id))}."


def _mcp(entity_type: str, urn: str, aspect_name: str, aspect: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "entityType": entity_type,
        "entityUrn": urn,
        "changeType": "UPSERT",
        "aspectName": aspect_name,
        "aspect": aspect,
    }


def ontology_to_glossary_mcps(
    ontology: Ontology,
    *,
    preserve_definitions: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Map an Ontology to DataHub glossary MCPs (pure; deterministic URNs).

    ``preserve_definitions`` is the set of class labels whose glossaryTermInfo
    aspect is now human-owned (a reviewer edited the definition in the DataHub
    UI, pulled back via ``action="annotate"``). For those terms this skips the
    glossaryTermInfo aspect entirely so a re-export does NOT clobber the human's
    text — glossaryTermInfo is atomic, so definition and customProperties cannot
    be updated independently, and definition ownership wins. The taxonomy
    (glossaryRelatedTerms / is-a) stays SEOCHO-owned and is always emitted.
    Pass the labels present in the last approved round-trip; omit for a first
    export."""
    preserved = {str(x) for x in (preserve_definitions or ())}
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
        if label not in preserved:
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
        # broader (is-a) → glossaryRelatedTerms.isRelatedTerms (always SEOCHO-owned)
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
        # NOTE: import exactly what we call. An earlier unused
        # `mce_builder.make_glossary_term_urn` import silently disabled live
        # emit on acryl-datahub 0.15 (the name no longer exists), because this
        # except-block masks ImportError as "SDK not available".
        from datahub.emitter.rest_emitter import DatahubRestEmitter
        from datahub.metadata.schema_classes import (
            ChangeTypeClass,
            GenericAspectClass,
            MetadataChangeProposalClass,
        )
    except Exception as exc:  # datahub not installed
        return {"emitted": False, "mode": "unavailable", "error": f"datahub SDK not available: {exc}",
                "summary": export_summary(mcps), "mcps": mcps}
    emitter = DatahubRestEmitter(gms_server=gms_server, token=token)
    sent = 0
    for m in mcps:
        # Our MCPs are plain dicts; the SDK's typed Wrapper requires generated
        # aspect objects (found live: passing a dict raises get_aspect_name).
        # The generic-aspect proposal is the sanctioned dict path and keeps the
        # boundary version-robust: GMS validates the JSON server-side.
        emitter.emit_mcp(MetadataChangeProposalClass(
            entityType=m["entityType"],
            entityUrn=m["entityUrn"],
            changeType=ChangeTypeClass.UPSERT,
            aspectName=m["aspectName"],
            aspect=GenericAspectClass(
                contentType="application/json",
                # ensure_ascii: Rest.li transports the bytes as a latin-1-ish
                # string; a raw non-ASCII char (e.g. an em-dash in a definition)
                # fails GMS param validation ("not a valid string representation
                # of bytes"; found live). \uXXXX escapes decode fine server-side.
                value=json.dumps(m["aspect"], ensure_ascii=True).encode("utf-8"),
            ),
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
    # PDL PrimitivePropertyValue is a tagged union — a raw scalar fails GMS
    # validation ("union type is not backed by a DataMap"; found live).
    def _num(v: Any) -> Dict[str, Any]:
        return {"double": float(v or 0.0)}

    def _txt(v: Any) -> Dict[str, Any]:
        return {"string": str(v or "")}

    props: List[Dict[str, Any]] = [
        {"propertyUrn": "urn:li:structuredProperty:seocho.scorecard.overall_score",
         "values": [_num(scorecard.get("overall_score"))]},
        {"propertyUrn": "urn:li:structuredProperty:seocho.scorecard.grade",
         "values": [_txt(scorecard.get("grade"))]},
        # DataHub has no boolean dataType — blocking is a STRING property, so its
        # value is stringified to match the definition emitted by
        # scorecard_structured_property_definitions().
        {"propertyUrn": "urn:li:structuredProperty:seocho.scorecard.blocking",
         "values": [_txt("true" if scorecard.get("blocking") else "false")]},
    ]
    for dim in scorecard.get("dimensions", []):
        name = dim.get("name")
        if name:
            props.append({
                "propertyUrn": f"urn:li:structuredProperty:seocho.scorecard.{name}",
                "values": [_num(dim.get("score"))],
            })
    entity_type = "glossaryNode" if ":glossaryNode:" in target_urn else "dataset"
    return [_mcp(entity_type, target_urn, "structuredProperties", {"properties": props})]


# Value types + entity types a scorecard structured property can carry.
_DATATYPE_NUMBER = "urn:li:dataType:datahub.number"
_DATATYPE_STRING = "urn:li:dataType:datahub.string"
_SCORECARD_ENTITY_TYPES = [
    "urn:li:entityType:datahub.glossaryNode",
    "urn:li:entityType:datahub.dataset",
]


def _property_definition_mcp(qualified_name: str, *, value_type: str, display_name: str,
                             description: str) -> Dict[str, Any]:
    urn = f"urn:li:structuredProperty:{qualified_name}"
    return _mcp("structuredProperty", urn, "propertyDefinition", {
        "qualifiedName": qualified_name,
        "displayName": display_name,
        "valueType": value_type,
        "cardinality": "SINGLE",
        "entityTypes": list(_SCORECARD_ENTITY_TYPES),
        "description": description,
    })


def scorecard_structured_property_definitions(
    dimension_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """The ``propertyDefinition`` MCPs that MUST exist on a GMS before
    ``scorecard_to_structured_properties`` values can be emitted — DataHub's
    StructuredPropertiesValidator rejects assignments to undefined properties, so
    live Phase-C emit fails without this bootstrap (seocho-v6w.2). Emit these
    once per GMS (idempotent UPSERT by URN), then emit the values.

    ``dimension_names`` declares the per-dimension score properties (e.g.
    ``taxonomy_health``); omit for just the three fixed properties."""
    mcps = [
        _property_definition_mcp("seocho.scorecard.overall_score", value_type=_DATATYPE_NUMBER,
                                 display_name="SEOCHO overall score",
                                 description="SEOCHO ontology scorecard: weighted overall score [0,1]."),
        _property_definition_mcp("seocho.scorecard.grade", value_type=_DATATYPE_STRING,
                                 display_name="SEOCHO grade",
                                 description="SEOCHO ontology scorecard: letter grade."),
        _property_definition_mcp("seocho.scorecard.blocking", value_type=_DATATYPE_STRING,
                                 display_name="SEOCHO blocking",
                                 description="SEOCHO ontology scorecard: 'true' if a blocking weakness exists."),
    ]
    for name in (dimension_names or []):
        mcps.append(_property_definition_mcp(
            f"seocho.scorecard.{name}", value_type=_DATATYPE_NUMBER,
            display_name=f"SEOCHO {name}",
            description=f"SEOCHO ontology scorecard dimension '{name}' score [0,1]."))
    return mcps


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

    ``action="annotate"`` edits metadata on an EXISTING class — the common case
    when a reviewer fills in or rewrites a term's definition in the DataHub UI;
    it carries the edited ``description`` (and/or an added ``alias``) back onto a
    class that already exists, where ``new_class`` would wrongly create one.

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
        if action not in {"alias", "new_class", "same_as", "annotate"}:
            continue
        entry: Dict[str, Any] = {"surface": name, "action": action}
        target = str(rec.get("target") or (name if action in ("new_class", "annotate") else "")).strip()
        if target:
            entry["target"] = target
        if action == "new_class":
            parent = str(rec.get("parent", "")).strip()
            if parent:
                entry["parent"] = parent
            desc = str(rec.get("description", "")).strip()
            if desc:
                entry["description"] = desc
        elif action == "annotate":
            # description is present-but-possibly-empty: forward the key so an
            # intentional clear is distinguishable from an untouched field, but
            # apply only writes it when non-empty.
            if "description" in rec:
                entry["description"] = str(rec.get("description") or "").strip()
            add_alias = str(rec.get("alias") or "").strip()
            if add_alias:
                entry["alias"] = add_alias
        mappings.append(entry)
    return {"ontology": ontology_name, "mappings": mappings}
