"""Shared utilities for FinDER × FIBO benchmark scripts.

Consolidates env loading, hashing, Opik project aliasing, Neo4j user
normalization, determinism, the single-source PHASE_CASES registry, workspace
ID building, meta prompt injection, ladybug cleanup, and preflight checks
that were previously duplicated across `scripts/benchmarks/finder_*.py`.

Designed to be imported as ``from examples.finder.lib import bench_common``.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
UPSTREAM_ENV = Path("/home/hadry/openup/.env")  # shared API keys (fallback)
REPO_ENV = REPO_ROOT / ".env"                    # repo-local overrides (primary)
# Load repo .env FIRST so user-managed key updates override the upstream
# shared file. ``load_env_files`` skips keys already set in os.environ, so
# the first file wins per key.
DEFAULT_ENV_FILES: tuple[Path, ...] = (REPO_ENV, UPSTREAM_ENV)
DEFAULT_LBUG_DIR = REPO_ROOT / ".seocho/finder_phase_runs"


# ---------------------------------------------------------------------------
# Env loading
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnvReport:
    loaded_files: tuple[str, ...]
    set_keys: tuple[str, ...]
    skipped_keys: tuple[str, ...]  # keys already in env, not overwritten


def load_env_files(roots: Iterable[Path] = DEFAULT_ENV_FILES) -> EnvReport:
    """Load .env files in priority order without overwriting existing env.

    Order: upstream (`/home/hadry/openup/.env`) → repo-local (`<repo>/.env`).
    Quoted values are stripped. Comments and blank lines are ignored.
    """
    loaded: list[str] = []
    set_keys: list[str] = []
    skipped: list[str] = []
    for path in roots:
        path = Path(path)
        if not path.is_file():
            continue
        loaded.append(str(path))
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if not k:
                    continue
                if k in os.environ:
                    skipped.append(k)
                    continue
                os.environ[k] = v
                set_keys.append(k)
    return EnvReport(tuple(loaded), tuple(set_keys), tuple(skipped))


# ---------------------------------------------------------------------------
# Hashing & determinism
# ---------------------------------------------------------------------------

def short_hash(text: str, n: int = 10) -> str:
    """Deterministic short hex prefix (SHA-256)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:n]


def set_global_determinism(seed: int = 42) -> None:
    """Best-effort global seed for `random` and `numpy` (if installed)."""
    random.seed(seed)
    os.environ.setdefault("PYTHONHASHSEED", str(seed))
    try:
        import numpy as np  # noqa: PLC0415
        np.random.seed(seed)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Opik project / workspace normalization
# ---------------------------------------------------------------------------

# Cache to avoid repeated REST calls.
_OPIK_WORKSPACES_CACHE: list[str] | None = None


def _fetch_opik_workspaces() -> list[str]:
    """Best-effort list of available Opik workspaces via REST.

    Falls back to a small verified set if the API call fails (offline mode).
    """
    global _OPIK_WORKSPACES_CACHE
    if _OPIK_WORKSPACES_CACHE is not None:
        return _OPIK_WORKSPACES_CACHE
    api_key = os.environ.get("OPIK_API_KEY")
    if not api_key:
        _OPIK_WORKSPACES_CACHE = []
        return _OPIK_WORKSPACES_CACHE
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://www.comet.com/api/rest/v2/workspaces",
            headers={"Authorization": api_key},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        names = list(data.get("workspaceNames") or [])
        _OPIK_WORKSPACES_CACHE = names
        return names
    except Exception:
        # Conservative fallback derived from prior session verification
        _OPIK_WORKSPACES_CACHE = ["seocho", "tteon"]
        return _OPIK_WORKSPACES_CACHE


