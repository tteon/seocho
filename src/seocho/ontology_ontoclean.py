"""OntoClean meta-property critic for is-a (subclass) hierarchies.

OntoClean (Guarino & Welty, 2002, *Evaluating ontological decisions with
OntoClean*) validates a taxonomy by tagging each class with four formal
meta-properties and then checking that every subsumption (``broader``) edge
respects the constraints those meta-properties impose. It is the rigorous core
of "axiom adjustment / taxonomy design": it catches is-a edges that are
*formally* wrong, not merely stylistically off.

This module has two cleanly separated halves:

1. A **pure constraint engine** (:func:`check_ontoclean`) that, given
   meta-property tags, deterministically reports violations. No LLM, no I/O —
   fully unit-testable offline. This is the part that encodes the OntoClean
   axioms.
2. An **optional, injectable inference step** (:func:`infer_metaproperties`)
   that uses an LLM backend (MARA by default) to *propose* the tags, since
   manual OntoClean tagging is the expensive part of the methodology. The
   backend is injected so the engine never depends on a live model, and so the
   scorecard stays offline (it consumes precomputed tags, never calls an LLM).

The four meta-properties (tri-state: ``True`` / ``False`` / ``None`` = unknown,
which conservatively skips its constraint so unknowns never produce a false
violation):

- ``rigid`` — +R: being an X is essential to every X for its whole existence.
  ~R (anti-rigid): an instance can stop being an X yet keep existing (roles like
  *Student*, phases like *Child*).
- ``carries_identity`` — +I: instances have an identity criterion (a way to tell
  two apart and re-identify one over time).
- ``supplies_identity`` — +O: the class itself *introduces* that identity
  criterion rather than inheriting it.
- ``unity`` — +U: every instance is a single whole under one unifying relation;
  ~U is anti-unity.
- ``dependent`` — +D: every instance necessarily depends on some other entity
  (e.g. *Student* on a school, *Spouse* on a partner).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ontology import Ontology


@dataclass(slots=True)
class MetaProperties:
    """OntoClean meta-property tags for one class. Tri-state; ``None`` = unknown."""

    rigid: Optional[bool] = None
    carries_identity: Optional[bool] = None
    supplies_identity: Optional[bool] = None
    unity: Optional[bool] = None
    dependent: Optional[bool] = None
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rigid": self.rigid,
            "carries_identity": self.carries_identity,
            "supplies_identity": self.supplies_identity,
            "unity": self.unity,
            "dependent": self.dependent,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MetaProperties":
        def _tri(v: Any) -> Optional[bool]:
            if v is None:
                return None
            if isinstance(v, str):
                t = v.strip().lower()
                if t in ("true", "yes", "+", "rigid"):
                    return True
                if t in ("false", "no", "-", "~", "anti"):
                    return False
                return None
            return bool(v)

        return cls(
            rigid=_tri(data.get("rigid")),
            carries_identity=_tri(data.get("carries_identity")),
            supplies_identity=_tri(data.get("supplies_identity")),
            unity=_tri(data.get("unity")),
            dependent=_tri(data.get("dependent")),
            rationale=str(data.get("rationale", "") or ""),
        )


@dataclass(slots=True)
class OntoCleanViolation:
    """One violated OntoClean subsumption constraint on a ``child broader parent``
    edge (i.e. *parent subsumes child*)."""

    constraint: str          # "rigidity" | "identity" | "unity" | "dependence"
    parent: str
    child: str
    severity: str            # "violation" | "warning"
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "constraint": self.constraint,
            "parent": self.parent,
            "child": self.child,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(slots=True)
class OntoCleanResult:
    ok: bool
    edges_checked: int
    violations: List[OntoCleanViolation] = field(default_factory=list)
    untagged_classes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "edges_checked": self.edges_checked,
            "violation_count": len(self.violations),
            "violations": [v.to_dict() for v in self.violations],
            "untagged_classes": list(self.untagged_classes),
        }


# ---------------------------------------------------------------------------
# Pure constraint engine
# ---------------------------------------------------------------------------


def check_ontoclean(
    ontology: Ontology,
    tags: Dict[str, MetaProperties],
) -> OntoCleanResult:
    """Check every ``broader`` (is-a) edge against the OntoClean constraints.

    Parameters
    ----------
    ontology:
        The ontology whose ``broader`` hierarchy is validated.
    tags:
        Meta-property tags per class label. Classes missing a tag are reported
        in ``untagged_classes``; their edges are still checked against whatever
        tags ARE present on the other endpoint (a ``None`` on either side skips
        the affected constraint).

    Returns
    -------
    OntoCleanResult
        ``ok`` is True iff no hard ``violation`` was found (``warning`` does not
        flip it). Each violation names the constraint, the parent/child, and a
        plain-language explanation of why the is-a edge is formally wrong.
    """
    node_labels = set(ontology.nodes)
    violations: List[OntoCleanViolation] = []
    edges = 0

    for child, nd in ontology.nodes.items():
        for parent in (getattr(nd, "broader", []) or []):
            if parent not in node_labels:
                continue  # dangling broader — lint_ontology owns that finding
            edges += 1
            p = tags.get(parent)
            c = tags.get(child)
            if p is None and c is None:
                continue

            p = p or MetaProperties()
            c = c or MetaProperties()

            # C1 — Rigidity: an anti-rigid class cannot subsume a rigid one.
            # Classic: Student(~R) must not be a superclass of Person(+R).
            if p.rigid is False and c.rigid is True:
                violations.append(OntoCleanViolation(
                    "rigidity", parent, child, "violation",
                    f"'{child}' (rigid: being a {child} is permanent) is placed under anti-rigid '{parent}' "
                    f"(an instance can stop being a {parent}). An anti-rigid class cannot subsume a rigid one — "
                    f"re-parent '{child}' or model the '{parent}' aspect as a role/relationship, not a superclass.",
                ))

            # C2 — Identity: identity criteria are inherited downward. A class
            # that carries identity must not be subsumed by one that lacks it,
            # and a child must not drop an identity criterion the parent supplies.
            if p.carries_identity is True and c.carries_identity is False:
                violations.append(OntoCleanViolation(
                    "identity", parent, child, "violation",
                    f"'{parent}' carries an identity criterion but its subclass '{child}' is tagged as carrying "
                    f"none. Identity is inherited downward — a subclass cannot drop it.",
                ))
            # Soft check: both endpoints supply their OWN identity with different
            # keys → incompatible own-identity along one chain.
            if p.supplies_identity is True and c.supplies_identity is True:
                p_keys = set(ontology.nodes[parent].effective_identity_keys)
                c_keys = set(ontology.nodes[child].effective_identity_keys)
                if p_keys and c_keys and not (c_keys >= p_keys):
                    violations.append(OntoCleanViolation(
                        "identity", parent, child, "warning",
                        f"'{parent}' and '{child}' each supply their own identity criterion with incompatible keys "
                        f"({sorted(p_keys)} vs {sorted(c_keys)}). A subclass should extend, not replace, the "
                        f"parent's identity criterion.",
                    ))

            # C3 — Unity: a class with a unity criterion (+U) cannot subsume one
            # with anti-unity (~U), or vice versa.
            if p.unity is not None and c.unity is not None and p.unity != c.unity:
                violations.append(OntoCleanViolation(
                    "unity", parent, child, "violation",
                    f"Unity mismatch: '{parent}' (+U={p.unity}) cannot subsume '{child}' (+U={c.unity}). "
                    f"A whole and a non-whole do not stand in an is-a relation.",
                ))

            # C4 — Dependence: dependence is inherited. A dependent class cannot
            # subsume an independent one (parent +D, child -D).
            if p.dependent is True and c.dependent is False:
                violations.append(OntoCleanViolation(
                    "dependence", parent, child, "violation",
                    f"'{parent}' is externally dependent but its subclass '{child}' is independent. "
                    f"A dependent class cannot subsume an independent one — dependence is inherited downward.",
                ))

    untagged = sorted(l for l in node_labels if l not in tags)
    hard = [v for v in violations if v.severity == "violation"]
    return OntoCleanResult(
        ok=not hard,
        edges_checked=edges,
        violations=violations,
        untagged_classes=untagged,
    )


# ---------------------------------------------------------------------------
# Optional LLM-assisted meta-property inference (MARA by default)
# ---------------------------------------------------------------------------

_INFER_SYSTEM = (
    "You are an ontology engineer applying the OntoClean methodology (Guarino & "
    "Welty). For each class you assign four formal meta-properties. Answer ONLY "
    "with the JSON object requested — no prose outside it."
)

_INFER_GUIDE = """For each class decide, using its label, definition and properties:

