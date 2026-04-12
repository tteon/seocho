"""
Experiment workbench — explore parameter combinations to find optimal
extraction and query settings.

Quick start::

    from seocho.experiment import Workbench

    wb = Workbench(input_texts=["Samsung CEO Jay Y. Lee met NVIDIA's Jensen Huang."])
    wb.vary("ontology", ["schema_v1.jsonld", "schema_v2.jsonld"])
    wb.vary("model", ["gpt-4o", "gpt-4o-mini"])
    wb.vary("chunk_size", [4000, 8000])

    results = wb.run_all()              # 2 x 2 x 2 = 8 runs
    print(results.best_by("extraction_score"))
    print(results.leaderboard())
    results.save("./experiments/run_001")

Also includes the simpler pairwise compare from earlier::

    runner = ExperimentRunner()
    diff = runner.compare(result_a, result_b)
"""

from __future__ import annotations

import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from .ontology import Ontology

logger = logging.getLogger(__name__)


@dataclass
class ExperimentResult:
    """Result of a single experiment run."""

    config_name: str = ""
    ontology_name: str = ""
    model: str = ""
    input_text: str = ""
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    extraction_score: float = 0.0
    validation_errors: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    params: Dict[str, Any] = field(default_factory=dict)  # all varied parameters
    usage: Dict[str, int] = field(default_factory=dict)  # LLM token usage

    def to_dict(self) -> Dict[str, Any]:
        return {
            "config_name": self.config_name,
            "ontology_name": self.ontology_name,
            "model": self.model,
            "nodes_count": len(self.nodes),
            "relationships_count": len(self.relationships),
            "extraction_score": round(self.extraction_score, 3),
            "validation_errors": len(self.validation_errors),
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "params": self.params,
            "usage": self.usage,
        }


@dataclass
class ComparisonResult:
    """Side-by-side comparison of two experiment runs."""

    result_a: ExperimentResult
    result_b: ExperimentResult
    node_diff: Dict[str, Any] = field(default_factory=dict)
    relationship_diff: Dict[str, Any] = field(default_factory=dict)
    score_diff: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "a": self.result_a.to_dict(),
            "b": self.result_b.to_dict(),
            "diff": {
                "nodes": self.node_diff,
                "relationships": self.relationship_diff,
                "score_delta": round(self.score_diff, 3),
            },
        }

    def summary(self) -> str:
        """Human-readable summary."""
        a, b = self.result_a, self.result_b
        lines = [
            f"{'':30s} {'Config A':>15s}  {'Config B':>15s}  {'Delta':>10s}",
            f"{'─' * 75}",
            f"{'Ontology':30s} {a.ontology_name:>15s}  {b.ontology_name:>15s}",
            f"{'Model':30s} {a.model:>15s}  {b.model:>15s}",
            f"{'Nodes extracted':30s} {len(a.nodes):>15d}  {len(b.nodes):>15d}  {len(b.nodes) - len(a.nodes):>+10d}",
            f"{'Relationships extracted':30s} {len(a.relationships):>15d}  {len(b.relationships):>15d}  {len(b.relationships) - len(a.relationships):>+10d}",
            f"{'Extraction score':30s} {a.extraction_score:>15.1%}  {b.extraction_score:>15.1%}  {self.score_diff:>+10.1%}",
            f"{'Validation errors':30s} {len(a.validation_errors):>15d}  {len(b.validation_errors):>15d}",
            f"{'Time (seconds)':30s} {a.elapsed_seconds:>15.2f}  {b.elapsed_seconds:>15.2f}",
        ]

        if self.node_diff.get("only_in_a") or self.node_diff.get("only_in_b"):
            lines.append("")
            lines.append("Node differences:")
            for n in self.node_diff.get("only_in_a", []):
                lines.append(f"  - only in A: {n}")
            for n in self.node_diff.get("only_in_b", []):
                lines.append(f"  + only in B: {n}")

        return "\n".join(lines)