def alias_opik_project(*, verbose: bool = False) -> None:
    """Normalize Opik env variable naming and detect workspace/project swap.

    SEOCHO ``seocho.tracing.configure_tracing_from_env`` reads
    ``OPIK_PROJECT_NAME``; users frequently write ``OPIK_PROJECT`` instead.
    Additionally users sometimes swap the workspace and project values — when
    the workspace is not in the verified set but the project is, we swap.
    """
    if not os.environ.get("OPIK_PROJECT_NAME") and os.environ.get("OPIK_PROJECT"):
        os.environ["OPIK_PROJECT_NAME"] = os.environ["OPIK_PROJECT"]
    ws = os.environ.get("OPIK_WORKSPACE", "")
    pj = os.environ.get("OPIK_PROJECT_NAME", "")
    verified = set(_fetch_opik_workspaces())
    if ws and verified and ws not in verified and pj in verified:
        if verbose:
            print(f"[opik] swapping mis-matched workspace/project: {ws!r} ↔ {pj!r}", flush=True)
        os.environ["OPIK_WORKSPACE"] = pj
        os.environ["OPIK_PROJECT_NAME"] = ws


# ---------------------------------------------------------------------------
# Neo4j user case normalization (CLAUDE.md §8 — DB safety)
# ---------------------------------------------------------------------------

def normalize_neo4j_user() -> None:
    """Lower-case NEO4J_USER (Neo4j default account is lowercase).

    Mirrors common .env input where users may type `NEO4J_USER="NEO4J"`.
    """
    current = os.environ.get("NEO4J_USER") or os.environ.get("NEO4J_USERNAME")
    if current and current != current.lower():
        os.environ["NEO4J_USER"] = current.lower()


# ---------------------------------------------------------------------------
# PHASE_CASES — single source of truth
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    slice_tag: str
    note: str = ""


@dataclass(frozen=True)
class PhaseSpec:
    code: str
    name: str
    treatment_modules: tuple[str, ...]
    rationale: str
    cases: tuple[CaseSpec, ...]


PHASES: tuple[PhaseSpec, ...] = (
    PhaseSpec(
        code="P0",
        name="phase0_S1_compositional",
        treatment_modules=("be", "ind"),
        rationale="Multi-year arithmetic on financial statements — graph wins via FinancialMetric{Company,Year}",
        cases=(
            CaseSpec("4af93b03", "S1_FIN_COMP", "ROST 3-FY net profit margin trend"),
            CaseSpec("ea48df6d", "S1_FIN_COMP", "PANW Diluted EPS trend 2022-2024"),
            CaseSpec("44645759", "S1_FIN_COMP", "AMAT CAGR FY22-FY24"),
        ),
    ),
    PhaseSpec(
        code="P1A",
        name="phase1A_dbt",
        treatment_modules=("be", "sec", "ind", "dbt"),
        rationale="Debt instrument structure (senior notes, coupons, maturities) — fibo-sec-dbt",
        cases=(CaseSpec("2cb2831f", "S5_FN_MULTI", "INTU senior notes 2020/2023"),),
    ),
    PhaseSpec(
        code="P1B",
        name="phase1B_mkt",
        treatment_modules=("be", "fbc", "mkt"),
        rationale="Markets & exchanges — fibo-fbc-fct-mkt (weak signal; included for completeness)",
        cases=(CaseSpec("416d398e", "S4_CO_MULTI_NONQUANT", "Nasdaq segment alignment"),),
    ),
    PhaseSpec(
        code="P1C",
        name="phase1C_acc",
        treatment_modules=("be", "ind", "fnd", "acc"),
        rationale="Accounting policy × statement integration — fibo-fnd-acc",
        cases=(
            CaseSpec("a1a0c98d", "S5_FN_MULTI", "Seagate PPE+depreciation+CAPEX"),
            CaseSpec("07a24577", "S5_FN_MULTI", "LULU accrued CapEx"),
        ),
    ),
    PhaseSpec(
        code="P1D",
        name="phase1D_corp",
        treatment_modules=("be", "fbc", "corp"),
        rationale="Corporate type & subsidiary structure — fibo-be-corp-corp",
        cases=(
            CaseSpec("73a13b04", "S2_FIN_NONQUANT_MULTI", "CINF subsidiary regulatory dividend"),
            CaseSpec("5a8e3536", "S3_CO_COMP", "Berkshire Hathaway subsidiaries (Compositional)"),
        ),
    ),
)


