"""Preflight checks for ``seocho run``.

Collects every check before reporting (no fail-fast), so one invocation
tells the user everything that needs fixing. Each failure message names
what failed, why, and one copy-pasteable fix.

Offline checks (also what ``--dry-run`` runs) touch only the local
filesystem and environment variables. Online checks add a graph
connection attempt before a real run spends LLM tokens.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .run_spec import RunSpec, parse_model_ref


@dataclass(slots=True)
class PreflightCheck:
    name: str
    status: str  # "ok" | "fail"
    detail: str = ""
    fix: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def render(self) -> str:
        mark = "ok  " if self.ok else "FAIL"
        line = f"  {mark}  {self.name} — {self.detail}" if self.detail else f"  {mark}  {self.name}"
        if not self.ok and self.fix:
            line += f"\n        fix: {self.fix}"
        return line


@dataclass(slots=True)
class PreflightReport:
    checks: List[PreflightCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def render(self) -> str:
        return "\n".join(check.render() for check in self.checks)

    def failures(self) -> List[PreflightCheck]:
        return [check for check in self.checks if not check.ok]


def _base_dir(spec: RunSpec) -> Path:
    return Path(spec.source_path).parent if spec.source_path else Path(".")


def _resolve(spec: RunSpec, raw_path: str) -> Path:
    """Resolve a spec-relative path against the config file's directory."""
    path = Path(raw_path)
    return path if path.is_absolute() else _base_dir(spec) / path


def _check_ontology(spec: RunSpec) -> PreflightCheck:
    path = _resolve(spec, spec.ontology_path)
    if not path.exists():
        return PreflightCheck(
            name="ontology",
            status="fail",
            detail=f"{path} does not exist.",
            fix="create one with: seocho init",
        )
    try:
        from .ontology import Ontology

        ontology = Ontology.load(path)
    except Exception as exc:
        return PreflightCheck(
            name="ontology",
            status="fail",
            detail=f"{path} failed to load: {exc}",
            fix=f"debug with: seocho ontology check --schema {path}",
        )
    return PreflightCheck(
        name="ontology",
        status="ok",
        detail=(
            f"{path} ({len(ontology.nodes)} node types, "
            f"{len(ontology.relationships)} relationships, mode={spec.enforcement})"
        ),
    )