class ExperimentRunner:
    """Runs extraction experiments and compares results."""

    def __init__(self, graph_store: Optional[Any] = None) -> None:
        self.graph_store = graph_store

    def run(
        self,
        *,
        ontology: Ontology,
        llm: Any,
        text: str,
        config_name: str = "",
    ) -> ExperimentResult:
        """Run a single extraction experiment."""
        from .query.strategy import ExtractionStrategy

        start = time.time()

        strategy = ExtractionStrategy(ontology)
        system, user = strategy.render(text)

        # Kimi K2.5 only accepts temperature=1
        safe_temp = 0.0
        if hasattr(llm, 'model') and 'kimi' in getattr(llm, 'model', '').lower():
            safe_temp = 1.0

        try:
            response = llm.complete(
                system=system, user=user,
                temperature=safe_temp,
                response_format={"type": "json_object"},
            )
        except Exception:
            response = llm.complete(
                system=system + "\n\nReturn ONLY valid JSON.",
                user=user,
                temperature=safe_temp,
            )

        try:
            extracted = response.json()
        except (json.JSONDecodeError, ValueError):
            extracted = {"nodes": [], "relationships": []}

        nodes = extracted.get("nodes", [])
        rels = extracted.get("relationships", [])

        scores = ontology.score_extraction(extracted)
        errors = ontology.validate_with_shacl(extracted)

        elapsed = time.time() - start

        return ExperimentResult(
            config_name=config_name or ontology.name,
            ontology_name=ontology.name,
            model=getattr(llm, "model", "unknown"),
            input_text=text[:200],
            nodes=nodes,
            relationships=rels,
            extraction_score=scores.get("overall", 0.0),
            validation_errors=errors,
            elapsed_seconds=elapsed,
        )

    def compare(
        self,
        result_a: ExperimentResult,
        result_b: ExperimentResult,
    ) -> ComparisonResult:
        """Compare two experiment results."""
        # Node diff by label+name
        def node_key(n: Dict) -> str:
            return f"{n.get('label', '')}:{n.get('properties', {}).get('name', n.get('id', ''))}"

        a_keys = {node_key(n) for n in result_a.nodes}
        b_keys = {node_key(n) for n in result_b.nodes}

        node_diff = {
            "only_in_a": sorted(a_keys - b_keys),
            "only_in_b": sorted(b_keys - a_keys),
            "common": sorted(a_keys & b_keys),
        }

        # Relationship diff
        def rel_key(r: Dict) -> str:
            return f"{r.get('source', '')}-[{r.get('type', '')}]->{r.get('target', '')}"

        a_rels = {rel_key(r) for r in result_a.relationships}
        b_rels = {rel_key(r) for r in result_b.relationships}

        rel_diff = {
            "only_in_a": sorted(a_rels - b_rels),
            "only_in_b": sorted(b_rels - a_rels),
            "common": sorted(a_rels & b_rels),
        }

        return ComparisonResult(
            result_a=result_a,
            result_b=result_b,
            node_diff=node_diff,
            relationship_diff=rel_diff,
            score_diff=result_b.extraction_score - result_a.extraction_score,
        )


# ======================================================================
# Workbench — multi-axis parameter exploration
# ======================================================================


class WorkbenchResults:
    """Collection of experiment results with analysis methods."""

    def __init__(self, results: List[ExperimentResult]) -> None:
        self.results = results

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self):
        return iter(self.results)

    def best_by(self, metric: str = "extraction_score") -> ExperimentResult:
        """Return the result with the highest value for the given metric."""
        return max(self.results, key=lambda r: getattr(r, metric, 0))

    def worst_by(self, metric: str = "extraction_score") -> ExperimentResult:
        """Return the result with the lowest value for the given metric."""
        return min(self.results, key=lambda r: getattr(r, metric, 0))

    def sorted_by(self, metric: str = "extraction_score", reverse: bool = True) -> List[ExperimentResult]:
        """Return results sorted by metric."""
        return sorted(self.results, key=lambda r: getattr(r, metric, 0), reverse=reverse)

    def leaderboard(self, metric: str = "extraction_score", top_n: int = 10) -> str:
        """Human-readable leaderboard."""
        ranked = self.sorted_by(metric)[:top_n]
        lines = [
            f"{'#':>3s}  {'Score':>8s}  {'Nodes':>6s}  {'Rels':>5s}  {'Errors':>6s}  {'Time':>6s}  Config",
            f"{'─' * 70}",
        ]
        for i, r in enumerate(ranked, 1):
            params_str = " | ".join(f"{k}={v}" for k, v in r.params.items())
            lines.append(
                f"{i:3d}  {r.extraction_score:8.1%}  {len(r.nodes):6d}  "
                f"{len(r.relationships):5d}  {len(r.validation_errors):6d}  "
                f"{r.elapsed_seconds:5.1f}s  {params_str}"
            )
        return "\n".join(lines)

    def to_dicts(self) -> List[Dict[str, Any]]:
        """All results as list of dicts."""
        return [r.to_dict() for r in self.results]

    def to_dataframe(self) -> Any:
        """Convert to pandas DataFrame (requires pandas)."""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("to_dataframe() requires pandas: pip install pandas")

        rows = []
        for r in self.results:
            row = {
                "ontology": r.ontology_name,
                "model": r.model,
                "nodes": len(r.nodes),
                "relationships": len(r.relationships),
                "score": r.extraction_score,
                "errors": len(r.validation_errors),
                "time_s": r.elapsed_seconds,
                **r.params,
            }
            if r.usage:
                row["tokens"] = r.usage.get("total_tokens", 0)
            rows.append(row)
        return pd.DataFrame(rows)

    def save(self, path: Union[str, Path]) -> Path:
        """Save results to a directory."""
        return ExperimentRegistry.save(self, path)