def iter_phase_cases() -> Iterable[tuple[PhaseSpec, CaseSpec]]:
    for phase in PHASES:
        for case in phase.cases:
            yield phase, case


def get_phase(code: str) -> PhaseSpec:
    for p in PHASES:
        if p.code == code:
            return p
    raise KeyError(f"unknown phase code: {code}")


# ---------------------------------------------------------------------------
# Workspace ID
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkspaceIdBuilder:
    prefix: str = ""

    def __call__(self, phase: str, case_id: str, variant: str = "treatment") -> str:
        prefix = self.prefix or os.environ.get("FINDER_WS_PREFIX", "finder-phase")
        return f"{prefix}-{phase}-{case_id}-{variant}"


def workspace_id_for(phase: str, case_id: str, variant: str = "treatment", *, prefix: str = "") -> str:
    return WorkspaceIdBuilder(prefix=prefix)(phase, case_id, variant)


# ---------------------------------------------------------------------------
# Meta prompt injection
# ---------------------------------------------------------------------------

DEFAULT_META_PROMPT_PATH = REPO_ROOT / "examples/finder/datasets/kimi_meta_system_prompt.md"


def load_meta_prompt(path: Path = DEFAULT_META_PROMPT_PATH) -> str:
    """Load the Kimi meta system prompt, trimming preamble before `## ROLE`."""
    if not Path(path).is_file():
        return ""
    text = Path(path).read_text(encoding="utf-8")
    marker = "## ROLE"
    idx = text.find(marker)
    return text[idx:] if idx >= 0 else text


def compose_system_prompt(meta_prompt: str, task_system: str) -> str:
    """Join meta prompt + task-specific system instruction."""
    meta = (meta_prompt or "").strip()
    task = (task_system or "").strip()
    if not meta:
        return task
    if not task:
        return meta
    return f"{meta}\n\n---\n\n## TASK-SPECIFIC INSTRUCTION\n{task}"


class MetaPromptLLMWrapper:
    """Wrap a SEOCHO LLMBackend to prepend a meta prompt to every call.

    Mirrors the implementation pattern that previously lived in the phase
    experiment runner so all benchmark scripts share one wrapper.
    """

    def __init__(self, inner, meta: str) -> None:
        self._inner = inner
        self._meta = (meta or "").strip()

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def _join(self, system: str) -> str:
        return compose_system_prompt(self._meta, system)

    def complete(self, *, system: str, user: str, **kwargs):
        return self._inner.complete(system=self._join(system), user=user, **kwargs)

    async def acomplete(self, *, system: str, user: str, **kwargs):
        return await self._inner.acomplete(system=self._join(system), user=user, **kwargs)

    def chat(self, text: str, *, system=None, **kwargs):
        return self._inner.chat(text, system=self._join(system or ""), **kwargs)


# ---------------------------------------------------------------------------
# LadyBug file housekeeping
# ---------------------------------------------------------------------------

def fresh_ladybug(path: Path | str) -> Path:
    """Ensure a LadyBug file path is fresh by removing sidecar artifacts.

    Removes ``<path>``, ``<path>.wal``, ``<path>.shm``, and any other sidecar
    files that share the same prefix to prevent the "Database ID does not
    match" runtime error when reusing a path.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    for sidecar in p.parent.glob(p.name + "*"):
        try:
            sidecar.unlink()
        except OSError:
            pass
    return p


# ---------------------------------------------------------------------------
# Atomic JSON write
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path | str, payload, *, indent: int = 2) -> Path:
    """Write JSON to a temp file then atomically rename to the target path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8")
    tmp.replace(path)
    return path


# ---------------------------------------------------------------------------
# Trace tagging helpers
# ---------------------------------------------------------------------------

def opik_tags(
    *,
    llm_spec: str,
    dataset_index: str,
    prompt_hash: str,
    ontology_hash: str,
    modules: str,
    extra: Mapping[str, str] | None = None,
) -> list[str]:
    """Build the standardized Opik tag set with the 4 mandatory identifiers."""
    tags = [
        f"model:{llm_spec}",
        f"dataset_index:{dataset_index}",
        f"prompt_hash:{prompt_hash}",
        f"ontology_hash:{ontology_hash}",
        f"modules:{modules or 'baseline'}",
    ]
    if extra:
        for k, v in extra.items():
            tags.append(f"{k}:{v}")
    return tags


