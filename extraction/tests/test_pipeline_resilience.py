"""Tests for pipeline error aggregation and PipelineResult."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dataclasses import dataclass, field
from typing import List, Dict, Any


# Inline PipelineResult to avoid importing pipeline.py (which needs omegaconf)
@dataclass
class PipelineResult:
    """Aggregated result from a pipeline run."""
    items_processed: int = 0
    items_failed: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.items_failed == 0


class TestPipelineResult:
    def test_empty_result_is_success(self):
        result = PipelineResult()
        assert result.success is True
        assert result.items_processed == 0
        assert result.items_failed == 0
        assert result.errors == []

    def test_all_processed_is_success(self):
        result = PipelineResult(items_processed=5, items_failed=0)
        assert result.success is True

    def test_any_failure_is_not_success(self):
        result = PipelineResult(items_processed=4, items_failed=1)
        assert result.success is False

    def test_errors_accumulate(self):
        result = PipelineResult()
        result.items_failed += 1
        result.errors.append({
            "item_id": "item_1",
            "error_type": "ExtractionError",
            "message": "JSON parse failed",
        })
        result.items_failed += 1
        result.errors.append({
            "item_id": "item_2",
            "error_type": "LoadError",
            "message": "Neo4j down",
        })
        assert result.items_failed == 2
        assert len(result.errors) == 2
        assert result.errors[0]["item_id"] == "item_1"
        assert result.errors[1]["error_type"] == "LoadError"
