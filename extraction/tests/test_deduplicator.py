"""Tests for EntityDeduplicator bounded cache."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock

# Mock heavy dependencies before any extraction imports
for mod in ["faiss", "openai", "opik", "opik.integrations", "opik.integrations.openai"]:
    sys.modules.setdefault(mod, MagicMock())

import numpy as np
from deduplicator import EntityDeduplicator, MAX_CANONICAL_EMBEDDINGS


class TestEntityDeduplicatorBoundedCache:
    def _make_deduplicator(self):
        mock_vs = MagicMock()
        call_count = [0]

        def fake_embed(text):
            # Return orthogonal-ish embeddings so cosine similarity < 0.99
            call_count[0] += 1
            emb = np.random.RandomState(call_count[0]).randn(1536).tolist()
            return emb

        mock_vs.embed_text.side_effect = fake_embed
        return EntityDeduplicator(vector_store=mock_vs, similarity_threshold=0.99)

    def test_cache_grows_with_unique_entities(self):
        dedup = self._make_deduplicator()
        nodes = [
            {"id": f"n{i}", "label": "Entity", "properties": {"name": f"entity_{i}"}}
            for i in range(10)
        ]
        dedup.deduplicate_nodes(nodes)
        assert len(dedup._canonical_embeddings) == 10

    def test_cache_bounded_at_max(self):
        dedup = self._make_deduplicator()
        count = MAX_CANONICAL_EMBEDDINGS + 5
        nodes = [
            {"id": f"n{i}", "label": "Entity", "properties": {"name": f"entity_{i}"}}
            for i in range(count)
        ]
        dedup.deduplicate_nodes(nodes)
        assert len(dedup._canonical_embeddings) <= MAX_CANONICAL_EMBEDDINGS

    def test_cosine_similarity(self):
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([1.0, 0.0, 0.0])
        assert EntityDeduplicator._cosine_similarity(a, b) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal(self):
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        assert EntityDeduplicator._cosine_similarity(a, b) == pytest.approx(0.0)

    def test_cosine_similarity_zero_vector(self):
        a = np.array([0.0, 0.0])
        b = np.array([1.0, 0.0])
        assert EntityDeduplicator._cosine_similarity(a, b) == 0.0