- rigid: true if being an instance of this class is ESSENTIAL and PERMANENT (a thing
  cannot stop being one while continuing to exist, e.g. Person, Organism). false if
  ANTI-RIGID — an instance can stop being one yet keep existing (roles like Student,
  Employee, CEO; phases like Child). null if unsure.
- carries_identity: true if instances have an identity criterion (a way to tell two
  apart and re-identify one over time). Most concrete object types are true; pure
  qualities/amounts are often false. null if unsure.
- supplies_identity: true if THIS class introduces its own identity criterion rather
  than inheriting one from a parent. null if unsure.
- unity: true if every instance is a single connected WHOLE under one unifying relation
  (e.g. an Organism, a Machine). false (anti-unity) for amounts/collections that need
  not be wholes. null if unsure.
- dependent: true if every instance NECESSARILY depends on some other distinct entity
  existing (Student needs a school; Spouse needs a partner; Subsidiary needs a parent
  company). false for independent substances. null if unsure.

Also give a one-sentence "rationale".
"""


def build_inference_prompt(ontology: Ontology) -> str:
    """The user-message body for meta-property inference (exposed for logging /
    reproducibility)."""
    lines = [_INFER_GUIDE, "", "Classes:"]
    for label, nd in ontology.nodes.items():
        desc = str(getattr(nd, "description", "") or "").strip() or "(no definition)"
        props = ", ".join(nd.properties.keys()) or "(none)"
        broader = ", ".join(getattr(nd, "broader", []) or []) or "(none)"
        lines.append(f"- {label}: {desc} | properties: {props} | broader: {broader}")
    lines.append("")
    lines.append(
        'Return JSON: {"classes": {"<Label>": {"rigid": <bool|null>, '
        '"carries_identity": <bool|null>, "supplies_identity": <bool|null>, '
        '"unity": <bool|null>, "dependent": <bool|null>, "rationale": "<str>"}}}'
    )
    return "\n".join(lines)


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Back-compat shim — delegates to the canonical robust extractor in
    :mod:`seocho.llm_structured`."""
    from .llm_structured import extract_json_object
    return extract_json_object(text)