# ---------------------------------------------------------------------------
# Multi-LLM "4 core meta" tag/metadata builder (Tier 3)
# ---------------------------------------------------------------------------

def build_core_meta(
    *,
    # 1. dataset
    dataset_name: str,          # e.g. "all_slices.csv"
    dataset_index: str,         # "{slice}/{case_id}"
    case_id: str,
    slice_tag: str,
    category: str,
    # 2. model
    llm_spec: str,              # "kimi/kimi-k2.5"
    provider: str,              # "kimi"
    judge_spec: str = "",       # "openai/gpt-4o-mini"
    # 3. flow
    mode: str = "",             # "vector" | "graph" | "hybrid"
    retrieval_k: int = 0,
    reasoning_mode: bool = False,
    repair_budget: int = 0,
    flow: str = "graphrag",
    # graph-related quality dimensions (track A/B before/after upgrades)
    graph_quality: str = "raw",         # "raw" | "qualified" | "denormalized"
    cypher_agent_version: str = "v1",   # "v1" | "v2" | ...
    # 4. ontology
    ontology_hash: str = "",
    ontology_modules: str = "",
    ontology_id: str = "",
    prompt_hash: str = "",
    # extras
    run_prefix: str = "",
    workspace_id: str = "",
    extra_tags: Mapping[str, str] | None = None,
    extra_metadata: Mapping[str, object] | None = None,
) -> tuple[list[str], dict]:
    """Return ``(tags, metadata)`` for an Opik trace covering the 4 core meta.

    Tag groups:
      1. dataset:    dataset, dataset_index, case, slice, category
      2. model:      model, provider, judge
      3. flow:       flow, mode, retrieval_k, reasoning_mode, repair_budget
      4. ontology:   ontology_hash, modules, ontology_id, prompt_hash
    """
    # Minimal, human-readable, FILTERABLE tag set (CLAUDE.md §19 Opik contract):
    # keep the Opik project legible at ~7 tags/trace. Everything else lives in
    # metadata (reproduce/inspect) or feedback_scores (numeric, comparable).
    tags = [
        f"slice:{slice_tag}",
        f"model:{llm_spec}",
        f"flow:{flow}",
        f"graph_quality:{graph_quality}",
    ]
    if run_prefix:
        tags.append(f"run:{run_prefix}")
    # Promote ONLY the two primary comparison axes from extra_tags to tags
    # (retrieval, ontology); the rest (prompt, seed, …) go to metadata.
    _TAG_WHITELIST = {"retrieval", "ontology"}
    if extra_tags:
        for k, v in extra_tags.items():
            if k in _TAG_WHITELIST:
                tags.append(f"{k}:{v}")

    metadata: dict = {
        # 1. dataset
        "dataset_name": dataset_name,
        "dataset_index": dataset_index,
        "case_id": case_id,
        "slice": slice_tag,
        "category": category,
        # 2. model
        "model": llm_spec,
        "provider": provider,
        "judge_spec": judge_spec,
        # 3. flow
        "flow": flow,
        "mode": mode,
        "retrieval_k": retrieval_k,
        "reasoning_mode": reasoning_mode,
        "repair_budget": repair_budget,
        "graph_quality": graph_quality,
        "cypher_agent_version": cypher_agent_version,
        # 4. ontology
        "ontology_hash": ontology_hash,
        "ontology_modules": ontology_modules,
        "ontology_id": ontology_id,
        "prompt_hash": prompt_hash,
        # routing
        "run_prefix": run_prefix,
        "workspace_id": workspace_id,
    }
    # Non-tag extra_tags (prompt, seed, …) are preserved in metadata for repro.
    if extra_tags:
        for k, v in extra_tags.items():
            metadata.setdefault(k, v)
    if extra_metadata:
        metadata.update(extra_metadata)
    return tags, metadata


