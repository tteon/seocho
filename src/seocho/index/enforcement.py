"""Ontology enforcement policy for the indexing path (seocho-snt).

``EnforcementPolicy`` names the admission policy applied to extracted graph
data against the ontology vocabulary. The three preset modes are the only
YAML/design surface; the individual knobs exist so the presets stay
coherent in one place (a strict prompt combined with an Entity fallback is
an incoherent state we never want expressible from config).

Deliberately NOT named closed-world/open-world: CWA/OWA are inference
regimes, and nothing here changes query-time entailment. ``strict`` is
closed-*vocabulary* admission in the spirit of SHACL closed shapes.
"""

from __future__ import annotations

from dataclasses import dataclass

ENFORCEMENT_MODES = ("strict", "guided", "open")


@dataclass(frozen=True, slots=True)
class EnforcementPolicy:
    """Compiled admission policy for one indexing pipeline.

    mode:
        The preset name this policy was derived from.
    prompt_strict:
        Append the constant closed-vocabulary instruction to extraction
        prompts. The line is ontology-independent by design — the
        extraction firewall (FinDER r=-0.76) forbids ontology richness
        from entering prompts.
    allow_relaxed_retry:
        Permit the empty-extraction relaxed retry ("use closest label or
        generic Entity"). That instruction is the negation of closed
        admission, so strict disables it: an empty extraction is a
        legitimate outcome for out-of-vocabulary text.
    allow_entity_fallback:
        Treat the generic ``Entity`` label as valid even when the ontology
        does not declare it.
    allow_heuristic_fallback:
        Permit the capitalized-token heuristic fallback that manufactures
        ``Entity``/``MENTIONS`` structure when extraction fails or returns
        empty. Strict refuses fabricated out-of-vocabulary structure.
    violation_action:
        ``"reject"`` skips chunks with validation errors (maps onto
        ``IndexingPipeline.strict_validation``); ``"warn"`` records the
        errors and writes anyway.
    closed_validation:
        Run :meth:`Ontology.validate_extraction` in closed mode (no Entity
        exemption, dangling-endpoint and domain/range conformance checks).
    annotate_out_of_ontology:
        Stamp ``_out_of_ontology: "true"`` on nodes/relationships whose
        label or type is not declared by the ontology. Open-mode signal
        for offline governance triage (schema-induction candidates).
    """

    mode: str
    prompt_strict: bool
    allow_relaxed_retry: bool
    allow_entity_fallback: bool
    allow_heuristic_fallback: bool
    violation_action: str
    closed_validation: bool
    annotate_out_of_ontology: bool

    @classmethod
    def from_mode(cls, mode: str) -> "EnforcementPolicy":
        normalized = str(mode or "guided").strip().lower() or "guided"
        if normalized == "strict":
            return cls(
                mode="strict",
                prompt_strict=True,
                allow_relaxed_retry=False,
                allow_entity_fallback=False,
                allow_heuristic_fallback=False,
                violation_action="reject",
                closed_validation=True,
                annotate_out_of_ontology=False,
            )
        if normalized == "open":
            return cls(
                mode="open",
                prompt_strict=False,
                allow_relaxed_retry=True,
                allow_entity_fallback=True,
                allow_heuristic_fallback=True,
                violation_action="warn",
                closed_validation=False,
                annotate_out_of_ontology=True,
            )
        if normalized != "guided":
            raise ValueError(
                f"Unknown enforcement mode {mode!r}. "
                f"Allowed: {', '.join(ENFORCEMENT_MODES)}."
            )
        # guided — today's tuned default (the mode the FinDER experiments
        # validated): ontology guides prompts, relaxed retry and Entity
        # fallback stay available, validation errors warn.
        return cls(
            mode="guided",
            prompt_strict=False,
            allow_relaxed_retry=True,
            allow_entity_fallback=True,
            allow_heuristic_fallback=True,
            violation_action="warn",
            closed_validation=False,
            annotate_out_of_ontology=False,
        )


def annotate_out_of_ontology(
    ontology,
    nodes,
    relationships,
) -> int:
    """Stamp ``_out_of_ontology`` on out-of-vocabulary elements (open mode).

    Mutates properties in place; returns the number of annotated elements.
    Unknown ≠ wrong under open admission, but a graph that cannot
    distinguish sanctioned from unsanctioned assertions is unauditable —
    and the annotation is exactly the signal the offline governance path
    needs for ontology-evolution triage.
    """
    annotated = 0
    declared_labels = set(getattr(ontology, "nodes", {}) or {})
    declared_rels = set(getattr(ontology, "relationships", {}) or {})
    for node in nodes or []:
        label = str(node.get("label", "") or "")
        if label and label not in declared_labels:
            node.setdefault("properties", {})["_out_of_ontology"] = "true"
            annotated += 1
    for rel in relationships or []:
        rtype = str(rel.get("type", "") or "")
        if rtype and rtype not in declared_rels:
            rel.setdefault("properties", {})["_out_of_ontology"] = "true"
            annotated += 1
    return annotated


__all__ = ["ENFORCEMENT_MODES", "EnforcementPolicy", "annotate_out_of_ontology"]
