"""
Experiment runner — compare different configurations side by side.

Run the same input through two different setups (ontology, model, chunk
size, etc.) and see a structured diff of the results.

Usage::

    from seocho.experiment import ExperimentRunner

    runner = ExperimentRunner(graph_store=store)
    result_a = runner.run(ontology=onto_v1, llm=llm_4o, text="...")
    result_b = runner.run(ontology=onto_v2, llm=llm_mini, text="...")
    diff = runner.compare(result_a, result_b)
    print(diff)

CLI::

    seocho compare \\
      --config-a schema_v1.jsonld --config-b schema_v2.jsonld \\
      --input "Samsung CEO Jay Y. Lee..."
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ontology import Ontology


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

        response = llm.complete(
            system=system, user=user,
            temperature=0.0,
            response_format={"type": "json_object"},
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
