"""Ambiguity review loop — Phase 1: quarantine, detection, mapping-spec.

The virtuous cycle (선순환): ambiguous / out-of-ontology entities surfaced during
extraction are **quarantined** (never silently dropped or force-fit), aggregated
into ranked clusters, mapped by the user via a declarative mapping-spec (the
durable, git-friendly artifact a UI will later write), and the decisions are
**applied back into the ontology taxonomy** — improving the next extraction.

This module is offline and headless (no LLM, no hot path): detection is a pure
walk over an already-extracted graph; the quarantine is a JSONL store; the
mapping-spec is YAML. LLM-backed proposals and a Streamlit UI are later phases;
this phase gives a testable spine and a CLI (`seocho ontology review`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ontology import NodeDef, Ontology

# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

# Why a mention was quarantined.
SIGNAL_OOV = "oov"                       # label not declared in the ontology
SIGNAL_FALLBACK = "entity_fallback"      # admitted as bare Entity / heuristic fallback
SIGNAL_OUT_OF_ONTOLOGY = "out_of_ontology"  # carries the open-mode _out_of_ontology stamp
SIGNAL_ALIAS_COLLISION = "alias_collision"  # surface maps to >1 declared class


@dataclass(slots=True)
class AmbiguousEntity:
    surface: str                          # the mention's display form (name or label)
    label: str                            # the label extraction assigned (may be OOV/Entity)
    signal: str                           # one of SIGNAL_*
    candidate_labels: List[str] = field(default_factory=list)
    context: str = ""
    source: str = ""
    workspace_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surface": self.surface, "label": self.label, "signal": self.signal,
            "candidate_labels": list(self.candidate_labels), "context": self.context,
            "source": self.source, "workspace_id": self.workspace_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AmbiguousEntity":
        return cls(
            surface=str(d.get("surface", "")), label=str(d.get("label", "")),
            signal=str(d.get("signal", "")), candidate_labels=list(d.get("candidate_labels", []) or []),
            context=str(d.get("context", "")), source=str(d.get("source", "")),
            workspace_id=str(d.get("workspace_id", "")),
        )


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


# ---------------------------------------------------------------------------
# Detection (pure, offline)
# ---------------------------------------------------------------------------

def detect_ambiguities(
    data: Dict[str, Any],
    ontology: Ontology,
    *,
    source: str = "",
    workspace_id: str = "",
) -> List[AmbiguousEntity]:
    """Flag nodes in an *already-extracted* graph that are ambiguous w.r.t. the
    ontology. Pure walk — no LLM, no hot path.

    Signals: label not declared (OOV); admitted as bare ``Entity``; carries the
    open-mode ``_out_of_ontology`` stamp; surface form that is an alias of more
    than one declared class (alias collision)."""
    declared = set(ontology.nodes)
    # alias -> set of class labels (for collision detection)
    alias_owners: Dict[str, set] = {}
    for label, nd in ontology.nodes.items():
        for alias in (getattr(nd, "aliases", []) or []):
            alias_owners.setdefault(_norm(alias), set()).add(label)

    out: List[AmbiguousEntity] = []
    for node in data.get("nodes", []):
        if not isinstance(node, dict):
            continue
        label = str(node.get("label", "")).strip()
        props = node.get("properties", {}) or {}
        surface = str(props.get("name") or node.get("id") or label).strip()
        context = str(props.get("context") or props.get("description") or "")[:300]

        signal = None
        candidates: List[str] = []
        if str(props.get("_out_of_ontology", "")).lower() == "true":
            signal = SIGNAL_OUT_OF_ONTOLOGY
        elif label == "Entity":
            signal = SIGNAL_FALLBACK
        elif label and label not in declared:
            signal = SIGNAL_OOV
        else:
            owners = alias_owners.get(_norm(surface), set())
            if len(owners) > 1:
                signal = SIGNAL_ALIAS_COLLISION
                candidates = sorted(owners)

        if signal:
            out.append(AmbiguousEntity(
                surface=surface or label, label=label, signal=signal,
                candidate_labels=candidates, context=context,
                source=source, workspace_id=workspace_id,
            ))
    return out


# ---------------------------------------------------------------------------
# Quarantine store (JSONL, append-only)
# ---------------------------------------------------------------------------

class AmbiguityQuarantine:
    """Append-only JSONL store of quarantined mentions, kept separate from the
    clean graph (the whole point: never silently force-fit)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, items: List[AmbiguousEntity]) -> int:
        with self.path.open("a", encoding="utf-8") as fh:
            for it in items:
                fh.write(json.dumps(it.to_dict(), ensure_ascii=False) + "\n")
        return len(items)

    def all(self) -> List[AmbiguousEntity]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(AmbiguousEntity.from_dict(json.loads(line)))
                    except Exception:
                        continue
        return out

    def clusters(self) -> List[Dict[str, Any]]:
        """Group mentions by normalized surface; rank by frequency (the impact
        proxy a UI/LLM phase will refine with corpus_coverage gain)."""
        groups: Dict[str, Dict[str, Any]] = {}
        for it in self.all():
            key = _norm(it.surface)
            g = groups.setdefault(key, {
                "surface": it.surface, "frequency": 0, "signals": {},
                "candidate_labels": set(), "labels": set(), "examples": [],
            })
            g["frequency"] += 1
            g["signals"][it.signal] = g["signals"].get(it.signal, 0) + 1
            g["candidate_labels"].update(it.candidate_labels)
            g["labels"].add(it.label)
            if it.context and len(g["examples"]) < 3:
                g["examples"].append(it.context)
        clusters = []
        for g in groups.values():
            clusters.append({
                "surface": g["surface"], "frequency": g["frequency"],
                "signals": g["signals"], "labels": sorted(g["labels"]),
                "candidate_labels": sorted(g["candidate_labels"]), "examples": g["examples"],
            })
        clusters.sort(key=lambda c: -c["frequency"])
        return clusters