def _check_documents(spec: RunSpec) -> PreflightCheck:
    from .index.file_reader import SUPPORTED_EXTENSIONS

    path = _resolve(spec, spec.documents_path)
    if not path.exists():
        return PreflightCheck(
            name="documents",
            status="fail",
            detail=f"{path} does not exist.",
            fix="point documents at a folder or file of .txt/.md/.csv/.json/.jsonl/.pdf content",
        )
    if path.is_file():
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            return PreflightCheck(name="documents", status="ok", detail=f"{path} (1 file)")
        return PreflightCheck(
            name="documents",
            status="fail",
            detail=f"{path} has unsupported extension {path.suffix}.",
            fix=f"supported: {' '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    pattern = "**/*" if spec.documents_recursive else "*"
    supported: Counter = Counter()
    unsupported: Counter = Counter()
    for item in path.glob(pattern):
        if not item.is_file():
            continue
        if item.suffix.lower() in SUPPORTED_EXTENSIONS:
            supported[item.suffix.lower()] += 1
        elif item.suffix:
            unsupported[item.suffix.lower()] += 1
    total = sum(supported.values())
    if total == 0:
        found = ", ".join(f"{ext} ({count})" for ext, count in unsupported.most_common(3))
        return PreflightCheck(
            name="documents",
            status="fail",
            detail=(
                f"{path} — 0 supported files "
                f"(looked for {' '.join(sorted(SUPPORTED_EXTENSIONS))}, "
                f"recursive={str(spec.documents_recursive).lower()})."
                + (f" Found unsupported: {found}." if found else "")
            ),
            fix="add supported documents or fix the documents path",
        )
    breakdown = ", ".join(f"{count} {ext}" for ext, count in supported.most_common())
    return PreflightCheck(
        name="documents", status="ok", detail=f"{path} ({total} supported files: {breakdown})"
    )


def _check_design(spec: RunSpec, *, section: str) -> "PreflightCheck | None":
    design = getattr(spec, section).get("design")
    if not design:
        return None
    if isinstance(design, dict):
        loader = _design_loader(section)
        try:
            loader["from_dict"](design)
        except Exception as exc:
            return PreflightCheck(
                name=f"{section}.design",
                status="fail",
                detail=f"inline design is invalid: {exc}",
            )
        return PreflightCheck(name=f"{section}.design", status="ok", detail="inline design valid")
    path = _resolve(spec, str(design))
    if not path.exists():
        return PreflightCheck(
            name=f"{section}.design",
            status="fail",
            detail=f"{path} does not exist.",
            fix=f"fix the {section}.design path or remove the key",
        )
    loader = _design_loader(section)
    try:
        loader["from_yaml"](path)
    except Exception as exc:
        return PreflightCheck(
            name=f"{section}.design", status="fail", detail=f"{path} failed to load: {exc}"
        )
    return PreflightCheck(name=f"{section}.design", status="ok", detail=str(path))


def _design_loader(section: str) -> dict:
    if section == "agent":
        from .agent_design import AgentDesignSpec

        return {"from_yaml": AgentDesignSpec.from_yaml, "from_dict": AgentDesignSpec.from_dict}
    from .indexing_design import IndexingDesignSpec

    return {"from_yaml": IndexingDesignSpec.from_yaml, "from_dict": IndexingDesignSpec.from_dict}


def _check_models(spec: RunSpec) -> List[PreflightCheck]:
    import os

    from .store.llm import get_provider_spec

    checks: List[PreflightCheck] = []
    seen = set()
    for phase, ref in (("indexing", spec.indexing_model()), ("query", spec.query_model())):
        if ref in seen:
            continue
        seen.add(ref)
        errors: List[str] = []
        provider, _model = parse_model_ref(ref, where=f"models.{phase}", errors=errors)
        try:
            provider_spec = get_provider_spec(provider)
        except ValueError as exc:
            checks.append(PreflightCheck(name=f"llm {ref}", status="fail", detail=str(exc)))
            continue
        env_names = (provider_spec.api_key_env, *provider_spec.api_key_env_aliases)
        if any(os.getenv(name, "").strip() for name in env_names):
            checks.append(
                PreflightCheck(
                    name=f"llm {ref}", status="ok", detail=f"{provider_spec.api_key_env} set"
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name=f"llm {ref}",
                    status="fail",
                    detail=f"{provider_spec.api_key_env} is not set.",
                    fix=(
                        f"export {provider_spec.api_key_env}=..., "
                        "or switch provider in the config (models.default)"
                    ),
                )
            )
    return checks


def _check_graph(spec: RunSpec, *, online: bool) -> PreflightCheck:
    target = spec.graph
    kind = spec.resolved_graph_kind()
    if not online:
        return PreflightCheck(
            name="graph", status="ok",
            detail=f"{kind} {target} (connection not checked in dry-run)",
        )
    try:
        from .store.graph import Neo4jGraphStore

        store = Neo4jGraphStore(target, spec.graph_user, spec.graph_password)
        try:
            store.query("RETURN 1 AS ok")
        finally:
            store.close()
    except Exception as exc:
        return PreflightCheck(
            name="graph",
            status="fail",
            detail=f"{target} — {exc}",
            fix=(
                "start the stack with 'seocho serve', or remove the 'graph:' key "
                "to use the embedded engine (no server needed)"
            ),
        )
    return PreflightCheck(name="graph", status="ok", detail=f"{target} connected")


def _check_vector(spec: RunSpec) -> "PreflightCheck | None":
    if not spec.uses_vector_store():
        return None
    kind = spec.vector_kind()
    module = "faiss" if kind == "faiss" else "lancedb"
    try:
        __import__(module)
    except ImportError:
        return PreflightCheck(
            name=f"vector {kind}",
            status="fail",
            detail=f"the '{module}' package is not installed.",
            fix=f"pip install {'faiss-cpu' if kind == 'faiss' else 'lancedb'}",
        )
    embedding = spec.vector_embedding()
    if embedding == "fastembed":
        try:
            __import__("fastembed")
        except ImportError:
            return PreflightCheck(
                name=f"vector {kind}",
                status="fail",
                detail="embedding=fastembed but the 'fastembed' package is not installed.",
                fix="pip install fastembed, or set vector.embedding to a provider (e.g. mara)",
            )
        return PreflightCheck(
            name=f"vector {kind}", status="ok", detail="embedding=fastembed (local bge)"
        )
    import os

    from .store.llm import get_provider_spec

    try:
        provider_spec = get_provider_spec(embedding)
    except ValueError as exc:
        return PreflightCheck(name=f"vector {kind}", status="fail", detail=str(exc))
    env_names = (provider_spec.api_key_env, *provider_spec.api_key_env_aliases)
    if any(os.getenv(name, "").strip() for name in env_names):
        return PreflightCheck(
            name=f"vector {kind}", status="ok",
            detail=f"embedding={embedding} ({provider_spec.api_key_env} set)",
        )
    return PreflightCheck(
        name=f"vector {kind}",
        status="fail",
        detail=f"embedding={embedding} but {provider_spec.api_key_env} is not set.",
        fix=f"export {provider_spec.api_key_env}=..., or use vector.embedding: fastembed",
    )


def _check_projection_governance(spec: RunSpec, *, online: bool) -> PreflightCheck:
    """Check a declared projection contract before any paid LLM call.

    The result is a readiness check, not an authorization decision: the Rust
    daemon re-reads the active pointer and lease immediately before every
    governed graph write.
    """
    import os

    from .ontology.plane_policy import ProjectionPolicyError, decide_projection
    from .ontology.projection_receipt import (
        ProjectionReceiptError,
        load_projection_admission_from_env,
        load_projection_receipt_from_env,
    )

    socket_path = os.getenv("SEOCHO_RUST_PROJECTOR_SOCKET", "").strip() or None
    if spec.governance_mode in {"governed", "lockdown"}:
        bundle_dir = str(spec.governance.get("bundle_dir") or "").strip()
        if not bundle_dir:
            return PreflightCheck(
                name="projection governance", status="fail",
                detail="governed candidate staging requires governance.bundle_dir",
                fix="set governance.bundle_dir to the immutable active RDF bundle",
            )
        if not Path(bundle_dir).is_dir():
            return PreflightCheck(name="projection governance", status="fail",
                                  detail=f"governance.bundle_dir does not exist: {bundle_dir}")
    try:
        receipt = load_projection_receipt_from_env()
        admission = load_projection_admission_from_env()
        # A governed run stages a distinct receipt after each extraction, but
        # its lease capability is already concrete and must be checked before
        # paid work begins. Prefer an explicitly supplied capability for
        # operator tooling; otherwise derive the run-scoped one from its
        # lifecycle store rather than requiring an unrelated environment file.
        if spec.governance_mode in {"governed", "lockdown"} and admission is None:
            from .ontology.lifecycle import OntologyLifecycleStore

            state_db = str(spec.governance.get("state_db") or "").strip()
            lease_id = str(spec.governance.get("lease_id") or "").strip()
            if not state_db or not lease_id:
                raise ValueError(
                    "governed candidate staging requires governance.state_db and governance.lease_id"
                )
            admission = OntologyLifecycleStore(state_db).admission(lease_id)
        # In a governed run the receipt is created after extraction from each
        # candidate payload.  A pre-existing receipt is optional evidence, not
        # a substitute for that per-candidate stage.
        staged_receipt = {"per_candidate": True} if spec.governance_mode in {"governed", "lockdown"} else None
        decision = decide_projection(
            spec.governance_mode,
            rust_socket=socket_path,
            semantic_receipt=receipt or staged_receipt,
            admission=admission,
        )
    except (ProjectionPolicyError, ProjectionReceiptError, ValueError) as exc:
        return PreflightCheck(
            name="projection governance", status="fail", detail=str(exc),
            fix=("for governed/lockdown configure the Rust socket, promotable RDF receipt, "
                 "projection profile, lifecycle state DB, and live projection lease"),
        )
    if online and decision.requires_rust_projector:
        try:
            from .dataplane.seochod import SeochodProjectionClient

            health = SeochodProjectionClient(socket_path or "").health()
            if not health.get("ok"):
                raise RuntimeError(health.get("error") or "health rejected")
        except Exception as exc:  # no paid work can begin in a strict mode
            return PreflightCheck(
                name="projection governance", status="fail",
                detail=f"{spec.governance_mode} capability is valid but seochod is unavailable: {exc}",
                fix="start seochod with SEOCHOD_CONTROL_DB and SEOCHOD_REQUIRE_GOVERNANCE=1",
            )
    detail = (f"mode={spec.governance_mode}; "
              f"canonical_claim_allowed={str(decision.canonical_claim_allowed).lower()}")
    if decision.missing:
        detail += "; optional signals absent=" + ",".join(decision.missing)
    if spec.governance_mode in {"governed", "lockdown"}:
        detail += "; receipt=per-candidate-stage"
    return PreflightCheck(name="projection governance", status="ok", detail=detail)


def run_preflight(spec: RunSpec, *, online: bool = False) -> PreflightReport:
    """Run all preflight checks for a run spec.

    ``online=False`` (dry-run) stays filesystem/env only; ``online=True``
    additionally attempts a graph connection.
    """
    report = PreflightReport()
    report.checks.append(_check_ontology(spec))
    report.checks.append(_check_documents(spec))
    for section in ("indexing", "agent"):
        check = _check_design(spec, section=section)
        if check is not None:
            report.checks.append(check)
    report.checks.extend(_check_models(spec))
    report.checks.append(_check_graph(spec, online=online))
    report.checks.append(_check_projection_governance(spec, online=online))
    vector_check = _check_vector(spec)
    if vector_check is not None:
        report.checks.append(vector_check)
    if spec.index_only():
        report.checks.append(
            PreflightCheck(name="questions", status="ok", detail="none (index-only run)")
        )
    else:
        report.checks.append(
            PreflightCheck(name="questions", status="ok", detail=f"{len(spec.questions)} questions")
        )
    return report


__all__ = ["PreflightCheck", "PreflightReport", "run_preflight"]
