"""Keet's modularisation metrics, computed over an ontology's modules.

`score_ontology` measures one ontology as a single artefact — is it structurally
sound, does it have a taxonomy, are its properties defined. None of that says
anything about how it is *divided*, and division is what decides whether an
ontology can evolve: whether a module can be changed without changing the rest,
whether two modules duplicate the same axioms, whether a module is self-contained
enough to be swapped.

Implements the metrics in Keet, *An Introduction to Ontology Engineering*,
§11.3, with the value bands from her Table 11.1:

    metric               range     good
    relative size        0..1      small to medium
    cohesion             0..1      small
    coupling             >=0       small
    redundancy           0..1      small to medium
    encapsulation        0..1      large
    independence         bool      true
    attribute richness   >=0       --
    inheritance richness >=0       --

Two of those bands read backwards at first and are worth stating plainly.
*Cohesion* here counts internal relationship density and Keet marks SMALL as
good, because a module whose entities are all wired to each other cannot be
decomposed further. *Relative size* is likewise small-to-medium: a module that
holds most of the ontology has not modularised anything.

A module is a group of classes. Where an ontology declares no grouping this
falls back to `broader` roots — each top-level class and its descendants form a
module — which is the closest thing to a partition an LPG ontology carries. That
fallback is reported in the output rather than hidden, because on a flat
ontology it yields one module per class and the metrics say so instead of
pretending the ontology is well-modularised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

SCHEMA_VERSION = 1

#: Keet Table 11.1's 4-point scale.
BANDS = ((0.25, "small"), (0.50, "medium"), (0.75, "moderate"), (1.01, "large"))


def band(value: Optional[float]) -> str:
    if value is None:
        return "-"
    for threshold, label in BANDS:
        if value < threshold:
            return label
    return "large"


@dataclass
class ModuleMetrics:
    """One module's numbers. `entities` is |Mi| in Keet's notation."""

    name: str
    entities: int
    relative_size: float
    cohesion: float
    coupling: float
    encapsulation: float
    independent: bool
    attribute_richness: float
    inheritance_richness: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entities": self.entities,
            "relative_size": self.relative_size,
            "relative_size_band": band(self.relative_size),
            "cohesion": self.cohesion,
            "cohesion_band": band(self.cohesion),
            "coupling": self.coupling,
            "encapsulation": self.encapsulation,
            "encapsulation_band": band(self.encapsulation),
            "independent": self.independent,
            "attribute_richness": self.attribute_richness,
            "inheritance_richness": self.inheritance_richness,
        }


@dataclass
class ModularityReport:
    modules: List[ModuleMetrics] = field(default_factory=list)
    redundancy: float = 0.0
    partition_source: str = ""      # how modules were derived
    total_entities: int = 0
    schema_version: int = SCHEMA_VERSION

    @property
    def independent_modules(self) -> int:
        return sum(1 for m in self.modules if m.independent)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "partition_source": self.partition_source,
            "module_count": len(self.modules),
            "total_entities": self.total_entities,
            "redundancy": self.redundancy,
            "redundancy_band": band(self.redundancy),
            "independent_modules": self.independent_modules,
            "modules": [m.to_dict() for m in self.modules],
        }


def _descendants(label: str, children: Dict[str, List[str]], seen: Set[str]) -> List[str]:
    out = [label]
    seen.add(label)
    for child in children.get(label, []):
        if child not in seen:
            out.extend(_descendants(child, children, seen))
    return out


def partition(ontology: Any,
              modules: Optional[Dict[str, Sequence[str]]] = None) -> tuple:
    """Return (modules, how). Explicit grouping wins; otherwise taxonomy roots.

    An LPG ontology has no module construct, so the honest fallback is the
    taxonomy: every class with no `broader` parent seeds a module containing its
    descendants. On a flat ontology that degenerates to one module per class,
    and the metrics then report a flat ontology rather than a modular one —
    which is the correct answer, not a defect in the measurement.
    """
    nodes = getattr(ontology, "nodes", None) or {}
    if modules:
        return {k: [c for c in v if c in nodes] for k, v in modules.items()}, "declared"

    children: Dict[str, List[str]] = {}
    has_parent: Set[str] = set()
    for label, node in nodes.items():
        for parent in (getattr(node, "broader", None) or []):
            if parent in nodes:
                children.setdefault(parent, []).append(label)
                has_parent.add(label)

    roots = [label for label in nodes if label not in has_parent]
    seen: Set[str] = set()
    derived = {root: _descendants(root, children, seen) for root in roots}
    return derived, "taxonomy_roots" if children else "flat_one_module_per_class"


def analyse(ontology: Any,
            modules: Optional[Dict[str, Sequence[str]]] = None) -> ModularityReport:
    nodes = getattr(ontology, "nodes", None) or {}
    relationships = getattr(ontology, "relationships", None) or {}
    grouped, source = partition(ontology, modules)

    total = len(nodes)
    report = ModularityReport(partition_source=source, total_entities=total)
    if not total or not grouped:
        return report

    member_of: Dict[str, str] = {}
    for name, members in grouped.items():
        for label in members:
            member_of.setdefault(label, name)

    # Edge counts, split by whether both ends sit in the same module.
    internal: Dict[str, int] = {name: 0 for name in grouped}
    external: Dict[str, int] = {name: 0 for name in grouped}
    for rel in relationships.values():
        source_label = getattr(rel, "source", None)
        target_label = getattr(rel, "target", None)
        left, right = member_of.get(source_label), member_of.get(target_label)
        if left is None or right is None:
            continue
        if left == right:
            internal[left] += 1
        else:
            external[left] += 1
            external[right] += 1

    for name, members in grouped.items():
        size = len(members)
        if size == 0:
            continue

        # Keet 11.12: coupling normalises cross-module edges by the product of
        # module sizes, so a large module is not penalised for having more of
        # them in absolute terms.
        others = sum(len(v) for k, v in grouped.items() if k != name)
        coupling = (external[name] / (size * others)) if others else 0.0

        # Cohesion as internal relationship density. Keet marks SMALL as good:
        # a module whose entities are all wired together cannot be split again.
        pairs = size * (size - 1) / 2
        cohesion = (internal[name] / pairs) if pairs else 0.0

        # Keet 11.14: encapsulation falls as a module's axioms reach outside it.
        axioms = internal[name] + external[name]
        encapsulation = 1.0 - (external[name] / axioms) if axioms else 1.0

        attributes = sum(len(getattr(nodes[label], "properties", None) or {})
                         for label in members if label in nodes)
        subclasses = sum(
            1 for label in members
            if label in nodes and (getattr(nodes[label], "broader", None) or [])
        )

        report.modules.append(ModuleMetrics(
            name=name,
            entities=size,
            relative_size=size / total,
            cohesion=cohesion,
            coupling=coupling,
            encapsulation=encapsulation,
            # Keet 11.15: independent iff fully encapsulated AND uncoupled.
            independent=(encapsulation >= 1.0 and coupling == 0.0),
            attribute_richness=attributes / size,      # Keet 11.16
            inheritance_richness=subclasses / size,    # Keet 11.17
        ))

    # Keet 11.13: how much of the summed module membership is duplication.
    summed = sum(len(v) for v in grouped.values())
    distinct = len({label for v in grouped.values() for label in v})
    report.redundancy = ((summed - distinct) / summed) if summed else 0.0
    return report


def findings(report: ModularityReport) -> List[Dict[str, str]]:
    """Turn the numbers into statements, using Keet's good/bad direction.

    Written out because three of the bands run against intuition — small
    cohesion, small relative size, and small coupling are all the good end, and
    a reader who assumes bigger-is-better reads the report backwards.
    """
    out: List[Dict[str, str]] = []

    if report.partition_source == "flat_one_module_per_class":
        out.append({
            "severity": "major", "element": "<ontology>",
            "message": ("no taxonomy, so every class is its own module — the "
                        "modularity metrics below describe an unmodularised "
                        "ontology rather than a well-divided one"),
        })

    if report.redundancy > 0.5:
        out.append({
            "severity": "major", "element": "<ontology>",
            "message": (f"redundancy {report.redundancy:.2f} "
                        f"({band(report.redundancy)}): the same classes appear "
                        f"in several modules"),
        })

    for module in report.modules:
        if module.relative_size > 0.5:
            out.append({
                "severity": "major", "element": module.name,
                "message": (f"holds {module.relative_size:.0%} of all classes; a "
                            f"module this large has not modularised anything"),
            })
        if module.coupling > 0.5:
            out.append({
                "severity": "minor", "element": module.name,
                "message": (f"coupling {module.coupling:.2f} is high — it cannot "
                            f"be changed without touching its neighbours"),
            })
        if module.encapsulation < 0.5 and module.entities > 1:
            out.append({
                "severity": "minor", "element": module.name,
                "message": (f"encapsulation {module.encapsulation:.2f} "
                            f"({band(module.encapsulation)}): most of its axioms "
                            f"reach outside the module"),
            })
    return out