# ---------------------------------------------------------------------------
# Mapping spec (the declarative, git-friendly artifact)
# ---------------------------------------------------------------------------

# action: "alias" (add surface as alias of target class), "new_class" (add a new
# class `target` with broader `parent`), "same_as" (alias to an existing class),
# "ignore" (noise — leave in quarantine, no ontology change).
_VALID_ACTIONS = {"alias", "new_class", "same_as", "ignore"}


def starter_mapping_spec(clusters: List[Dict[str, Any]], ontology: Ontology) -> Dict[str, Any]:
    """A heuristic first-draft spec from quarantine clusters (no LLM): suggest
    `alias` when the surface already collides with declared aliases, else
    `new_class` for capitalized/typed-looking surfaces, else `ignore`. A human or
    a later LLM phase edits this before applying."""
    mappings = []
    for c in clusters:
        surface = c["surface"]
        if c["candidate_labels"]:
            action, entry = "alias", {"surface": surface, "action": "alias", "target": c["candidate_labels"][0]}
        elif re.match(r"^[A-Z][A-Za-z0-9 ]+$", surface) and len(surface) <= 40:
            label = re.sub(r"[^A-Za-z0-9]", "", surface.title())
            entry = {"surface": surface, "action": "new_class", "target": label, "parent": "", "description": ""}
        else:
            entry = {"surface": surface, "action": "ignore"}
        mappings.append(entry)
    return {"ontology": ontology.name, "mappings": mappings}


