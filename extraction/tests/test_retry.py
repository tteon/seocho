"""Tests for retry decorators."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock

from exceptions import OpenAIAPIError, Neo4jConnectionError
from retry_utils import openai_retry, neo4j_retry


class TestOpenAIRetry:
    def test_success_on_first_attempt(self):
        mock_fn = MagicMock(return_value="ok")
        decorated = openai_retry(mock_fn)
        result = decorated()
        assert result == "ok"
        assert mock_fn.call_count == 1

    def test_success_after_failure(self):
        mock_fn = MagicMock(
            side_effect=[OpenAIAPIError("rate limited"), "ok"]
        )
        decorated = openai_retry(mock_fn)
        result = decorated()
        assert result == "ok"
        assert mock_fn.call_count == 2

    def test_exhaustion_raises(self):
        mock_fn = MagicMock(
            side_effect=OpenAIAPIError("always fails")
        )
        decorated = openai_retry(mock_fn)
        with pytest.raises(OpenAIAPIError, match="always fails"):
            decorated()
        assert mock_fn.call_count == 3

    def test_non_retryable_passthrough(self):
        """Non-OpenAIAPIError should not be retried."""
        mock_fn = MagicMock(side_effect=ValueError("bad input"))
        decorated = openai_retry(mock_fn)
        with pytest.raises(ValueError, match="bad input"):
            decorated()
        assert mock_fn.call_count == 1


class TestNeo4jRetry:
    def test_success_on_first_attempt(self):
        mock_fn = MagicMock(return_value="ok")
        decorated = neo4j_retry(mock_fn)
        result = decorated()
        assert result == "ok"
        assert mock_fn.call_count == 1

    def test_success_after_failure(self):
        mock_fn = MagicMock(
            side_effect=[Neo4jConnectionError("conn refused"), "ok"]
        )
        decorated = neo4j_retry(mock_fn)
        result = decorated()
        assert result == "ok"
        assert mock_fn.call_count == 2

    def test_exhaustion_raises(self):
        mock_fn = MagicMock(
            side_effect=Neo4jConnectionError("always fails")
        )
        decorated = neo4j_retry(mock_fn)
        with pytest.raises(Neo4jConnectionError, match="always fails"):
            decorated()
        assert mock_fn.call_count == 3

    def test_non_retryable_passthrough(self):
        mock_fn = MagicMock(side_effect=RuntimeError("other error"))
        decorated = neo4j_retry(mock_fn)
        with pytest.raises(RuntimeError, match="other error"):
            decorated()
        assert mock_fn.call_count == 1
