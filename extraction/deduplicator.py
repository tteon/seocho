"""
Entity Deduplicator

Embedding-similarity based deduplication for extracted entities.
Detects semantic duplicates (e.g. "SpaceX" vs "Space Exploration
Technologies Corp") and merges them under a canonical ID.
"""

import logging
from typing import Dict, List, Tuple

import numpy as np

from vector_store import VectorStore

logger = logging.getLogger(__name__)


class EntityDeduplicator:
    """Embedding-similarity based entity deduplication."""

    def __init__(
        self,
        vector_store: VectorStore,
        similarity_threshold: float = 0.92,
    ):
        self.vector_store = vector_store
        self.similarity_threshold = similarity_threshold
        # entity name -> canonical id
        self._canonical_map: Dict[str, str] = {}
        # canonical id -> embedding (numpy array)
        self._canonical_embeddings: Dict[str, np.ndarray] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def deduplicate(self, extracted_data: Dict) -> Dict:
        """Apply deduplication to both nodes and relationships."""
        nodes = self.deduplicate_nodes(extracted_data.get("nodes", []))
        rels = self.deduplicate_relationships(extracted_data.get("relationships", []))
        return {"nodes": nodes, "relationships": rels}

    def deduplicate_nodes(self, nodes: List[Dict]) -> List[Dict]:
        """Detect semantic duplicates and assign canonical IDs.

        Strategy:
        1. Embed each entity name.
        2. Compare against existing canonical embeddings (cosine similarity).
        3. If above threshold → reuse canonical ID.
        4. Otherwise → register as new canonical entity.
        """
        deduped: List[Dict] = []
        seen_canonical_ids: set = set()

        for node in nodes:
            name = node.get("properties", {}).get("name", node.get("id", ""))
            node_id = node.get("id", "")

            # Fast path: exact match in canonical map
            if name in self._canonical_map:
                canonical_id = self._canonical_map[name]
                if canonical_id not in seen_canonical_ids:
                    node["id"] = canonical_id
                    deduped.append(node)
                    seen_canonical_ids.add(canonical_id)
                continue

            # Generate embedding
            embedding = np.array(
                self.vector_store.embed_text(name), dtype="float32"
            )

            # Compare against all canonical embeddings
            canonical_id, similarity = self._find_best_match(embedding)

            if canonical_id is not None and similarity >= self.similarity_threshold:
                # Merge: reuse existing canonical ID
                self._canonical_map[name] = canonical_id
                logger.info(
                    "Dedup merge: '%s' -> canonical '%s' (sim=%.3f)",
                    name,
                    canonical_id,
                    similarity,
                )
                if canonical_id not in seen_canonical_ids:
                    node["id"] = canonical_id
                    deduped.append(node)
                    seen_canonical_ids.add(canonical_id)
            else:
                # New canonical entity
                self._canonical_map[name] = node_id
                self._canonical_embeddings[node_id] = embedding
                if node_id not in seen_canonical_ids:
                    deduped.append(node)
                    seen_canonical_ids.add(node_id)

        return deduped

    def deduplicate_relationships(self, relationships: List[Dict]) -> List[Dict]:
        """Remap relationship source/target to canonical IDs and remove duplicates."""
        seen: set = set()
        deduped: List[Dict] = []

        for rel in relationships:
            source = self._canonical_map.get(rel.get("source", ""), rel.get("source", ""))
            target = self._canonical_map.get(rel.get("target", ""), rel.get("target", ""))
            rel_type = rel.get("type", "RELATED_TO")

            dedup_key = (source, target, rel_type)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            rel["source"] = source
            rel["target"] = target
            deduped.append(rel)

        return deduped

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_best_match(
        self, embedding: np.ndarray
    ) -> Tuple[str, float]:
        """Find the closest canonical embedding by cosine similarity.

        Returns (canonical_id, similarity) or (None, 0.0) if no candidates.
        """
        if not self._canonical_embeddings:
            return None, 0.0

        best_id = None
        best_sim = 0.0

        for cid, cemb in self._canonical_embeddings.items():
            sim = self._cosine_similarity(embedding, cemb)
            if sim > best_sim:
                best_sim = sim
                best_id = cid

        return best_id, best_sim

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