def apply_mapping_spec(ontology: Ontology, spec: Dict[str, Any]) -> Ontology:
    """Apply a mapping spec to produce a NEW draft ontology (minor version bump).
    Pure transform; the caller decides whether to snapshot/version it."""
    data = ontology.to_dict()
    nodes = data.setdefault("nodes", {})

    def _resolve_class(name: str) -> Optional[str]:
        if name in nodes:
            return name
        nl = _norm(name)
        for label in nodes:
            if _norm(label) == nl:
                return label
        return None

    for m in (spec.get("mappings") or []):
        action = str(m.get("action", "")).strip()
        if action not in _VALID_ACTIONS:
            raise ValueError(f"invalid mapping action: {m.get('action')!r}")
        surface = str(m.get("surface", "")).strip()
        if action == "ignore" or not surface:
            continue
        if action in ("alias", "same_as"):
            target = _resolve_class(str(m.get("target", "")))
            if not target:
                raise ValueError(f"alias/same_as target class not found: {m.get('target')!r}")
            aliases = nodes[target].setdefault("aliases", [])
            if surface not in aliases:
                aliases.append(surface)
        elif action == "new_class":
            label = str(m.get("target") or "").strip() or re.sub(r"[^A-Za-z0-9]", "", surface.title())
            if label not in nodes:
                nd: Dict[str, Any] = {"description": str(m.get("description", "") or ""), "properties": {}}
                parent = str(m.get("parent", "") or "").strip()
                if parent:
                    presolved = _resolve_class(parent)
                    if not presolved:
                        raise ValueError(f"new_class parent not found: {parent!r}")
                    nd["broader"] = [presolved]
                if _norm(surface) != _norm(label):
                    nd["aliases"] = [surface]
                nodes[label] = nd

    new_onto = Ontology.from_dict(data)
    new_onto.version = _bump_minor(ontology.version)
    return new_onto


def _bump_minor(version: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", str(version or "").strip())
    if not m:
        return version
    return f"{m.group(1)}.{int(m.group(2)) + 1}.0"


def load_mapping_spec(path: str | Path) -> Dict[str, Any]:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Phase 2 — LLM proposal engine (ADR-0128, seocho-2mg). Phase 1 gives a
# heuristic ``starter_mapping_spec``; this generates proposals with an LLM (via
# the provider-aware structured layer) and scores each by its predicted
# corpus-coverage lift, so the human reviews a ranked, consequence-annotated list.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MappingProposal:
    surface: str
    action: str                       # alias | new_class | same_as | ignore
    target: str = ""
    parent: str = ""
    description: str = ""
    confidence: float = 0.0
    predicted_coverage_delta: Optional[float] = None
    rationale: str = ""
    # OntoClean pre-validation of the proposed is-a edge (new_class under parent):
    # None = not checked (no tags), "ok" = passes, else a violation message.
    ontoclean: Optional[str] = None

    def to_spec_entry(self) -> Dict[str, Any]:
        entry: Dict[str, Any] = {"surface": self.surface, "action": self.action}
        if self.action in ("alias", "same_as", "new_class") and self.target:
            entry["target"] = self.target
        if self.action == "new_class":
            if self.parent:
                entry["parent"] = self.parent
            if self.description:
                entry["description"] = self.description
        return entry

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surface": self.surface, "action": self.action, "target": self.target,
            "parent": self.parent, "description": self.description,
            "confidence": self.confidence, "predicted_coverage_delta": self.predicted_coverage_delta,
            "rationale": self.rationale, "ontoclean": self.ontoclean,
        }


_PROPOSE_SYS = (
    "You are an ontology engineer triaging out-of-ontology entity mentions. For each surface form, "
    "choose how to map it into the ontology. Return ONLY JSON."
)


def _propose_prompt(clusters: List[Dict[str, Any]], ontology: Ontology) -> str:
    labels = ", ".join(ontology.nodes.keys()) or "(none)"
    lines = [f"EXISTING ONTOLOGY CLASSES: {labels}", "", "AMBIGUOUS SURFACE FORMS (with frequency / example context):"]
    for c in clusters:
        ex = (c.get("examples") or [""])[0][:160]
        lines.append(f"- '{c['surface']}' (x{c.get('frequency', 1)}; candidates={c.get('candidate_labels', [])}) e.g. {ex!r}")
    lines.append("")
    lines.append(
        'For each, choose an action: "alias" (a synonym of an existing class -> set "target" to that class), '
        '"new_class" (a genuinely new type -> set "target" to a PascalCase label, "parent" to an existing class '
        'or "", and a short "description"), or "ignore" (noise). '
        'Return JSON: {"proposals":[{"surface","action","target","parent","description","confidence","rationale"}]} '
        "with confidence in [0,1]."
    )
    return "\n".join(lines)


