"""Content fingerprints for saved E2E runs; never invokes a model or graph."""

from __future__ import annotations

from contextlib import suppress
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any
from .run_redaction import safe_endpoint as safe_endpoint

from .run_spec import RunSpec

SCHEMA = "seocho.run_evidence.v2"

# Explicit behavioral settings: never collect the whole process environment.
RUNTIME_ENV_KEYS = (
    "SEOCHO_MULTI_PLAN",
    "SEOCHO_ANSWER_SHAPE",
    "SEOCHO_ROUTE_PROFILE",
    "SEOCHO_SEMANTIC_LAYER",
    "SEOCHO_MODEL_ROUTING",
    "SEOCHO_MODEL_ROUTING_TIERS",
    "SEOCHO_ONTOLOGY_GROUNDING",
    "SEOCHO_GROUNDING_SCORER",
    "SEOCHO_PLAN_GATE",
    "SEOCHO_CHUNK_FALLBACK",
    "SEOCHO_ONTOLOGY_CRITIQUE",
    "SEOCHO_ONTOLOGY_DRIFT_POLICY",
    "SEOCHO_GRAPH_COT_PROPERTIES",
    "SEOCHO_EXTRACTION_CONCURRENCY",
    "SEOCHO_DETERMINISTIC_FINANCIAL",
    "SEOCHO_VERIFIED_FINANCIAL_ANSWER",
    "SEOCHO_ENFORCE_WORKSPACE_FILTER",
    "SEOCHO_GRAPH_QUERY_MAX_INFLIGHT",
    "SEOCHO_GRAPH_QUERY_ADMISSION_WAIT_SECONDS",
    "SEOCHO_TIMEOUT",
    "SEOCHO_TEXT2CYPHER_MAX_TOKENS",
    "SEOCHO_ENABLE_ENRICHMENT_ROUTER",
    "SEOCHO_RESPONSE_CACHE_PATH",
    "SEOCHO_QUERY_PRECEDENCE",
    "VOCABULARY_RESOLVER_ENABLED",
    "VOCABULARY_GLOBAL_WORKSPACE_ID",
    "SEMANTIC_ARTIFACT_DIR",
    "SEOCHO_RUST_PROJECTOR_SOCKET",
    "SEOCHO_ONTOLOGY_STATE_DB",
    "SEOCHO_ONTOLOGY_LEASE_ID",
    "PYTHONHASHSEED",
)
RUNTIME_FILE_ENV_KEYS = (
    "ONTOLOGY_HINTS_PATH",
    "SEOCHO_RDF_GOVERNANCE_RECEIPT",
    "SEOCHO_AGENT_ONTOLOGY_PROFILE",
)


def digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def file_digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _resolve(spec: RunSpec, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(spec.source_path).parent / path


def _tree_digest(
    path: Path, *, recursive: bool = True, documents: bool = False
) -> tuple[str, int]:
    from .index.file_reader import SUPPORTED_EXTENSIONS

    if path.is_file():
        return digest([(path.name, file_digest(path))]), 1
    if not path.is_dir():
        raise FileNotFoundError(f"Input path is unavailable: {path}")
    files = sorted(p for p in path.glob("**/*" if recursive else "*") if p.is_file())
    if documents:
        files = [p for p in files if p.suffix.lower() in SUPPORTED_EXTENSIONS]
    return digest(
        [(p.relative_to(path).as_posix(), file_digest(p)) for p in files]
    ), len(files)


def _settings(spec: RunSpec, section: str) -> dict[str, Any]:
    result = dict(getattr(spec, section))
    # A referenced design/bundle changing in place must change the receipt.
    for key in ("design", "ontology_bundle_dir", "bundle_dir"):
        value = result.get(key)
        if isinstance(value, str) and value:
            result[key] = _tree_digest(_resolve(spec, value))[0]
    # These are identities for mutable storage, not credential values.
    for key in ("uri",):
        if isinstance(result.get(key), str):
            result[key] = safe_endpoint(result[key])
    return result


def source_receipt() -> dict[str, Any]:
    package = Path(__file__).resolve().parent
    root = package.parent.parent
    files = sorted(package.rglob("*.py"))
    records = [(p.relative_to(package).as_posix(), file_digest(p)) for p in files]
    for name in ("pyproject.toml", "uv.lock"):
        path = root / name
        if path.is_file():
            records.append((name, file_digest(path)))
    revision: str | None = None
    if (root / ".git").exists():
        with suppress(OSError, subprocess.SubprocessError):
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            ).stdout.strip()
    return {
        "sha256": digest(records),
        "git_revision": revision,
        "scope": "installed seocho Python sources plus available pyproject/lock",
    }


