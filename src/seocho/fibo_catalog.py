"""Build guardrail-candidate Ontologies from a compiled FIBO catalog (ADR-0133).

ADR-0132 added a pinned FIBO submodule + an offline compiler emitting
`catalog.json` (schema `seocho.fibo_catalog.v1`): per-module label/definition/IRI
indexes. This module turns that authoritative, version-pinned catalog into SEOCHO
``Ontology`` objects usable as guardrail candidates by
``guardrail_selector.select_guardrail`` and ``ontology_scorecard.score_ontology``
— replacing the hand-made ``examples/datasets/fibo_{minus,base,plus}.jsonld``.

Pure/offline: consumes the compiled JSON (a dict or a path); never reads raw
OWL/RDF (ADR-0132 boundary). The FIBO commit + snapshot hash from the catalog flow
into ``Ontology.package_id``/``version`` so a guardrail choice is version-pinned
provenance (composes with ``OntologySnapshotStore``, ADR-0117).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .ontology import NodeDef, Ontology, RelDef

_CLASS_KINDS = {"class"}
_PROP_KINDS = {"object_property", "datatype_property"}


def load_catalog(data: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """Load a compiled FIBO catalog from a path or accept a dict. Validates the
    schema marker."""
    if isinstance(data, (str, Path)):
        data = json.loads(Path(data).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "modules" not in data:
        raise ValueError("not a FIBO catalog (missing 'modules')")
    return data


def _local_name(iri: str) -> str:
    text = str(iri or "").rstrip("/")
    frag = re.split(r"[#/]", text)[-1] if text else ""
    return frag


def _first_iri(value: Any) -> str:
    """Coerce a domain/range/subClassOf cell to a single IRI string. FIBO
    properties may carry a list of IRIs (multiple domains/ranges) or a string."""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value or "")


def _iri_list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [str(value)] if value else []


def _label_for(resource: Dict[str, Any], iri: str) -> str:
    """A valid SEOCHO class label: prefer the IRI local name (usually PascalCase),
    else a sanitized human label."""
    ln = str(resource.get("local_name") or _local_name(iri)).strip()
    if ln:
        return re.sub(r"[^A-Za-z0-9]", "", ln) or ln
    return re.sub(r"[^A-Za-z0-9]", "", str(resource.get("label", "")).title()) or "Entity"


def catalog_module_to_ontology(
    catalog: Dict[str, Any],
    module_code: str,
    *,
    name: Optional[str] = None,
) -> Ontology:
    """Build one ``Ontology`` from a catalog module: classes → nodes (human label
    as alias, definition, ``same_as`` = IRI, ``broader`` from subClassOf within
    the module); object properties → relationships (domain/range mapped to class
    labels)."""
    catalog = load_catalog(catalog)
    module = catalog["modules"].get(module_code)
    if module is None:
        raise KeyError(f"module '{module_code}' not in catalog")

    resources: Dict[str, Dict[str, Any]] = module.get("resources", {})
    definitions: Dict[str, str] = module.get("definitions", {})
    iri_to_label: Dict[str, str] = {iri: _label_for(r, iri) for iri, r in resources.items()}

    nodes: Dict[str, NodeDef] = {}
    for iri, r in resources.items():
        if r.get("kind") not in _CLASS_KINDS:
            continue
        label = iri_to_label[iri]
        human = str(r.get("label", "")).strip()
        aliases = [human] if human and human != label else []
        broader = [iri_to_label[p] for p in _iri_list(r.get("subclass_of")) if p in iri_to_label]
        nodes[label] = NodeDef(
            description=str(definitions.get(iri, "")).strip(),
            aliases=aliases, broader=sorted(set(broader)), same_as=iri,
        )

    relationships: Dict[str, RelDef] = {}
    for iri, r in resources.items():
        if r.get("kind") not in _PROP_KINDS:
            continue
        rtype = re.sub(r"[^A-Za-z0-9]", "_", iri_to_label[iri]).upper() or "RELATED_TO"
        src = iri_to_label.get(_first_iri(r.get("domain")), "Any")
        tgt = iri_to_label.get(_first_iri(r.get("range")), "Any")
        relationships[rtype] = RelDef(source=src or "Any", target=tgt or "Any",
                                      description=str(definitions.get(iri, "")).strip())

    onto = Ontology(
        name or f"fibo-{module_code}",
        package_id=f"fibo.{module_code}",
        version=str(catalog.get("fibo_commit", "0"))[:12] or "0.0.0",
        description=str(module.get("summary", "")).strip(),
        nodes=nodes, relationships=relationships,
    )
    return onto


def fibo_guardrail_candidates(
    catalog: Union[str, Path, Dict[str, Any]],
    *,
    modules: Optional[List[str]] = None,
) -> Dict[str, Ontology]:
    """Build a ``{module_code: Ontology}`` map of guardrail candidates from a
    compiled FIBO catalog — feed directly to ``guardrail_selector.select_guardrail``
    (e.g. select the FIBO module whose vocabulary best covers a corpus)."""
    catalog = load_catalog(catalog)
    codes = modules if modules is not None else list(catalog["modules"].keys())
    return {code: catalog_module_to_ontology(catalog, code) for code in codes if code in catalog["modules"]}


def catalog_provenance(catalog: Union[str, Path, Dict[str, Any]]) -> Dict[str, str]:
    """The version-pinning provenance to attach to a snapshot / run."""
    catalog = load_catalog(catalog)
    return {
        "schema_version": str(catalog.get("schema_version", "")),
        "fibo_commit": str(catalog.get("fibo_commit", "")),
        "snapshot_hash": str(catalog.get("snapshot_hash", "")),
    }


# ---------------------------------------------------------------------------
# Alias-bridging (ADR-0135): official FIBO's fine-grained labels (JointStockCompany)
# don't match the LLM's generic extraction vocabulary (Company) → corpus_coverage
# ~0 (ADR-0134). Bridge by adding each generic term as an alias to FIBO classes
# whose label lexically contains it, so coverage can match. Offline/deterministic.
# ---------------------------------------------------------------------------


def _tokens(s: str) -> frozenset:
    """Normalized word tokens of a label: split camelCase + non-alnum, lowercased.
    'JointStockCompany' → {joint, stock, company}; 'Legal Entity' → {legal, entity}."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(s))
    return frozenset(t.lower() for t in re.split(r"[^A-Za-z0-9]+", spaced) if len(t) >= 3)