def _ontoclean_precheck(candidate: Ontology, prop: "MappingProposal", tags: Dict[str, Any]) -> Optional[str]:
    """Run the OntoClean subsumption check on the candidate ontology (which now
    contains the proposed ``new_class`` under its parent) and report the verdict
    for that edge. Returns None when the proposed class is untagged (can't check)."""
    from .ontology_ontoclean import check_ontoclean

    child_label = prop.target or prop.surface
    if child_label not in tags:
        return None  # no meta-properties for the proposed class → cannot judge
    result = check_ontoclean(candidate, tags)
    hits = [v for v in result.violations
            if v.severity == "violation" and v.child == child_label and v.parent == prop.parent]
    if hits:
        return f"violation: {hits[0].message}"
    return "ok"


def propose_mappings(
    clusters: List[Dict[str, Any]],
    ontology: Ontology,
    *,
    backend: Any,
    model: Optional[str] = None,
    top_k: int = 20,
    ontoclean_tags: Optional[Dict[str, Any]] = None,
) -> List[MappingProposal]:
    """Generate ranked mapping proposals for the top clusters via an LLM
    (injected ``backend``; routed through the provider-aware structured layer,
    ADR-0120). Each proposal is annotated with its predicted corpus-coverage lift
    (computed offline by applying it and re-scoring). When ``ontoclean_tags``
    (``{label: MetaProperties}``) are supplied, a ``new_class`` proposal's
    is-a placement under its parent is OntoClean-prechecked and the verdict is
    recorded on ``proposal.ontoclean``. Fake-testable."""
    from .llm_structured import StructuredOutputError, structured_complete

    top = list(clusters)[:top_k]
    if not top:
        return []
    try:
        payload = structured_complete(
            backend, system=_PROPOSE_SYS, user=_propose_prompt(top, ontology),
            model=model, task_hint="json_extraction",
        )
    except StructuredOutputError:
        return []
    raw = payload.get("proposals", []) if isinstance(payload, dict) else []

    # corpus profile from the clusters → measure each proposal's coverage lift
    from .ontology_scorecard import CorpusProfile, score_ontology

    profile = CorpusProfile(
        label_frequencies={str(c["surface"]): int(c.get("frequency", 1)) for c in top},
        source="ambiguity-clusters",
    )

    def _coverage(o: Ontology) -> float:
        dim = score_ontology(o, corpus_profile=profile, profile="guardrail").dimension("corpus_coverage")
        return dim.score if dim else 0.0

    base_cov = _coverage(ontology)
    proposals: List[MappingProposal] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip()
        if action not in _VALID_ACTIONS:
            continue
        prop = MappingProposal(
            surface=str(item.get("surface", "")).strip(),
            action=action,
            target=str(item.get("target", "")).strip(),
            parent=str(item.get("parent", "")).strip(),
            description=str(item.get("description", "")).strip(),
            confidence=float(item.get("confidence", 0.0) or 0.0),
            rationale=str(item.get("rationale", "")).strip(),
        )
        if action != "ignore":
            try:
                candidate = apply_mapping_spec(ontology, {"mappings": [prop.to_spec_entry()]})
                prop.predicted_coverage_delta = round(_coverage(candidate) - base_cov, 4)
                if action == "new_class" and prop.parent and ontoclean_tags:
                    prop.ontoclean = _ontoclean_precheck(candidate, prop, ontoclean_tags)
            except Exception:
                prop.predicted_coverage_delta = None
        proposals.append(prop)
    # rank: biggest predicted lift first, then confidence
    proposals.sort(key=lambda p: (-(p.predicted_coverage_delta or 0.0), -p.confidence))
    return proposals


def proposals_to_mapping_spec(
    proposals: List[MappingProposal],
    *,
    min_confidence: float = 0.0,
    ontology_name: str = "",
) -> Dict[str, Any]:
    """Convert accepted proposals (confidence >= threshold, non-ignore) into a
    mapping-spec consumable by :func:`apply_mapping_spec`."""
    mappings = [p.to_spec_entry() for p in proposals
                if p.action != "ignore" and p.confidence >= min_confidence]
    return {"ontology": ontology_name, "mappings": mappings}
