"""DDD bounded contexts + context map over SEOCHO ontologies (Eric Evans).

SEOCHO's ontology is a strong domain model (aliases = ubiquitous language,
``same_as`` = context mapping, cardinality/SHACL = invariants), but its
*bounded-context boundaries* are implicit: multiple ontologies can be registered
per workspace, and misalignment is only caught **after** the fact via
``OntologyDriftError``. DDD wants the opposite — an explicit **context map** that
says which context owns which concept and how concepts translate across
contexts, validated **proactively**.

This module models that:

* :class:`BoundedContext` — a named context that *owns* a set of concepts and
  declares *translations* of its concepts to other contexts (the
  anti-corruption mapping, derived from the ontology's ``same_as``).
* :class:`ContextMap` — registers contexts and validates boundaries up front:
  a concept owned by two contexts (blurred boundary), or a translation that
  points at an unknown context / a concept that context doesn't own (a broken
  anti-corruption layer).

Pure and deterministic — no graph, no LLM — so boundary checks run in CI as a
fitness function instead of surfacing at runtime as drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping

__all__ = ["BoundedContext", "BoundaryViolation", "ContextMap"]


def _norm(value: str) -> str:
    return str(value).strip()


@dataclass(frozen=True)
class BoundedContext:
    """A bounded context: the concepts it owns + cross-context translations.

    ``translations`` maps a locally-owned concept to ``"<other_context>:<concept>"``
    — the explicit statement "our X is their Y" that DDD's context map and
    anti-corruption layer require.
    """

    name: str
    concepts: frozenset
    translations: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_ontology(cls, name: str, ontology, *, context_of_same_as=None) -> "BoundedContext":
        """Build a context from an Ontology-like object (duck-typed).

        Concepts = node labels it owns. Translations = each node's ``same_as``
        (e.g. ``"schema:Organization"``), interpreted as
        ``<prefix>:<concept>`` where the prefix names the foreign context.
        ``context_of_same_as`` may remap a ``same_as`` prefix to a context name.
        """
        nodes = getattr(ontology, "nodes", {}) or {}
        concepts = {_norm(label) for label in nodes.keys()}
        translations: Dict[str, str] = {}
        for label, nodedef in nodes.items():
            same_as = getattr(nodedef, "same_as", None)
            if not same_as:
                continue
            target = same_as
            if context_of_same_as and ":" in same_as:
                prefix, _, local = same_as.partition(":")
                mapped = context_of_same_as.get(prefix)
                if mapped:
                    target = f"{mapped}:{local}"
            translations[_norm(label)] = _norm(target)
        return cls(name=_norm(name), concepts=frozenset(concepts), translations=translations)


@dataclass(frozen=True)
class BoundaryViolation:
    kind: str     # "shared_ownership" | "dangling_translation"
    detail: str


class ContextMap:
    """Registry of bounded contexts with proactive boundary validation."""

    def __init__(self) -> None:
        self._contexts: Dict[str, BoundedContext] = {}

    def register(self, context: BoundedContext) -> None:
        self._contexts[context.name] = context

    def owners_of(self, concept: str) -> List[str]:
        concept = _norm(concept)
        return sorted(c.name for c in self._contexts.values() if concept in c.concepts)

    def translate(self, concept: str, *, from_context: str) -> str | None:
        ctx = self._contexts.get(_norm(from_context))
        if ctx is None:
            return None
        return ctx.translations.get(_norm(concept))

    def validate(self) -> List[BoundaryViolation]:
        """Return boundary violations, proactively (empty == clean).

        - shared_ownership: a concept owned by more than one context — the
          boundary is blurred; one context should own it and others translate to it.
        - dangling_translation: a translation whose target context is unknown, or
          whose target concept that context doesn't own — a broken anti-corruption
          mapping. Translations to an *external* vocabulary (a prefix that is not a
          registered context, e.g. ``schema:``) are left alone — only translations
          that name a known context are checked.
        """
        violations: List[BoundaryViolation] = []

        # shared ownership across contexts
        owners: Dict[str, List[str]] = {}
        for ctx in self._contexts.values():
            for concept in ctx.concepts:
                owners.setdefault(concept, []).append(ctx.name)
        for concept, names in owners.items():
            if len(names) > 1:
                violations.append(BoundaryViolation(
                    "shared_ownership",
                    f"concept '{concept}' is owned by multiple contexts: {sorted(names)}",
                ))

        # translations that name a known context must resolve to a concept it owns
        for ctx in self._contexts.values():
            for local, target in ctx.translations.items():
                if ":" not in target:
                    continue
                prefix, _, foreign_concept = target.partition(":")
                other = self._contexts.get(prefix)
                if other is None:
                    continue  # external vocabulary (schema:, fibo:, ...) — not our boundary
                if _norm(foreign_concept) not in other.concepts:
                    violations.append(BoundaryViolation(
                        "dangling_translation",
                        f"{ctx.name}.{local} -> {target}, but context '{prefix}' "
                        f"does not own concept '{foreign_concept}'",
                    ))
        return sorted(violations, key=lambda v: (v.kind, v.detail))

    def is_consistent(self) -> bool:
        return not self.validate()
