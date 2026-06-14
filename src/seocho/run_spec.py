"""Run spec: the YAML contract behind ``seocho run``.

A run spec declares one end-to-end run — which documents to index, which
ontology to bind, which models drive each phase, and which questions to
ask — without writing Python. This module is the pure spec layer: parsing,
env interpolation, and validation only. No store, LLM, or graph imports.

Design vocabulary stays in :mod:`seocho.agent_design` and
:mod:`seocho.indexing_design`; a run spec references or embeds those
documents instead of redefining their keys.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

DEFAULT_RUN_SPEC_FILENAME = "seocho.run.yaml"
DEFAULT_MODEL = "mara/MiniMax-M2.5"

_ALLOWED_ENFORCEMENT_MODES = {"strict", "guided", "open"}
_ALLOWED_EXECUTION_MODES = {"pipeline", "agent", "supervisor"}
_ALLOWED_ROUTING_POLICIES = {"fast", "balanced", "thorough"}
_ALLOWED_ANSWER_STYLES = {"concise", "evidence", "table"}
_ALLOWED_GRAPH_KINDS = {"neo4j", "dozerdb", "ladybug"}
_ALLOWED_VECTOR_KINDS = {"faiss", "lancedb"}
_BOLT_SCHEMES = ("bolt://", "neo4j://", "neo4j+s://", "bolt+s://")

_TOP_LEVEL_KEYS = {
    "name",
    "description",
    "ontology",
    "documents",
    "models",
    "graph",
    "graph_user",
    "graph_password",
    "database",
    "workspace_id",
    "indexing",
    "agent",
    "query",
    "vector",
    "questions",
    "output",
}
_SECTION_KEYS: Dict[str, set] = {
    "ontology": {"path", "enforcement", "select"},
    "documents": {"path", "recursive"},
    "models": {"default", "indexing", "query"},
    "graph": {"kind", "uri", "path", "user", "password", "database"},
    "indexing": {"design", "category", "force"},
    "agent": {"design", "execution_mode", "routing_policy"},
    "query": {"reasoning_mode", "repair_budget", "answer_style", "limit"},
    "vector": {"kind", "embedding", "embedding_model", "dimension", "uri", "table_name"},
    "output": {"dir"},
}

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class RunSpecError(ValueError):
    """Raised when a run spec fails to parse or validate.

    ``errors`` keeps the individual messages so callers (the CLI) can
    print one error per line.
    """

    def __init__(self, errors: List[str]) -> None:
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


def _interpolate_env(value: Any, *, errors: List[str], where: str) -> Any:
    """Resolve ``${VAR}`` / ``${VAR:-default}`` in string values, recursively."""
    if isinstance(value, str):
        def _resolve(match: "re.Match[str]") -> str:
            name, default = match.group(1), match.group(2)
            resolved = os.environ.get(name)
            if resolved is not None:
                return resolved
            if default is not None:
                return default
            errors.append(
                f"at {where}: environment variable {name} is not set. "
                f"Export it or use ${{{name}:-fallback}}."
            )
            return ""
        return _ENV_PATTERN.sub(_resolve, value)
    if isinstance(value, dict):
        return {
            key: _interpolate_env(item, errors=errors, where=f"{where}.{key}" if where else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _interpolate_env(item, errors=errors, where=f"{where}[{idx}]")
            for idx, item in enumerate(value)
        ]
    return value


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _suggest(key: str, allowed: set) -> str:
    matches = difflib.get_close_matches(key, sorted(allowed), n=1)
    return f" Did you mean '{matches[0]}'?" if matches else ""


def _check_unknown_keys(
    payload: Mapping[str, Any],
    *,
    allowed: set,
    where: str,
    errors: List[str],
) -> None:
    for key in payload:
        if key not in allowed:
            errors.append(f"at {where}: unknown key '{key}'.{_suggest(str(key), allowed)}")


def _section(
    payload: Mapping[str, Any],
    key: str,
    *,
    errors: List[str],
) -> Dict[str, Any]:
    """Return a section as a dict, validating its keys. Accepts absent sections."""
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        errors.append(f"at {key}: must be a mapping.")
        return {}
    _check_unknown_keys(value, allowed=_SECTION_KEYS[key], where=key, errors=errors)
    return dict(value)


def parse_model_ref(value: str, *, where: str, errors: List[str]) -> Tuple[str, str]:
    """Parse ``provider/model`` into a (provider, model) pair."""
    text = _string(value)
    if "/" not in text:
        errors.append(
            f"at {where}: model must be 'provider/model' (e.g. 'mara/MiniMax-M2.5'), got {text!r}."
        )
        return ("", text)
    provider, model = text.split("/", 1)
    provider, model = provider.strip().lower(), model.strip()
    if not provider or not model:
        errors.append(f"at {where}: model must be 'provider/model', got {text!r}.")
    return (provider, model)


@dataclass(slots=True)
class QuestionSpec:
    """One query-phase question; ``expect`` is recorded, never auto-graded."""

    question: str
    expect: str = ""
    question_id: str = ""


@dataclass(slots=True)
class RunSpec:
    """Validated, env-resolved run configuration for ``seocho run``."""

    name: str
    ontology_path: str
    documents_path: str
    enforcement: str = "guided"
    # True when the YAML declared ontology.enforcement explicitly — an
    # explicit value overrides an agent design's enforcement; the default
    # never does.
    enforcement_set: bool = False
    description: str = ""
    documents_recursive: bool = True
    models: Dict[str, str] = field(default_factory=dict)
    graph: str = ""
    # Optional explicit backend kind (neo4j | dozerdb | ladybug). Empty means
    # infer from the graph value: bolt-scheme URI → Neo4j/DozerDB, anything
    # else (or blank) → embedded LadybugDB path.
    graph_kind: str = ""
    graph_user: str = "neo4j"
    graph_password: str = "password"
    database: str = ""
    workspace_id: str = ""
    indexing: Dict[str, Any] = field(default_factory=dict)
    agent: Dict[str, Any] = field(default_factory=dict)
    query: Dict[str, Any] = field(default_factory=dict)
    # Optional hybrid-search vector store: {kind, embedding, embedding_model,
    # dimension, uri, table_name}. Absent section → no vector store.
    vector: Dict[str, Any] = field(default_factory=dict)
    questions: List[QuestionSpec] = field(default_factory=list)
    output_dir: str = "runs"
    source_path: str = ""
    # Optional domain-adaptive guardrail selection (ADR-0123): when
    # ``ontology.select`` is declared instead of a fixed ``ontology.path``, the
    # runner scores the candidates against the corpus profile and picks one.
    # ``ontology_path`` is then filled at resolve time and ``selected_guardrail``
    # records the recommendation.
    guardrail_candidates: Dict[str, str] = field(default_factory=dict)
    guardrail_corpus_profile: str = ""
    selected_guardrail: Optional[Dict[str, Any]] = None

    # -- model resolution ------------------------------------------------

    def default_model(self) -> str:
        return _string(self.models.get("default")) or DEFAULT_MODEL

    def indexing_model(self) -> str:
        return _string(self.models.get("indexing")) or self.default_model()

    def query_model(self) -> str:
        return _string(self.models.get("query")) or self.default_model()

    def uses_split_models(self) -> bool:
        return self.indexing_model() != self.query_model()

    # -- enforcement mapping ----------------------------------------------

    def strict_validation(self) -> bool:
        """``strict`` rejects chunks with validation errors. The full
        admission policy (prompt line, no relaxed retry/Entity fallback,
        closed validation) is compiled by
        :class:`seocho.index.enforcement.EnforcementPolicy` from
        ``AgentConfig.ontology_enforcement``."""
        return self.enforcement == "strict"

    def resolved_workspace_id(self) -> str:
        return self.workspace_id or re.sub(r"[^a-z0-9_]", "_", self.name.lower())

    def index_only(self) -> bool:
        return not self.questions

    # -- backend resolution -------------------------------------------------

    def resolved_graph_kind(self) -> str:
        if self.graph_kind:
            return self.graph_kind
        if self.graph and self.graph.startswith(_BOLT_SCHEMES):
            return "neo4j"
        return "ladybug"

    def uses_vector_store(self) -> bool:
        return bool(self.vector)

    def vector_kind(self) -> str:
        return _string(self.vector.get("kind")).lower()

    def vector_embedding(self) -> str:
        """Embedding source for the vector store. Default ``fastembed``
        (local bge, no network) per the MARA-first policy; any other value
        is treated as an LLM provider preset name."""
        return _string(self.vector.get("embedding")).lower() or "fastembed"


def _parse_questions(value: Any, *, errors: List[str]) -> List[QuestionSpec]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append("at questions: must be a list of strings or mappings.")
        return []
    questions: List[QuestionSpec] = []
    for idx, item in enumerate(value):
        where = f"questions[{idx}]"
        if isinstance(item, str):
            if item.strip():
                questions.append(QuestionSpec(question=item.strip()))
            continue
        if isinstance(item, Mapping):
            _check_unknown_keys(
                item, allowed={"question", "expect", "id"}, where=where, errors=errors
            )
            text = _string(item.get("question"))
            if not text:
                errors.append(f"at {where}: mapping form requires a 'question' key.")
                continue
            questions.append(
                QuestionSpec(
                    question=text,
                    expect=_string(item.get("expect")),
                    question_id=_string(item.get("id")),
                )
            )
            continue
        errors.append(f"at {where}: must be a string or a mapping with 'question'.")
    return questions


def _path_or_mapping(
    payload: Mapping[str, Any],
    key: str,
    *,
    errors: List[str],
) -> Dict[str, Any]:
    """``ontology`` / ``documents`` accept a bare path string or a mapping."""
    value = payload.get(key)
    if isinstance(value, str):
        return {"path": value.strip()}
    return _section(payload, key, errors=errors)


def parse_run_spec(payload: Any, *, source_path: str = "") -> RunSpec:
    """Parse and validate a run spec payload. Raises :class:`RunSpecError`."""
    errors: List[str] = []
    if not isinstance(payload, Mapping):
        raise RunSpecError(["run spec must be a YAML mapping."])

    payload = _interpolate_env(dict(payload), errors=errors, where="")
    _check_unknown_keys(payload, allowed=_TOP_LEVEL_KEYS, where="top level", errors=errors)

    ontology = _path_or_mapping(payload, "ontology", errors=errors)
    documents = _path_or_mapping(payload, "documents", errors=errors)

    # Optional domain-adaptive guardrail selection (ADR-0123).
    select = ontology.get("select")
    guardrail_candidates: Dict[str, str] = {}
    guardrail_corpus_profile = ""
    if select is not None:
        if not isinstance(select, Mapping):
            errors.append("at ontology.select: must be a mapping with 'candidates' and 'corpus_profile'.")
        else:
            cands = select.get("candidates") or {}
            if not isinstance(cands, Mapping) or not cands:
                errors.append("at ontology.select.candidates: a non-empty mapping of name -> ontology path is required.")
            else:
                guardrail_candidates = {str(k): _string(v) for k, v in cands.items()}
            guardrail_corpus_profile = _string(select.get("corpus_profile"))
            if not guardrail_corpus_profile:
                errors.append("at ontology.select.corpus_profile: a corpus-profile path is required.")

    models_value = payload.get("models")
    if isinstance(models_value, str):
        models: Dict[str, str] = {"default": models_value.strip()}
    else:
        models = {k: _string(v) for k, v in _section(payload, "models", errors=errors).items()}

    indexing = _section(payload, "indexing", errors=errors)
    agent = _section(payload, "agent", errors=errors)
    query = _section(payload, "query", errors=errors)
    vector = _section(payload, "vector", errors=errors)
    output = _section(payload, "output", errors=errors)

    # ``graph`` accepts a bare string (bolt URI or ladybug path — inferred)
    # or a mapping with an explicit backend kind. The mapping form
    # normalizes into the flat fields so everything downstream is unchanged.
    graph_value = payload.get("graph")
    graph_kind = ""
    if isinstance(graph_value, Mapping):
        graph_section = _section(payload, "graph", errors=errors)
        graph_kind = _string(graph_section.get("kind")).lower()
        graph_target = _string(graph_section.get("uri")) or _string(graph_section.get("path"))
        graph_user = _string(graph_section.get("user"))
        graph_password = _string(graph_section.get("password"))
        graph_database = _string(graph_section.get("database"))
        if _string(graph_section.get("uri")) and _string(graph_section.get("path")):
            errors.append("at graph: declare either 'uri' (bolt) or 'path' (ladybug), not both.")
    else:
        graph_target = _string(graph_value)
        graph_user = ""
        graph_password = ""
        graph_database = ""

    default_name = Path(source_path).stem if source_path else "seocho-run"
    spec = RunSpec(
        name=_string(payload.get("name")) or default_name,
        description=_string(payload.get("description")),
        ontology_path=_string(ontology.get("path")),
        enforcement=_string(ontology.get("enforcement")).lower() or "guided",
        enforcement_set=bool(_string(ontology.get("enforcement"))),
        documents_path=_string(documents.get("path")),
        documents_recursive=bool(documents.get("recursive", True)),
        models=models,
        graph=graph_target,
        graph_kind=graph_kind,
        graph_user=graph_user or _string(payload.get("graph_user")) or "neo4j",
        graph_password=graph_password or _string(payload.get("graph_password")) or "password",
        database=graph_database or _string(payload.get("database")),
        workspace_id=_string(payload.get("workspace_id")),
        indexing=indexing,
        agent=agent,
        query=query,
        vector=vector,
        questions=_parse_questions(payload.get("questions"), errors=errors),
        output_dir=_string(output.get("dir")) or "runs",
        source_path=source_path,
        guardrail_candidates=guardrail_candidates,
        guardrail_corpus_profile=guardrail_corpus_profile,
    )

    if not spec.ontology_path and not (spec.guardrail_candidates and spec.guardrail_corpus_profile):
        errors.append("at ontology: a run spec requires ontology (path/mapping with 'path', "
                      "or 'select' with candidates + corpus_profile).")
    if not spec.documents_path:
        errors.append("at documents: a run spec requires documents (path or mapping with 'path').")
    if spec.enforcement not in _ALLOWED_ENFORCEMENT_MODES:
        errors.append(
            "at ontology.enforcement: must be one of: "
            f"{', '.join(sorted(_ALLOWED_ENFORCEMENT_MODES))}; got {spec.enforcement!r}."
        )

    for key in ("default", "indexing", "query"):
        if _string(spec.models.get(key)):
            parse_model_ref(spec.models[key], where=f"models.{key}", errors=errors)

    execution_mode = _string(spec.agent.get("execution_mode")).lower()
    if execution_mode and execution_mode not in _ALLOWED_EXECUTION_MODES:
        errors.append(
            "at agent.execution_mode: must be one of: "
            f"{', '.join(sorted(_ALLOWED_EXECUTION_MODES))}; got {execution_mode!r}."
        )
    routing_policy = _string(spec.agent.get("routing_policy")).lower()
    if routing_policy and routing_policy not in _ALLOWED_ROUTING_POLICIES:
        errors.append(
            "at agent.routing_policy: must be one of: "
            f"{', '.join(sorted(_ALLOWED_ROUTING_POLICIES))}; got {routing_policy!r}."
        )
    answer_style = _string(spec.query.get("answer_style")).lower()
    if answer_style and answer_style not in _ALLOWED_ANSWER_STYLES:
        errors.append(
            "at query.answer_style: must be one of: "
            f"{', '.join(sorted(_ALLOWED_ANSWER_STYLES))}; got {answer_style!r}."
        )
    repair_budget = spec.query.get("repair_budget")
    if repair_budget is not None and not isinstance(repair_budget, int):
        errors.append("at query.repair_budget: must be an integer.")
    limit = spec.query.get("limit")
    if limit is not None and not isinstance(limit, int):
        errors.append("at query.limit: must be an integer.")

    if spec.graph_kind:
        if spec.graph_kind not in _ALLOWED_GRAPH_KINDS:
            errors.append(
                "at graph.kind: must be one of: "
                f"{', '.join(sorted(_ALLOWED_GRAPH_KINDS))}; got {spec.graph_kind!r}."
            )
        else:
            is_bolt = spec.graph.startswith(_BOLT_SCHEMES)
            if spec.graph_kind in ("neo4j", "dozerdb") and not is_bolt:
                errors.append(
                    f"at graph: kind {spec.graph_kind!r} requires a bolt:// (or neo4j://) "
                    f"uri; got {spec.graph!r}."
                )
            if spec.graph_kind == "ladybug" and is_bolt:
                errors.append(
                    "at graph: kind 'ladybug' is the embedded engine and takes a file "
                    f"path, not a bolt uri; got {spec.graph!r}."
                )

    if spec.vector:
        vector_kind = spec.vector_kind()
        if vector_kind not in _ALLOWED_VECTOR_KINDS:
            errors.append(
                "at vector.kind: must be one of: "
                f"{', '.join(sorted(_ALLOWED_VECTOR_KINDS))}; got {vector_kind!r}."
            )
        dimension = spec.vector.get("dimension")
        if dimension is not None and not isinstance(dimension, int):
            errors.append("at vector.dimension: must be an integer.")

    if errors:
        raise RunSpecError(errors)
    return spec


def load_run_spec(path: "str | Path") -> RunSpec:
    """Load, env-resolve, and validate a run spec YAML file."""
    spec_path = Path(path)
    if not spec_path.exists():
        raise RunSpecError(
            [
                f"run spec not found: {spec_path}. "
                "Create one with: seocho run --init"
            ]
        )
    with spec_path.open("r", encoding="utf-8") as handle:
        try:
            payload = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise RunSpecError([f"invalid YAML in {spec_path}: {exc}"]) from exc
    return parse_run_spec(payload, source_path=str(spec_path))


RUN_SPEC_TEMPLATE = """\
# seocho.run.yaml — one yaml, one end-to-end run: seocho run
# Minimal config: an ontology, a documents folder, and your questions.
ontology: ./schema.yaml
documents: ./docs/
questions:
  - Which companies reported revenue growth?
  - Who is the CEO of Acme?