def alias_bridge(ontology: Ontology, terms: List[str], *, min_len: int = 4) -> Ontology:
    """Return a NEW ontology where each generic ``term`` is added as an alias to
    every class whose label/alias token-set CONTAINS the term's token-set, e.g.
    ``Company`` → alias of ``JointStockCompany``/``PubliclyHeldCompany``;
    ``FinancialMetric`` matches a class with both tokens. Token-subset matching
    (not raw substring) avoids spurious hits (``Date`` ⊄ ``Candidate``)."""
    data = ontology.to_dict()
    nodes: Dict[str, Any] = data.get("nodes", {})
    norm_terms = [(term, _tokens(term)) for term in terms
                  if len(re.sub(r"[^A-Za-z0-9]", "", str(term))) >= min_len]
    for label, nd in nodes.items():
        label_tokens = [_tokens(label)] + [_tokens(a) for a in (nd.get("aliases") or [])]
        for term, tt in norm_terms:
            if not tt:
                continue
            if any(tt <= lt for lt in label_tokens):
                aliases = nd.setdefault("aliases", [])
                if term not in aliases:
                    aliases.append(term)
    return Ontology.from_dict(data)


def bridge_to_corpus(ontology: Ontology, corpus_profile: Any, *, min_len: int = 4) -> Ontology:
    """Alias-bridge an ontology to a corpus's observed labels (the LLM's
    extraction vocabulary) — the automatic form of :func:`alias_bridge`."""
    terms = list(getattr(corpus_profile, "label_frequencies", {}).keys())
    return alias_bridge(ontology, terms, min_len=min_len)


# ---------------------------------------------------------------------------
# Semantic bridge (ADR-0136): lexical token-bridge (ADR-0135) can't reach FIBO's
# non-obvious roots — FIBO's company concept is `LegalEntity`, not lexically
# "Company". Seed generic term → FIBO root class(es) and propagate the alias DOWN
# the subClassOf hierarchy (a subclass of a Company IS a Company), so a doc's
# `Bank`/`Corporation` (under the root) matches the generic `Company`.
# ---------------------------------------------------------------------------

# A small generic→FIBO-root seed for the FinDER financial domain (roots verified
# present in the compiled BE/FBC/FND/SEC modules). Only roots that exist take effect.
FINDER_FIBO_ROOTS: Dict[str, List[str]] = {
    "Company": ["LegalEntity", "BusinessEntity", "FormalOrganization",
                "FinancialServiceProvider", "FinancialInstitution"],
    "Person": ["Person", "ResponsibleParty", "AutonomousAgent"],
    "FinancialMetric": ["Security", "Share", "DebtInstrument", "MonetaryAmount", "FinancialInstrument"],
    "Regulation": ["LegalConstruct", "Agreement", "ContractualElement"],
    "Exchange": ["Exchange"],
}


def _descendants(children: Dict[str, List[str]], root: str) -> set:
    from collections import deque
    seen: set = set()
    q = deque([root])
    while q:
        x = q.popleft()
        for c in children.get(x, []):
            if c not in seen:
                seen.add(c)
                q.append(c)
    return seen