def infer_metaproperties(
    ontology: Ontology,
    *,
    backend: Any,
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> Dict[str, MetaProperties]:
    """Propose OntoClean meta-property tags for every class via an LLM backend.

    ``backend`` is any object exposing ``complete(system=, user=, temperature=,
    response_format=)`` returning an object with ``.text`` / ``.json()`` (the
    SEOCHO ``LLMBackend`` contract; construct a MARA one with
    ``create_llm_backend(provider="mara")``). Injected, not constructed here, so
    the critic never hard-depends on a live model and tests run offline.
    ``max_tokens`` is generous by default because reasoning models spend tokens
    on chain-of-thought before emitting the JSON.

    Returns a ``{label: MetaProperties}`` map. Labels the model omits are simply
    absent (treated as untagged by :func:`check_ontoclean`).
    """
    from .llm_structured import StructuredOutputError, structured_complete

    user = build_inference_prompt(ontology)
    # Route through the provider/model-aware structured-output layer (seocho-ub5)
    # so reasoning models (MiniMax/gpt-oss) that emit chain-of-thought or need a
    # higher max_tokens floor are handled instead of lost to JSON-parse failure.
    try:
        payload = structured_complete(
            backend, system=_INFER_SYSTEM, user=user,
            temperature=temperature, max_tokens=max_tokens, task_hint="json_extraction",
        )
    except StructuredOutputError:
        return {}
    classes = payload.get("classes", payload) if isinstance(payload, dict) else {}
    tags: Dict[str, MetaProperties] = {}
    for label in ontology.nodes:
        if label in classes and isinstance(classes[label], dict):
            tags[label] = MetaProperties.from_dict(classes[label])
    return tags


def load_metaproperties(data: Dict[str, Dict[str, Any]]) -> Dict[str, MetaProperties]:
    """Build a tag map from a plain dict (e.g. a hand-authored or cached JSON
    file): ``{label: {rigid: ..., ...}}``."""
    return {label: MetaProperties.from_dict(md) for label, md in data.items()}


def dump_metaproperties(tags: Dict[str, MetaProperties]) -> Dict[str, Dict[str, Any]]:
    """Serialise a tag map for caching / recording (the data-trail requirement)."""
    return {label: mp.to_dict() for label, mp in tags.items()}