class ExperimentRegistry:
    """Persists experiment results to disk."""

    @staticmethod
    def save(results: WorkbenchResults, path: Union[str, Path]) -> Path:
        out = Path(path)
        out.mkdir(parents=True, exist_ok=True)

        # Config + results
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_runs": len(results),
            "results": results.to_dicts(),
        }
        (out / "results.json").write_text(json.dumps(data, indent=2, default=str))

        # Best run
        if results.results:
            best = results.best_by("extraction_score")
            (out / "best_run.json").write_text(json.dumps(best.to_dict(), indent=2, default=str))

        # Human summary
        (out / "summary.md").write_text(
            f"# Experiment Results\n\n"
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"Total runs: {len(results)}\n\n"
            f"## Leaderboard\n\n```\n{results.leaderboard()}\n```\n"
        )

        return out

    @staticmethod
    def load(path: Union[str, Path]) -> WorkbenchResults:
        """Load results from a saved experiment directory."""
        results_file = Path(path) / "results.json"
        data = json.loads(results_file.read_text())
        results = []
        for r in data.get("results", []):
            results.append(ExperimentResult(
                config_name=r.get("config_name", ""),
                ontology_name=r.get("ontology_name", ""),
                model=r.get("model", ""),
                extraction_score=r.get("extraction_score", 0.0),
                elapsed_seconds=r.get("elapsed_seconds", 0.0),
                params=r.get("params", {}),
                usage=r.get("usage", {}),
            ))
        return WorkbenchResults(results)