# ---------------------------------------------------------------------------
# Stratified sampling (slice-aware)
# ---------------------------------------------------------------------------

def stratified_sample(df, *, fraction: float, slice_col: str = "slice", seed: int = 42, min_per_slice: int = 1):
    """Sample ``fraction`` of rows per ``slice_col`` value (deterministic).

    Returns a pandas DataFrame ordered by (slice_col, _id). If a slice has
    fewer rows than ``ceil(n*fraction)``, all of them are kept. Honors
    ``min_per_slice`` (e.g. 1) so every slice is represented.
    """
    import pandas as pd  # noqa: PLC0415
    import math
    parts: list = []
    for tag, group in df.groupby(slice_col):
        n = len(group)
        take = max(min_per_slice, math.ceil(n * fraction))
        take = min(take, n)
        parts.append(group.sample(n=take, random_state=seed).sort_values("_id"))
    if not parts:
        return df.iloc[:0]
    return pd.concat(parts, ignore_index=True)


def set_opik_trace_metadata(*, name: str, tags: Sequence[str], metadata: Mapping[str, object] | None = None) -> None:
    """Best-effort: update the current Opik trace if one is active."""
    try:
        from opik import opik_context  # type: ignore
        opik_context.update_current_trace(name=name, tags=list(tags), metadata=dict(metadata or {}))
    except Exception:
        pass


def set_opik_feedback_scores(scores: Mapping[str, object]) -> None:
    """Attach numeric feedback scores to the CURRENT Opik trace.

    Feedback scores (not tags) are what the Opik UI renders as sortable,
    chartable columns — this is what makes vector vs graph vs hybrid trivially
    comparable. Call from inside a run_under_opik_track work_fn.
    """
    try:
        from opik import opik_context  # type: ignore
        fs = [{"name": k, "value": float(v)} for k, v in scores.items() if v is not None]
        if fs:
            opik_context.update_current_trace(feedback_scores=fs)
    except Exception:
        pass


def run_under_opik_track(name: str, tags: Sequence[str], metadata: Mapping[str, object], work_fn):
    """Execute ``work_fn`` inside an explicit Opik @track context.

    Falls back to direct execution if Opik isn't installed. Mirrors the
    pattern previously inlined in benchmark scripts.
    """
    try:
        from opik import track  # type: ignore
    except Exception:
        return work_fn()
    decorated = track(
        name=name,
        tags=list(tags),
        metadata=dict(metadata or {}),
        project_name=os.environ.get("OPIK_PROJECT_NAME"),
    )(work_fn)
    return decorated()


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

@dataclass
class PreflightResult:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = False


@dataclass
class PreflightReport:
    results: list[PreflightResult] = field(default_factory=list)

    def append(self, result: PreflightResult) -> None:
        self.results.append(result)

    @property
    def ok(self) -> bool:
        return all(r.ok or not r.fatal for r in self.results)

    @property
    def fatal_failures(self) -> list[PreflightResult]:
        return [r for r in self.results if not r.ok and r.fatal]

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checks": [
                {"name": r.name, "ok": r.ok, "fatal": r.fatal, "detail": r.detail}
                for r in self.results
            ],
        }

    def print_table(self, *, file=sys.stdout) -> None:
        print(f"\n== preflight ({'OK' if self.ok else 'FAIL'}) ==", file=file)
        for r in self.results:
            mark = "✓" if r.ok else ("✗" if r.fatal else "!")
            print(f"  {mark} {r.name:35s} {r.detail}", file=file)


def _check_env(name: str, *, fatal: bool = True) -> PreflightResult:
    has = bool(os.environ.get(name))
    return PreflightResult(name=f"env:{name}", ok=has, detail="set" if has else "missing", fatal=fatal)