# --- Everything below is optional (defaults shown) ---------------------
# name: my-run                      # default: config filename stem
#
# ontology:
#   path: ./schema.yaml             # YAML / JSON-LD / TTL
#   enforcement: guided             # strict | guided | open
#                                   #   strict: reject chunks failing ontology validation
#                                   #   guided: ontology guides extraction (default)
#                                   #   open:   accept everything
#
# documents:
#   path: ./docs/                   # .txt .md .csv .json .jsonl .pdf
#   recursive: true
#
# models:
#   default: mara/MiniMax-M2.5      # provider/model for both phases
#   indexing: mara/MiniMax-M2       # per-phase override
#   query: mara/MiniMax-M2.5
#
# graph: bolt://localhost:7687      # omit for embedded LadybugDB (no server)
# graph_user: neo4j
# graph_password: ${NEO4J_PASSWORD:-password}
# database: neo4j                   # omit to derive from the ontology name
# workspace_id: my_run
#
# graph:                            # mapping form with an explicit backend
#   kind: dozerdb                   # neo4j | dozerdb | ladybug
#   uri: bolt://localhost:7687      # (ladybug uses `path:` instead)
#   user: neo4j
#   password: ${NEO4J_PASSWORD}
#   database: mydb
#
# vector:                           # optional hybrid-search vector store
#   kind: faiss                     # faiss (in-memory) | lancedb (on-disk)
#   embedding: fastembed            # local bge (default, no network) or an
#                                   #   LLM provider preset (mara, openai, ...)
#   embedding_model: BAAI/bge-small-en-v1.5
#   uri: ./.lancedb                 # lancedb only
#   table_name: seocho_vectors      # lancedb only
#
# indexing:
#   design: ./indexing_design.yaml  # optional IndexingDesignSpec
#   category: file
#   force: false
#
# agent:
#   design: ./agent_design.yaml     # optional AgentDesignSpec
#   execution_mode: pipeline        # pipeline | agent | supervisor
#   routing_policy: balanced        # fast | balanced | thorough
#
# query:
#   reasoning_mode: true
#   repair_budget: 1
#   answer_style: concise           # concise | evidence | table
#
# questions:                        # strings, or mappings with expected answers
#   - question: Who is the CEO of Acme?
#     expect: Jane Park             # recorded in the report, not auto-graded
#
# output:
#   dir: runs                       # report lands in runs/<name>-<timestamp>/
"""


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_RUN_SPEC_FILENAME",
    "QuestionSpec",
    "RUN_SPEC_TEMPLATE",
    "RunSpec",
    "RunSpecError",
    "load_run_spec",
    "parse_model_ref",
    "parse_run_spec",
]