class Workbench:
    """Multi-axis parameter exploration for extraction and query settings.

    Define axes to vary, then run all combinations::

        wb = Workbench(input_texts=["Samsung CEO..."])
        wb.vary("ontology", ["v1.jsonld", "v2.jsonld"])
        wb.vary("model", ["gpt-4o", "gpt-4o-mini"])
        wb.vary("chunk_size", [4000, 8000])
        wb.vary("temperature", [0.0, 0.2])
        wb.vary("strict_validation", [True, False])

        results = wb.run_all()  # 2*2*2*2*2 = 32 runs
        print(results.leaderboard())

    Axes you can vary:

    - ``ontology``: list of JSON-LD/YAML file paths or Ontology objects
    - ``model``: LLM model names
    - ``chunk_size``: max chars per chunk
    - ``temperature``: LLM temperature
    - ``strict_validation``: True/False
    - ``prompt_template``: custom system prompt strings
    - Any custom key (passed through to params)
    """

    BUILTIN_AXES = {"ontology", "model", "chunk_size", "temperature", "strict_validation", "prompt_template"}

    def __init__(
        self,
        input_texts: Optional[List[str]] = None,
        input_dir: Optional[str] = None,
    ) -> None:
        self._input_texts = input_texts or []
        self._input_dir = input_dir
        self._axes: Dict[str, List[Any]] = {}
        self._on_run: Optional[Callable] = None

        # Load input from directory if provided
        if input_dir and not input_texts:
            from pathlib import Path as P
            d = P(input_dir)
            for f in sorted(d.glob("*.txt")) + sorted(d.glob("*.md")):
                self._input_texts.append(f.read_text(encoding="utf-8", errors="replace"))

    def vary(self, axis: str, values: List[Any]) -> "Workbench":
        """Define an axis to vary.

        Parameters
        ----------
        axis:
            Parameter name (e.g. "ontology", "model", "chunk_size").
        values:
            List of values to try for this axis.

        Returns self for chaining.
        """
        self._axes[axis] = list(values)
        return self

    def on_run(self, callback: Callable) -> "Workbench":
        """Set a callback for each run: ``callback(run_index, total, params)``."""
        self._on_run = callback
        return self

    @property
    def total_combinations(self) -> int:
        """Number of runs that run_all() will execute."""
        if not self._axes:
            return 0
        count = 1
        for values in self._axes.values():
            count *= len(values)
        return count * max(len(self._input_texts), 1)

    def run_all(self, **kwargs: Any) -> WorkbenchResults:
        """Execute all parameter combinations and return results.

        Each combination is run on each input text.
        """
        if not self._axes:
            raise ValueError("No axes defined. Call wb.vary(...) first.")

        axis_names = list(self._axes.keys())
        axis_values = list(self._axes.values())
        combinations = list(itertools.product(*axis_values))
        total = len(combinations) * max(len(self._input_texts), 1)

        all_results: List[ExperimentResult] = []
        run_idx = 0

        for combo in combinations:
            params = dict(zip(axis_names, combo))

            for text in (self._input_texts or [""]):
                run_idx += 1
                if self._on_run:
                    self._on_run(run_idx, total, params)

                result = self._run_single(params, text, **kwargs)
                all_results.append(result)

        return WorkbenchResults(all_results)

    def _run_single(self, params: Dict[str, Any], text: str, **kwargs: Any) -> ExperimentResult:
        """Execute one parameter combination."""
        from .query.strategy import ExtractionStrategy

        # Resolve ontology
        ontology = self._resolve_ontology(params.get("ontology"))
        if ontology is None:
            return ExperimentResult(
                config_name="error",
                params=params,
                validation_errors=["No ontology provided or resolved"],
            )

        # Resolve LLM
        model = params.get("model", "gpt-4o")
        temperature = params.get("temperature", 0.0)
        llm = self._resolve_llm(model)

        # Resolve pipeline params
        chunk_size = params.get("chunk_size", 6000)
        strict = params.get("strict_validation", False)
        prompt_template = params.get("prompt_template")

        # Build extraction strategy with optional custom prompt
        from .query.strategy import PromptTemplate as PT
        pt = prompt_template if isinstance(prompt_template, PT) else None
        strategy = ExtractionStrategy(ontology, prompt_template=pt)

        # Chunk if needed
        from .index.pipeline import chunk_text
        chunks = chunk_text(text, max_chars=chunk_size) if text else [""]

        start = time.time()
        all_nodes: List[Dict] = []
        all_rels: List[Dict] = []
        total_usage: Dict[str, int] = {}

        for chunk in chunks:
            system, user = strategy.render(chunk)

            # Some models (e.g. kimi-k2.5) only accept temperature=1
            safe_temp = temperature
            if hasattr(llm, 'model') and 'kimi' in getattr(llm, 'model', '').lower():
                safe_temp = 1.0

            try:
                response = llm.complete(
                    system=system, user=user,
                    temperature=safe_temp,
                    response_format={"type": "json_object"},
                )
            except Exception:
                # Fallback: some models don't support response_format
                response = llm.complete(
                    system=system + "\n\nReturn ONLY valid JSON.",
                    user=user,
                    temperature=safe_temp,
                )

            # Track usage
            if response.usage:
                for k, v in response.usage.items():
                    total_usage[k] = total_usage.get(k, 0) + v

            try:
                extracted = response.json()
            except (json.JSONDecodeError, ValueError):
                continue

            all_nodes.extend(extracted.get("nodes", []))
            all_rels.extend(extracted.get("relationships", []))

        elapsed = time.time() - start

        # Score
        data = {"nodes": all_nodes, "relationships": all_rels}
        scores = ontology.score_extraction(data)
        errors = ontology.validate_with_shacl(data) if strict else []

        result = ExperimentResult(
            config_name=f"{ontology.name}/{model}",
            ontology_name=ontology.name,
            model=model,
            input_text=text[:100],
            nodes=all_nodes,
            relationships=all_rels,
            extraction_score=scores.get("overall", 0.0),
            validation_errors=errors,
            elapsed_seconds=elapsed,
            params=params,
            usage=total_usage,
        )

        # --- Opik tracing ---
        try:
            from .tracing import log_experiment_run, is_tracing_enabled
            if is_tracing_enabled():
                log_experiment_run(
                    params=params,
                    score=result.extraction_score,
                    nodes_count=len(all_nodes),
                    relationships_count=len(all_rels),
                    elapsed_seconds=elapsed,
                    usage=total_usage,
                )
        except Exception:
            pass

        return result

    def _resolve_ontology(self, value: Any) -> Optional[Ontology]:
        """Resolve ontology from file path or object."""
        if isinstance(value, Ontology):
            return value
        if isinstance(value, (str, Path)):
            p = Path(value)
            if p.exists():
                if p.suffix in (".yaml", ".yml"):
                    return Ontology.from_yaml(p)
                return Ontology.from_jsonld(p)
        return None

    @staticmethod
    def _resolve_llm(model: str) -> Any:
        """Create LLM backend for a model name.

        Auto-detects provider from model name:
        - kimi* → KimiBackend (api.moonshot.ai)
        - deepseek* → DeepSeekBackend
        - grok* → GrokBackend
        - everything else → OpenAIBackend
        """
        from .store.llm import create_llm_backend

        model_lower = model.lower()
        if "kimi" in model_lower or "moonshot" in model_lower:
            return create_llm_backend(provider="kimi", model=model)
        if "deepseek" in model_lower:
            return create_llm_backend(provider="deepseek", model=model)
        if "grok" in model_lower:
            return create_llm_backend(provider="grok", model=model)
        return create_llm_backend(provider="openai", model=model)
