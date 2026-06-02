from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class FIBOModule:
    """Declarative manifest for a single FIBO module.

    ``label_index`` maps ``rdfs:label`` (lowercased) to the FIBO class IRI.
    It is precomputed offline by an owlready2-backed compile job and read
    by the runtime without invoking the reasoner (CLAUDE.md §6.3).
    """

    code: str
    iri_prefix: str
    summary: str
    label_index: Mapping[str, str] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    load_cost_ms: int = 0
    fibo_version: str = ""


@dataclass(frozen=True, slots=True)
class FIBOCatalog:
    """In-memory registry of FIBO modules available to a workspace.

    The catalog is workspace-scoped and pinned to a single ``fibo_version``.
    Code lookup is case-sensitive; consumers should normalize before
    constructing the catalog.
    """

    modules: Mapping[str, FIBOModule]
    fibo_version: str = ""

    @classmethod
    def from_modules(
        cls, modules: Iterable[FIBOModule], *, fibo_version: str = ""
    ) -> "FIBOCatalog":
        registry: dict[str, FIBOModule] = {}
        for module in modules:
            if module.code in registry:
                raise ValueError(f"duplicate FIBO module code: {module.code}")
            registry[module.code] = module
        return cls(modules=registry, fibo_version=fibo_version)

    def get(self, code: str) -> FIBOModule | None:
        return self.modules.get(code)

    def require(self, code: str) -> FIBOModule:
        module = self.modules.get(code)
        if module is None:
            raise KeyError(f"FIBO module not in catalog: {code}")
        return module

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(self.modules))

    def with_dependencies(self, codes: Iterable[str]) -> tuple[str, ...]:
        """Expand ``codes`` to include all transitive ``depends_on`` modules.

        Returns codes sorted alphabetically for deterministic output. Raises
        ``KeyError`` on any code not present in the catalog. Cycles in
        ``depends_on`` are tolerated (visited set protects the walk).
        """

        resolved: set[str] = set()
        stack: list[str] = list(codes)
        while stack:
            code = stack.pop()
            if code in resolved:
                continue
            module = self.require(code)
            resolved.add(code)
            stack.extend(module.depends_on)
        return tuple(sorted(resolved))
