"""WP-O offline pipeline: SHACL shapes as source code (ADR pending, design v0.3 §WP-O).

The ontology is the cache layer's address space and invalidation boundary, so
it is treated like source: canonical formatting so diffs mean something, a
lockfile so derived artifacts are pinned, and a composite ``active_hash`` so
"the ontology changed" is one comparison everywhere (etcd, KV validity, CI).

Everything here is offline governance — rdflib only, imported lazily, never
on a hot request path (CLAUDE.md runtime guardrails). SHACL is the single
source (O.10): vocabulary, path index and address space all derive from
``shapes/*.ttl``; OWL/RDFS reasoning stays out of v1.

Hash provenance: ``source_hash`` is blake2b over rdflib's canonical graph
(isomorphism-invariant bnode labeling) serialized as sorted N-Triples, tagged
``rdfcanon-rdflib1:`` — deliberately NOT claiming RDFC-1.0; upgrading the
algorithm changes the tag, which changes ``active_hash``, which is exactly
the invalidation the lockfile design demands (§O.4).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PIPELINE_VERSION = "seocho-ont/0.1.0"
CANON_TAG = "rdfcanon-rdflib1"

SH = "http://www.w3.org/ns/shacl#"


def _require_rdflib():
    try:
        import rdflib
        from rdflib import compare
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "seocho ont requires rdflib. Install it with: pip install 'seocho[ontology]'"
        ) from exc
    return rdflib, compare


# ----------------------------------------------------------------------------
# Canonical form. fmt exists for the same reason as WP2's KV canonicalization:
# equal meaning must be equal bytes — there for diffability, here for hashes.
# ----------------------------------------------------------------------------

def load_graph(path: Path):
    rdflib, _ = _require_rdflib()
    graph = rdflib.Graph()
    graph.parse(path, format="turtle")
    return graph


def canonical_ntriples(graph) -> str:
    """Isomorphism-stable, deterministic N-Triples for hashing."""
    _, compare = _require_rdflib()
    canonical = compare.to_canonical_graph(graph)
    lines = sorted(
        f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in canonical
    )
    return "\n".join(lines) + "\n"


def canonical_turtle(graph) -> str:
    """Deterministic, human-readable Turtle: sorted prefixes, sorted subject
    blocks, sorted predicates within a subject. Blank nodes take canonical
    labels — intrusive on first format, stable forever after.
    """
    rdflib, compare = _require_rdflib()
    canonical = compare.to_canonical_graph(graph)

    namespaces = sorted(
        (prefix, str(ns)) for prefix, ns in graph.namespaces() if prefix
    )

    def compact(term) -> str:
        if isinstance(term, rdflib.URIRef):
            text = str(term)
            for prefix, ns in namespaces:
                if text.startswith(ns) and len(text) > len(ns):
                    local = text[len(ns):]
                    if local and all(c.isalnum() or c in "_-." for c in local):
                        return f"{prefix}:{local}"
            return f"<{text}>"
        return term.n3()

    triples = sorted(
        (compact(s), compact(p), compact(o)) for s, p, o in canonical
    )
    lines = [f"@prefix {prefix}: <{ns}> ." for prefix, ns in namespaces]
    lines.append("")
    current: Optional[str] = None
    for subject, predicate, obj in triples:
        if subject != current:
            if current is not None:
                lines[-1] = lines[-1][:-2] + " ."
                lines.append("")
            lines.append(f"{subject} {predicate} {obj} ;")
            current = subject
        else:
            lines.append(f"    {predicate} {obj} ;")
    if current is not None:
        lines[-1] = lines[-1][:-2] + " ."
    return "\n".join(lines) + "\n"


def source_hash(graph) -> str:
    digest = hashlib.blake2b(canonical_ntriples(graph).encode(), digest_size=16)
    return f"{CANON_TAG}:{digest.hexdigest()}"


# ----------------------------------------------------------------------------
# Derivation: vocabulary, path index, address space — all from sh:targetClass
# and sh:property/sh:path (O.10: shapeless classes have no address).
# ----------------------------------------------------------------------------

@dataclass
class DerivedArtifacts:
    classes: List[str]
    relationships: List[str]
    path_index: Dict[str, Dict[str, str]]   # class -> {relationship -> target class}
    properties: Dict[str, List[str]]        # class -> declared datatype paths
    address_space: List[str]                # "class:<curie>" entries
    unaddressed_note: str = (
        "classes without a shape carry no address and join the anonymous pool"
    )

    def to_payloads(self) -> Dict[str, str]:
        return {
            "vocab.json": json.dumps(
                {"classes": self.classes, "relationships": self.relationships},
                indent=2, sort_keys=True) + "\n",
            "path_index.json": json.dumps(self.path_index, indent=2, sort_keys=True) + "\n",
            "properties.json": json.dumps(self.properties, indent=2, sort_keys=True) + "\n",
            "address_space.json": json.dumps(
                {"addresses": self.address_space, "source": "sh:targetClass",
                 "note": self.unaddressed_note}, indent=2, sort_keys=True) + "\n",
        }


def _curie(graph, term) -> str:
    try:
        return graph.namespace_manager.normalizeUri(term)
    except Exception:
        return str(term)


def derive(graph) -> DerivedArtifacts:
    rdflib, _ = _require_rdflib()
    sh = rdflib.Namespace(SH)
    rdf_type = rdflib.RDF.type

    classes: List[str] = []
    path_index: Dict[str, Dict[str, str]] = {}
    properties: Dict[str, List[str]] = {}
    relationships: set = set()

    for shape in graph.subjects(rdf_type, sh.NodeShape):
        for target in graph.objects(shape, sh.targetClass):
            cls = _curie(graph, target)
            classes.append(cls)
            path_index.setdefault(cls, {})
            properties.setdefault(cls, [])
            for prop_shape in graph.objects(shape, sh.property):
                path = next(graph.objects(prop_shape, sh.path), None)
                if path is None:
                    continue
                target_class = next(graph.objects(prop_shape, sh["class"]), None)
                path_name = _curie(graph, path)
                if target_class is not None:
                    path_index[cls][path_name] = _curie(graph, target_class)
                    relationships.add(path_name)
                else:
                    properties[cls].append(path_name)

    classes = sorted(set(classes))
    for cls in properties:
        properties[cls] = sorted(set(properties[cls]))
    return DerivedArtifacts(
        classes=classes,
        relationships=sorted(relationships),
        path_index={c: dict(sorted(v.items())) for c, v in sorted(path_index.items())},
        properties=dict(sorted(properties.items())),
        address_space=[f"class:{c}" for c in classes],
    )


# ----------------------------------------------------------------------------
# Lockfile. active_hash covers the WHOLE lockfile — shapes, every derived
# artifact, and the tool version — because a compiler bump with identical
# shapes still invalidates derived state (§O.4).
# ----------------------------------------------------------------------------

def _sha256(payload: str) -> str:
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class BuildResult:
    lock: Dict[str, Any]
    artifacts: Dict[str, str] = field(default_factory=dict)

    @property
    def active_hash(self) -> str:
        return self.lock["active_hash"]


def shape_files(shapes_dir: Path) -> List[Path]:
    return sorted(shapes_dir.rglob("*.ttl"))


def build(shapes_dir: Path) -> BuildResult:
    rdflib, _ = _require_rdflib()
    files = shape_files(shapes_dir)
    if not files:
        raise FileNotFoundError(f"no .ttl shapes under {shapes_dir}")
    merged = rdflib.Graph()
    per_file: Dict[str, str] = {}
    for path in files:
        graph = load_graph(path)
        per_file[path.relative_to(shapes_dir).as_posix()] = source_hash(graph)
        for triple in graph:
            merged.add(triple)
        for prefix, ns in graph.namespaces():
            merged.namespace_manager.bind(prefix, ns, replace=False)

    artifacts = derive(merged).to_payloads()
    lock: Dict[str, Any] = {
        "shapes": {"source_hash": source_hash(merged), "files": per_file},
        "derived": {name: _sha256(payload) for name, payload in sorted(artifacts.items())},
        "tool": {"pipeline": PIPELINE_VERSION, "canonicalization": CANON_TAG},
    }
    lock["active_hash"] = _sha256(json.dumps(lock, sort_keys=True))
    return BuildResult(lock=lock, artifacts=artifacts)


def write_build(result: BuildResult, out_dir: Path, lock_path: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in result.artifacts.items():
        (out_dir / name).write_text(payload, encoding="utf-8")
    lock_path.write_text(
        json.dumps(result.lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify(shapes_dir: Path, lock_path: Path) -> Tuple[bool, List[str]]:
    recorded = json.loads(lock_path.read_text())
    current = build(shapes_dir).lock
    problems: List[str] = []
    # Internal integrity first: the stored active_hash must equal the hash of
    # the stored body. Without this, editing any field while keeping the old
    # active_hash silently verifies — the exact quiet-stale failure §O.4 exists
    # to prevent.
    body = {k: v for k, v in recorded.items() if k != "active_hash"}
    if _sha256(json.dumps(body, sort_keys=True)) != recorded.get("active_hash"):
        problems.append("lockfile internally inconsistent: body does not match "
                        "its active_hash (hand-edited?)")
    if recorded.get("active_hash") != current["active_hash"]:
        if recorded.get("shapes", {}).get("source_hash") != current["shapes"]["source_hash"]:
            problems.append("shapes changed since lock")
        for name, digest in current["derived"].items():
            if recorded.get("derived", {}).get(name) != digest:
                problems.append(f"derived artifact drifted: {name}")
        if recorded.get("tool") != current["tool"]:
            problems.append("pipeline tool version changed")
        if not problems:
            problems.append("active_hash mismatch")
    return (not problems, problems)


# ----------------------------------------------------------------------------
# Blast radius v1: which addresses and derived artifacts a proposed shapes
# change touches. KV-block-level radius needs the live translation table
# (WP6.1a); until then the address delta is the honest bound.
# ----------------------------------------------------------------------------

def blast_radius(current_dir: Path, proposed_dir: Path) -> Dict[str, Any]:
    before = build(current_dir)
    after = build(proposed_dir)
    before_derived = derive_from_lockfree(current_dir)
    after_derived = derive_from_lockfree(proposed_dir)

    before_addresses = set(before_derived.address_space)
    after_addresses = set(after_derived.address_space)
    changed: List[str] = []
    for address in sorted(before_addresses & after_addresses):
        cls = address[len("class:"):]
        if (before_derived.path_index.get(cls) != after_derived.path_index.get(cls)
                or before_derived.properties.get(cls) != after_derived.properties.get(cls)):
            changed.append(address)

    artifacts_changed = sorted(
        name for name in before.lock["derived"]
        if before.lock["derived"][name] != after.lock["derived"].get(name)
    )
    total = max(len(before_addresses | after_addresses), 1)
    touched = (before_addresses ^ after_addresses) | set(changed)
    return {
        "active_hash_before": before.active_hash,
        "active_hash_after": after.active_hash,
        "addresses_added": sorted(after_addresses - before_addresses),
        "addresses_removed": sorted(before_addresses - after_addresses),
        "addresses_changed": changed,
        "artifacts_changed": artifacts_changed,
        "address_space_share_touched": round(len(touched) / total, 4),
        "note": "KV-block radius requires the live xlat table (WP6.1a); "
                "this is the address-level bound",
    }


def derive_from_lockfree(shapes_dir: Path) -> DerivedArtifacts:
    rdflib, _ = _require_rdflib()
    merged = rdflib.Graph()
    for path in shape_files(shapes_dir):
        graph = load_graph(path)
        for triple in graph:
            merged.add(triple)
        for prefix, ns in graph.namespaces():
            merged.namespace_manager.bind(prefix, ns, replace=False)
    return derive(merged)


# ----------------------------------------------------------------------------
# Lint v1: the checks that make shapes usable as the single source.
# ----------------------------------------------------------------------------

def lint(shapes_dir: Path) -> List[str]:
    rdflib, _ = _require_rdflib()
    sh = rdflib.Namespace(SH)
    findings: List[str] = []
    for path in shape_files(shapes_dir):
        graph = load_graph(path)
        rel = path.relative_to(shapes_dir).as_posix()
        shapes = list(graph.subjects(rdflib.RDF.type, sh.NodeShape))
        if not shapes:
            findings.append(f"WARN {rel}: no sh:NodeShape declared")
        for shape in shapes:
            targets = list(graph.objects(shape, sh.targetClass))
            if not targets:
                findings.append(
                    f"WARN {rel}: {_curie(graph, shape)} has no sh:targetClass — "
                    f"it derives no address (O.10: shapeless classes join the "
                    f"anonymous pool)")
            for prop_shape in graph.objects(shape, sh.property):
                if next(graph.objects(prop_shape, sh.path), None) is None:
                    findings.append(
                        f"ERROR {rel}: property shape under {_curie(graph, shape)} "
                        f"lacks sh:path")
        formatted = canonical_turtle(graph)
        if path.read_text(encoding="utf-8") != formatted:
            findings.append(f"WARN {rel}: not in canonical form (run: seocho ont fmt)")
    return findings