def semantic_bridge(ontology: Ontology, root_aliases: Dict[str, List[str]], *, include_self: bool = True) -> Ontology:
    """Return a NEW ontology where each generic term is added as an alias to its
    seeded FIBO root class(es) AND all their subClassOf descendants — propagating
    the generic label down the is-a hierarchy. Non-lexical: bridges roots whose
    own label doesn't contain the term (`LegalEntity` ← "Company")."""
    data = ontology.to_dict()
    nodes: Dict[str, Any] = data.get("nodes", {})
    children: Dict[str, List[str]] = {}
    for lbl, nd in nodes.items():
        for p in (nd.get("broader") or []):
            children.setdefault(p, []).append(lbl)
    for term, roots in root_aliases.items():
        targets: set = set()
        for root in roots:
            if root in nodes:
                if include_self:
                    targets.add(root)
                targets |= _descendants(children, root)
        for t in targets:
            aliases = nodes[t].setdefault("aliases", [])
            if term not in aliases:
                aliases.append(term)
    return Ontology.from_dict(data)


# ---------------------------------------------------------------------------
# Auto-derived seed (ADR-0139): replace the hand-written FINDER_FIBO_ROOTS with
# an LLM mapping generic corpus term → FIBO root class(es). Candidate roots are
# the classes with the most subClassOf descendants (the meaningful propagation
# anchors); the LLM picks which roots each generic term subsumes. Injected
# backend (fake-testable); the result feeds semantic_bridge.
# ---------------------------------------------------------------------------


def root_candidates(ontology: Ontology, *, top: int = 30) -> List[Dict[str, str]]:
    """The most root-like classes (highest subClassOf-descendant count) with their
    definitions — the anchors worth seeding a generic alias onto."""
    children: Dict[str, List[str]] = {}
    for lbl, nd in ontology.nodes.items():
        for p in (getattr(nd, "broader", []) or []):
            children.setdefault(p, []).append(lbl)
    scored = sorted(((len(_descendants(children, lbl)), lbl) for lbl in ontology.nodes), reverse=True)
    out = []
    for n_desc, lbl in scored[:top]:
        if n_desc == 0:
            continue
        out.append({"label": lbl, "descendants": str(n_desc),
                    "definition": str(getattr(ontology.nodes[lbl], "description", "") or "")[:160]})
    return out


_ROOTS_SYS = (
    "You are an ontology engineer aligning a generic extraction vocabulary to FIBO. "
    "For each generic term, pick the FIBO root class(es) it SUBSUMES — i.e. instances of "
    "that FIBO class (and its subclasses) are examples of the generic term. Return ONLY JSON."
)


def _roots_prompt(generic_terms: List[str], candidates: List[Dict[str, str]]) -> str:
    lines = ["GENERIC TERMS: " + ", ".join(generic_terms), "", "FIBO ROOT CANDIDATES (label — #subclasses — definition):"]
    for c in candidates:
        lines.append(f"- {c['label']} — {c['descendants']} — {c['definition']}")
    lines.append("")
    lines.append('Map each generic term to 0+ FIBO root labels from the list above (only labels that '
                 'genuinely subsume the term). Return JSON: {"roots": {"<GenericTerm>": ["<FiboLabel>", ...]}}')
    return "\n".join(lines)


def derive_fibo_roots(
    generic_terms: List[str],
    ontology: Ontology,
    *,
    backend: Any,
    model: Optional[str] = None,
    top_candidates: int = 30,
) -> Dict[str, List[str]]:
    """LLM-derive a ``{generic_term: [FIBO root labels]}`` seed (the automated
    replacement for the hand-written FINDER_FIBO_ROOTS). Injected ``backend`` via
    the provider-aware structured layer; roots are validated to exist in the
    ontology. Fake-testable."""
    from .llm_structured import StructuredOutputError, structured_complete

    candidates = root_candidates(ontology, top=top_candidates)
    if not candidates:
        return {}
    try:
        payload = structured_complete(
            backend, system=_ROOTS_SYS, user=_roots_prompt(generic_terms, candidates),
            model=model, task_hint="json_extraction",
        )
    except StructuredOutputError:
        return {}
    raw = payload.get("roots", payload) if isinstance(payload, dict) else {}
    seed: Dict[str, List[str]] = {}
    for term, roots in (raw.items() if isinstance(raw, dict) else []):
        valid = [str(r) for r in (roots or []) if str(r) in ontology.nodes]
        if valid:
            seed[str(term)] = valid
    return seed


def auto_semantic_bridge(
    ontology: Ontology,
    generic_terms: List[str],
    *,
    backend: Any,
    model: Optional[str] = None,
) -> Ontology:
    """Derive the generic→root seed with the LLM, then semantic-bridge — the
    fully-automated form of the ADR-0136 pipeline (no hand seed)."""
    seed = derive_fibo_roots(generic_terms, ontology, backend=backend, model=model)
    return semantic_bridge(ontology, seed)
