"""Versioned ontology snapshot store — Layer 3 of the ontology-management roadmap.

The scorecard (ADR-0114), OntoClean critic (ADR-0115) and corpus-aware tier
(ADR-0116) measure and refine an ontology. This module gives those refinements a
*home*: an immutable, content-addressed snapshot store that persists each
ontology version **together with the evaluation evidence that justified it** —
its scorecard, its OntoClean consensus tags, the corpus profile it was judged
against, and the weight profile used. That makes version acceptance objective
("is v2 actually a better guardrail than v1?") and lets a downstream consumer
pull a known-good, evidence-backed ontology by version.

Design:

- **Filesystem-backed, JSON, offline.** No DB, no LLM. One file per snapshot
  under ``<root>/<package_id>/<version>__<fp8>.json``.
- **Immutable + content-addressed.** A snapshot is keyed by ``(package_id,
  version)`` and stamped with the schema fingerprint. Re-saving the same version
  with the *same* fingerprint is idempotent; with a *different* fingerprint it
  raises — you cannot silently mutate a published version (forces a real bump).
- **Carries evidence.** Optional scorecard / OntoClean tags / corpus profile /
  weight-profile travel with the ontology, so ``compare`` can report not just a
  schema diff but a measured guardrail-value delta.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ontology import Ontology
from .ontology_versioning import build_ontology_upgrade_plan, parse_semver


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-._") else "_" for c in str(name))


@dataclass(slots=True)
class OntologySnapshot:
    """One immutable, evidence-carrying ontology version."""

    package_id: str
    version: str
    schema_fingerprint: str
    created_at: str
    ontology: Dict[str, Any]                      # Ontology.to_dict()
    scorecard: Optional[Dict[str, Any]] = None    # OntologyScorecard.to_dict()
    ontoclean_tags: Optional[Dict[str, Any]] = None
    corpus_profile: Optional[Dict[str, Any]] = None
    weight_profile: str = "balanced"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id,
            "version": self.version,
            "schema_fingerprint": self.schema_fingerprint,
            "created_at": self.created_at,
            "ontology": self.ontology,
            "scorecard": self.scorecard,
            "ontoclean_tags": self.ontoclean_tags,
            "corpus_profile": self.corpus_profile,
            "weight_profile": self.weight_profile,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OntologySnapshot":
        return cls(
            package_id=data["package_id"],
            version=data["version"],
            schema_fingerprint=data["schema_fingerprint"],
            created_at=data.get("created_at", ""),
            ontology=data.get("ontology", {}),
            scorecard=data.get("scorecard"),
            ontoclean_tags=data.get("ontoclean_tags"),
            corpus_profile=data.get("corpus_profile"),
            weight_profile=data.get("weight_profile", "balanced"),
            notes=data.get("notes", ""),
        )

    def load_ontology(self) -> Ontology:
        return Ontology.from_dict(self.ontology)

    def overall_score(self) -> Optional[float]:
        return (self.scorecard or {}).get("overall_score")

    def dimension_score(self, name: str) -> Optional[float]:
        for d in (self.scorecard or {}).get("dimensions", []):
            if d.get("name") == name:
                return d.get("score")
        return None


class SnapshotConflict(Exception):
    """Raised when a version is re-saved with different content (a silent
    mutation of a published version)."""


class OntologySnapshotStore:
    """Filesystem-backed store of immutable, evidence-carrying ontology versions."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- paths -----------------------------------------------------------
    def _pkg_dir(self, package_id: str) -> Path:
        d = self.root / _safe(package_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _path(self, package_id: str, version: str, fingerprint: str) -> Path:
        return self._pkg_dir(package_id) / f"{_safe(version)}__{fingerprint[:8]}.json"

    # ---- write -----------------------------------------------------------
    def save(
        self,
        ontology: Ontology,
        *,
        scorecard: Optional[Any] = None,
        ontoclean_tags: Optional[Dict[str, Any]] = None,
        corpus_profile: Optional[Any] = None,
        weight_profile: str = "balanced",
        notes: str = "",
        created_at: Optional[str] = None,
    ) -> OntologySnapshot:
        """Persist ``ontology`` as a snapshot of ``(package_id, version)``.

        ``scorecard`` may be an ``OntologyScorecard`` or its dict; ``corpus_profile``
        a ``CorpusProfile`` or its dict; ``ontoclean_tags`` the dict form from
        ``dump_metaproperties``. Idempotent for identical content; raises
        :class:`SnapshotConflict` if the version already exists with different
        content."""
        fp = ontology.schema_fingerprint()
        snap = OntologySnapshot(
            package_id=ontology.package_id,
            version=ontology.version,
            schema_fingerprint=fp,
            created_at=created_at or _now_iso(),
            ontology=ontology.to_dict(),
            scorecard=scorecard.to_dict() if hasattr(scorecard, "to_dict") else scorecard,
            ontoclean_tags=ontoclean_tags,
            corpus_profile=corpus_profile.to_dict() if hasattr(corpus_profile, "to_dict") else corpus_profile,
            weight_profile=weight_profile,
            notes=notes,
        )

        # immutability guard: same version + different fingerprint = conflict
        for existing in self._versions(ontology.package_id):
            if existing.version == ontology.version and existing.schema_fingerprint != fp:
                raise SnapshotConflict(
                    f"version '{ontology.version}' of '{ontology.package_id}' already exists with a "
                    f"different schema (fingerprint {existing.schema_fingerprint[:8]} != {fp[:8]}). "
                    f"Bump the version before saving changed content."
                )

        path = self._path(ontology.package_id, ontology.version, fp)
        path.write_text(json.dumps(snap.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return snap

    # ---- read ------------------------------------------------------------
    def _versions(self, package_id: str) -> List[OntologySnapshot]:
        d = self.root / _safe(package_id)
        if not d.exists():
            return []
        out = []
        for f in d.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as file_obj:
                    out.append(OntologySnapshot.from_dict(json.load(file_obj)))
            except Exception:
                continue
        return out

    @staticmethod
    def _sort_key(s: OntologySnapshot):
        sv = parse_semver(s.version)
        # valid semver sorts by tuple first; invalid versions fall back to created_at
        return (0, sv, s.created_at) if sv else (1, (0, 0, 0), s.created_at)

    def list(self, package_id: Optional[str] = None) -> List[OntologySnapshot]:
        """All snapshots (optionally for one package), oldest→newest by semver
        then creation time."""
        if package_id is not None:
            snaps = self._versions(package_id)
        else:
            snaps = [s for d in self.root.iterdir() if d.is_dir() for s in self._versions(d.name)]
        return sorted(snaps, key=self._sort_key)

    def get(self, package_id: str, version: str) -> Optional[OntologySnapshot]:
        for s in self._versions(package_id):
            if s.version == version:
                return s
        return None

    def latest(self, package_id: str) -> Optional[OntologySnapshot]:
        snaps = self.list(package_id)
        return snaps[-1] if snaps else None

    def history(self, package_id: str) -> List[Dict[str, Any]]:
        """A compact lineage timeline for display."""
        return [
            {
                "version": s.version,
                "created_at": s.created_at,
                "schema_fingerprint": s.schema_fingerprint[:8],
                "grade": (s.scorecard or {}).get("grade"),
                "overall_score": s.overall_score(),
                "corpus_coverage": s.dimension_score("corpus_coverage"),
                "notes": s.notes,
            }
            for s in self.list(package_id)
        ]

    # ---- compare ---------------------------------------------------------
    def compare(self, package_id: str, from_version: str, to_version: str) -> Dict[str, Any]:
        """Compare two stored versions: schema diff + recommended bump + measured
        scorecard / guardrail-value delta + a verdict on whether ``to_version`` is
        a better guardrail."""
        a = self.get(package_id, from_version)
        b = self.get(package_id, to_version)
        if a is None or b is None:
            missing = from_version if a is None else to_version
            raise KeyError(f"snapshot '{package_id}' v'{missing}' not found")

        plan = build_ontology_upgrade_plan(a.load_ontology(), b.load_ontology())

        # scorecard deltas
        score_delta: Dict[str, Any] = {}
        if a.scorecard and b.scorecard:
            if a.overall_score() is not None and b.overall_score() is not None:
                score_delta["overall"] = round(b.overall_score() - a.overall_score(), 4)
            dims = {d["name"] for d in a.scorecard.get("dimensions", [])} | {
                d["name"] for d in b.scorecard.get("dimensions", [])
            }
            score_delta["by_dimension"] = {
                name: round((b.dimension_score(name) or 0.0) - (a.dimension_score(name) or 0.0), 4)
                for name in sorted(dims)
                if a.dimension_score(name) is not None and b.dimension_score(name) is not None
            }

        # guardrail verdict: prefer corpus_coverage (the downstream-predictive
        # signal, ADR-0116); fall back to overall.
        cc_a, cc_b = a.dimension_score("corpus_coverage"), b.dimension_score("corpus_coverage")
        verdict, basis, delta = "unknown", None, None
        if cc_a is not None and cc_b is not None:
            basis, delta = "corpus_coverage", round(cc_b - cc_a, 4)
        elif score_delta.get("overall") is not None:
            basis, delta = "overall_score", score_delta["overall"]
        if delta is not None:
            verdict = "better" if delta > 0.001 else ("worse" if delta < -0.001 else "equal")

        return {
            "package_id": package_id,
            "from_version": from_version,
            "to_version": to_version,
            "schema_changed": a.schema_fingerprint != b.schema_fingerprint,
            "recommended_bump": plan.recommended_bump,
            "requires_migration": plan.requires_migration,
            "changes": plan.changes,
            "scorecard_delta": score_delta,
            "guardrail_verdict": {"verdict": verdict, "basis": basis, "delta": delta},
        }
