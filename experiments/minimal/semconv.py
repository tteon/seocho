"""Span attribute names for this experiment.

OpenTelemetry defines conventions for GenAI calls (`gen_ai.*`) and for servers
and databases, and those are used unchanged. It defines nothing for the concepts
this experiment actually measures: a view, a comparable key, a conflict kind, a
refusal to serve. Without a fixed vocabulary those attributes get invented at
each call site, and a trace stops being queryable across runs.

So the vocabulary is declared here, once, and every stage imports it. Adding a
key means adding it to this file, which makes the set of things a run can report
a reviewable artifact rather than an accident of the code.

Namespaces
    fed.*        federation structure: views, cases, keys
    fed.verify.* cross-view comparison outcomes
    fed.serve.*  what the boundary allowed through
    fed.onto.*   ontology identity and typing
    gen_ai.*     OpenTelemetry's own, used as specified
"""
from __future__ import annotations

from typing import Any, Final, Mapping

# --- federation structure -------------------------------------------------
VIEW_NAMES: Final = "fed.views"                    # list[str], the graphs consulted
VIEW_COUNT: Final = "fed.view_count"
CASE_ID: Final = "fed.case_id"                     # the source document
FACTS_PER_VIEW: Final = "fed.facts_per_view"       # json object view -> count
ENTITIES_PER_VIEW: Final = "fed.entities_per_view"

# --- comparison -----------------------------------------------------------
KEY_RULE: Final = "fed.verify.key_rule"            # how two views are matched
VALUE_RULE: Final = "fed.verify.value_rule"        # when two values are one claim
DISTINCT_KEYS: Final = "fed.verify.distinct_keys"
COMPARABLE_KEYS: Final = "fed.verify.comparable_keys"
COMPARABLE_RATE: Final = "fed.verify.comparable_key_rate"   # the structural ceiling
PAIRS: Final = "fed.verify.pairs"
AGREE: Final = "fed.verify.agree"
DISAGREE: Final = "fed.verify.disagree"
DISAGREE_RATE: Final = "fed.verify.disagreement_rate"
CONFLICT_KINDS: Final = "fed.verify.kinds"         # json object kind -> count

# Conflict kinds are a closed set; a new one must be added here so that
# dashboards and result tables cannot silently gain a category.
KIND_SCALE_1E3: Final = "scale_1e3"
KIND_SCALE_1E6: Final = "scale_1e6"
KIND_SCALE_1E9: Final = "scale_1e9"
KIND_SIGN_FLIP: Final = "sign_flip"
KIND_ZERO: Final = "zero_versus_nonzero"
KIND_ROUNDING: Final = "rounding_within_5pct"
KIND_DIFFERENT: Final = "different_value"
KIND_UNPARSEABLE: Final = "unparseable_on_one_side"
CONFLICT_KIND_SET: Final = frozenset({
    KIND_SCALE_1E3, KIND_SCALE_1E6, KIND_SCALE_1E9, KIND_SIGN_FLIP,
    KIND_ZERO, KIND_ROUNDING, KIND_DIFFERENT, KIND_UNPARSEABLE,
})

# --- serving boundary -----------------------------------------------------
SERVE_STATUS: Final = "fed.serve.status"           # consistent | conflict
SERVE_CONFLICTING: Final = "fed.serve.conflicting_slots"
SERVE_WITHHELD: Final = "fed.serve.withheld_protected_slots"
SERVE_ALLOWED: Final = "fed.serve.servable"        # bool, did anything go through

# --- ontology -------------------------------------------------------------
ONTO_NAME: Final = "fed.onto.name"
ONTO_MODULES: Final = "fed.onto.modules"
ONTO_CLASS_COUNT: Final = "fed.onto.class_count"
ONTO_TYPING: Final = "fed.onto.typing"             # declared | generic | undeclared counts

# --- run identity ---------------------------------------------------------
FINGERPRINT: Final = "fed.run.fingerprint"         # hash of the decisive config

ALL: Final = frozenset({
    VIEW_NAMES, VIEW_COUNT, CASE_ID, FACTS_PER_VIEW, ENTITIES_PER_VIEW,
    KEY_RULE, VALUE_RULE, DISTINCT_KEYS, COMPARABLE_KEYS, COMPARABLE_RATE,
    PAIRS, AGREE, DISAGREE, DISAGREE_RATE, CONFLICT_KINDS,
    SERVE_STATUS, SERVE_CONFLICTING, SERVE_WITHHELD, SERVE_ALLOWED,
    ONTO_NAME, ONTO_MODULES, ONTO_CLASS_COUNT, ONTO_TYPING, FINGERPRINT,
})

# Stage output key -> span attribute. Anything not listed still reaches
# trace.jsonl; only span attributes are restricted, so a typo cannot quietly
# create a parallel attribute name.
STAGE_OUTPUT_TO_ATTRIBUTE: Final[Mapping[str, str]] = {
    "facts_per_view": FACTS_PER_VIEW,
    "entities_per_view": ENTITIES_PER_VIEW,
    "distinct_keys": DISTINCT_KEYS,
    "comparable_keys": COMPARABLE_KEYS,
    "comparable_key_rate": COMPARABLE_RATE,
    "pairs": PAIRS,
    "agree": AGREE,
    "disagree": DISAGREE,
    "disagreement_rate": DISAGREE_RATE,
    "kinds": CONFLICT_KINDS,
    "status": SERVE_STATUS,
    "conflicting_slots": SERVE_CONFLICTING,
    "withheld_protected_slots": SERVE_WITHHELD,
    "servable": SERVE_ALLOWED,
    "declared_classes": ONTO_CLASS_COUNT,
    "typing": ONTO_TYPING,
    "case": CASE_ID,
    "views": VIEW_NAMES,
    "key_rule": KEY_RULE,
    "name": ONTO_NAME,
    "modules": ONTO_MODULES,
}


def attribute_for(key: str) -> str | None:
    """The declared span attribute for a stage field, or None if it is not one."""
    return STAGE_OUTPUT_TO_ATTRIBUTE.get(key)


def coerce(value: Any) -> Any:
    """OTel accepts scalars and homogeneous sequences; everything else is JSON."""
    import json

    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        if all(isinstance(v, str) for v in value):
            return list(value)
        if all(isinstance(v, bool) for v in value):
            return list(value)
        if all(isinstance(v, int) for v in value):
            return list(value)
        return json.dumps(list(value), default=str)[:1800]
    if isinstance(value, dict) and all(
            isinstance(v, (int, float)) for v in value.values()):
        # counts keyed by name are the common case; keep them queryable
        return json.dumps(value, sort_keys=True)[:1800]
    return json.dumps(value, default=str)[:1800]