def _ping_openai_compatible(name: str, *, base_url: str | None, api_key_env: str, model: str,
                            force_temp_one: bool = False, strict: bool) -> PreflightResult:
    if not os.environ.get(api_key_env):
        return PreflightResult(f"{name}_ping", ok=False, detail=f"{api_key_env} missing", fatal=False)
    try:
        from openai import OpenAI  # type: ignore
        kwargs: dict = {"api_key": os.environ[api_key_env], "timeout": 20}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        call_kwargs: dict = {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ok"}],
        }
        if force_temp_one:
            call_kwargs["temperature"] = 1.0
        resp = client.chat.completions.create(**call_kwargs)
        finish = resp.choices[0].finish_reason if resp.choices else "?"
        return PreflightResult(f"{name}_ping", ok=True, detail=f"{model} finish={finish}", fatal=False)
    except Exception as exc:
        return PreflightResult(f"{name}_ping", ok=False,
                               detail=f"{type(exc).__name__}: {str(exc)[:80]}", fatal=strict)


def _check_moonshot_ping(*, strict: bool) -> PreflightResult:
    return _ping_openai_compatible(
        "moonshot",
        base_url="https://api.moonshot.ai/v1",
        api_key_env="MOONSHOT_API_KEY",
        model=os.environ.get("FINDER_LLM_MODEL", "kimi-k2.5"),
        force_temp_one=True,
        strict=strict,
    )


def _check_xai_ping(*, strict: bool) -> PreflightResult:
    return _ping_openai_compatible(
        "xai",
        base_url="https://api.x.ai/v1",
        api_key_env="XAI_API_KEY",
        model="grok-4-fast-non-reasoning",
        strict=strict,
    )


def _check_deepseek_ping(*, strict: bool) -> PreflightResult:
    return _ping_openai_compatible(
        "deepseek",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        model="deepseek-chat",
        strict=strict,
    )


def _check_openai_ping(*, strict: bool) -> PreflightResult:
    if not os.environ.get("OPENAI_API_KEY"):
        return PreflightResult("openai_ping", ok=False, detail="OPENAI_API_KEY missing", fatal=False)
    try:
        from openai import OpenAI  # type: ignore
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=20)
        client.embeddings.create(model="text-embedding-3-small", input="x")
        return PreflightResult("openai_ping", ok=True, detail="embedding ok", fatal=False)
    except Exception as exc:
        return PreflightResult("openai_ping", ok=False, detail=f"{type(exc).__name__}: {str(exc)[:80]}", fatal=strict)


def _check_opik(*, strict: bool) -> PreflightResult:
    if not os.environ.get("OPIK_API_KEY"):
        return PreflightResult("opik_workspace", ok=False, detail="OPIK_API_KEY missing", fatal=False)
    workspaces = _fetch_opik_workspaces()
    if not workspaces:
        return PreflightResult("opik_workspace", ok=False, detail="REST workspaces fetch failed", fatal=False)
    ws = os.environ.get("OPIK_WORKSPACE", "")
    if ws and ws not in workspaces:
        return PreflightResult(
            "opik_workspace",
            ok=False,
            detail=f"{ws!r} not in {workspaces}",
            fatal=False,
        )
    return PreflightResult("opik_workspace", ok=True, detail=f"{ws} ({len(workspaces)} workspaces)", fatal=False)


def _check_neo4j(*, strict: bool) -> PreflightResult:
    uri = os.environ.get("NEO4J_URI") or os.environ.get("BOLT_URL")
    if not uri:
        return PreflightResult("neo4j_connect", ok=False, detail="NEO4J_URI missing", fatal=False)
    try:
        from neo4j import GraphDatabase  # type: ignore
        user = os.environ.get("NEO4J_USER", "neo4j")
        pwd = os.environ.get("NEO4J_PASSWORD", "")
        with GraphDatabase.driver(uri, auth=(user, pwd)) as drv:
            drv.verify_connectivity()
        return PreflightResult("neo4j_connect", ok=True, detail=f"{uri}", fatal=False)
    except Exception as exc:
        return PreflightResult("neo4j_connect", ok=False, detail=f"{type(exc).__name__}: {str(exc)[:80]}", fatal=strict)