def collect_evidence(
    spec: RunSpec, *, only: str | None, force: bool, track: bool
) -> dict[str, Any]:
    """Hash actual inputs before execution; leave unavailable evidence explicit."""
    conditions: dict[str, str] = {}
    gaps: list[str] = []
    input_files: int | None = None
    try:
        conditions["documents"], input_files = _tree_digest(
            _resolve(spec, spec.documents_path),
            recursive=spec.documents_recursive,
            documents=True,
        )
    except OSError as exc:
        gaps.append(f"documents: {type(exc).__name__}")
    try:
        if spec.ontology_path:
            ontology_hash = file_digest(_resolve(spec, spec.ontology_path))
        else:
            # Adaptive selection may use paid derivation; never run it to fingerprint.
            ontology_hash = ""
            gaps.append("ontology: adaptive selection needs a resolved snapshot")
        conditions["ontology"] = digest(
            {"content": ontology_hash, "enforcement": spec.enforcement}
        )
    except OSError as exc:
        gaps.append(f"ontology: {type(exc).__name__}")
    conditions["questions"] = digest(
        [
            {
                "id": q.question_id or str(i + 1),
                "question": q.question,
                "expect": q.expect,
            }
            for i, q in enumerate(spec.questions)
        ]
    )
    conditions["models"] = digest(
        {"indexing": spec.indexing_model(), "query": spec.query_model()}
    )
    for section in ("indexing", "agent", "query", "vector", "governance"):
        try:
            conditions[section] = digest(_settings(spec, section))
        except OSError as exc:
            gaps.append(f"{section}: {type(exc).__name__}")
    conditions["execution"] = digest(
        {
            "only": only,
            "force": force,
            "track": track,
            "recursive": spec.documents_recursive,
            "governance_mode": spec.governance_mode,
        }
    )
    # Workspace/database are recorded in run metadata but intentionally not matched:
    # isolated arms should use different targets. External graph state is unverified.
    conditions["graph"] = digest(
        {"kind": spec.resolved_graph_kind(), "endpoint": safe_endpoint(spec.graph)}
    )
    source = source_receipt()
    conditions["source"] = source["sha256"]
    versions: dict[str, str | None] = {"python": platform.python_version()}
    for name in ("seocho", "neo4j", "openai", "openai-agents", "pydantic"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    conditions["environment"] = digest(versions)
    runtime_settings: dict[str, Any] = {
        key: os.environ.get(key) for key in RUNTIME_ENV_KEYS
    }
    for key in RUNTIME_FILE_ENV_KEYS:
        value = os.environ.get(key)
        runtime_settings[key] = value
        if value:
            try:
                runtime_settings[key] = {
                    "path": value,
                    "sha256": _tree_digest(Path(value))[0],
                }
            except OSError as exc:
                gaps.append(f"runtime_settings.{key}: {type(exc).__name__}")
    conditions["runtime_settings"] = digest(runtime_settings)
    # Endpoint overrides affect experiments even when provider/model names agree.
    overrides = {
        k: v
        for k, v in os.environ.items()
        if k.endswith(("_BASE_URL", "_API_BASE")) and not k.startswith("UV_")
    }
    conditions["provider_endpoints"] = digest(
        {k: safe_endpoint(v) for k, v in overrides.items()}
    )
    return {
        "schema_version": SCHEMA,
        "conditions": conditions,
        "gaps": gaps,
        "input_files": input_files,
        "source": source,
        "versions": versions,
        "limitations": [
            "Fingerprints identify inputs; they do not snapshot or isolate a mutable graph.",
            "Service versions, hardware, provider revisions, warmup and cache state are unverified.",
            "A single run is descriptive evidence, not a causal quality/performance result.",
        ],
    }
