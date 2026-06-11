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
    if not target or not target.startswith(("bolt://", "neo4j://", "neo4j+s://", "bolt+s://")):
        path = target or ".seocho/local.lbug"
        try:
            import real_ladybug  # noqa: F401
        except ImportError:
            return PreflightCheck(
                name="graph",
                status="fail",
                detail=f"embedded ladybug ({path}) — the 'real_ladybug' package is not installed.",
                fix="pip install 'seocho[local]', or set graph: bolt://... to use Neo4j/DozerDB",
            )
        return PreflightCheck(name="graph", status="ok", detail=f"embedded ladybug ({path})")
    if not online:
        return PreflightCheck(
            name="graph", status="ok", detail=f"{target} (connection not checked in dry-run)"
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