def _check_lbug_dir(*, strict: bool) -> PreflightResult:
    DEFAULT_LBUG_DIR.mkdir(parents=True, exist_ok=True)
    if not os.access(DEFAULT_LBUG_DIR, os.W_OK):
        return PreflightResult("lbug_dir_writable", ok=False, detail=str(DEFAULT_LBUG_DIR), fatal=strict)
    free_gb = shutil.disk_usage(DEFAULT_LBUG_DIR).free / (1 << 30)
    return PreflightResult(
        "lbug_dir_writable",
        ok=True,
        detail=f"{DEFAULT_LBUG_DIR} free={free_gb:.1f}GB",
        fatal=False,
    )


def _check_slices_csv(*, strict: bool, min_rows: int = 100) -> PreflightResult:
    csv = REPO_ROOT / ".seocho/datasets/finder/slices/all_slices.csv"
    if not csv.is_file():
        return PreflightResult("slices_csv", ok=False, detail=f"missing {csv}", fatal=strict)
    try:
        import pandas as pd  # noqa: PLC0415
        df = pd.read_csv(csv, usecols=["_id"], nrows=10_000)
        n = len(df)
        ok = n >= min_rows
        return PreflightResult("slices_csv", ok=ok, detail=f"{n} rows", fatal=strict and not ok)
    except Exception as exc:
        return PreflightResult("slices_csv", ok=False, detail=f"{type(exc).__name__}: {str(exc)[:80]}", fatal=strict)


def preflight(
    *,
    strict: bool = True,
    require_moonshot: bool = True,
    require_openai_embed: bool = False,
    require_neo4j: bool = False,
    require_opik: bool = False,
    require_slices: bool = True,
    require_xai: bool = False,
    require_deepseek: bool = False,
    require_openai_chat: bool = False,
) -> PreflightReport:
    """Run all external-dependency connectivity checks.

    Returns a structured report. Callers decide whether to abort based on
    ``report.ok``. Set the ``require_*`` flags to mark specific checks as
    fatal for the calling step.
    """
    report = PreflightReport()
    # Required env vars first (cheapest)
    report.append(_check_env("MOONSHOT_API_KEY", fatal=strict and require_moonshot))
    report.append(_check_env("OPIK_API_KEY", fatal=False))
    report.append(_check_env("OPIK_WORKSPACE", fatal=False))
    report.append(_check_env("OPIK_PROJECT_NAME", fatal=False))
    # Connectivity / behavioral
    if require_moonshot:
        report.append(_check_moonshot_ping(strict=strict))
    if require_openai_embed:
        report.append(_check_openai_ping(strict=strict))
    if require_openai_chat:
        report.append(_ping_openai_compatible(
            "openai_chat", base_url=None, api_key_env="OPENAI_API_KEY",
            model="gpt-4o-mini", strict=strict))
    if require_xai:
        report.append(_check_xai_ping(strict=strict))
    if require_deepseek:
        report.append(_check_deepseek_ping(strict=strict))
    if require_opik:
        report.append(_check_opik(strict=strict))
    if require_neo4j:
        report.append(_check_neo4j(strict=strict))
    if require_slices:
        report.append(_check_slices_csv(strict=strict))
    report.append(_check_lbug_dir(strict=strict))
    return report


# ---------------------------------------------------------------------------
# Bootstrap helper — load env + apply normalizations in one call
# ---------------------------------------------------------------------------

def bootstrap(
    *,
    env_files: Iterable[Path] = DEFAULT_ENV_FILES,
    seed: int = 42,
    verbose: bool = False,
) -> EnvReport:
    """Single entrypoint scripts call at startup:

    1. Load .env files (upstream first, then repo-local)
    2. Alias OPIK_PROJECT → OPIK_PROJECT_NAME, swap workspace/project if needed
    3. Lower-case NEO4J_USER
    4. Set global determinism (random/numpy)
    """
    report = load_env_files(env_files)
    alias_opik_project(verbose=verbose)
    normalize_neo4j_user()
    set_global_determinism(seed)
    if verbose:
        print(f"[bootstrap] loaded env from {report.loaded_files}, set {len(report.set_keys)} keys", flush=True)
    return report
