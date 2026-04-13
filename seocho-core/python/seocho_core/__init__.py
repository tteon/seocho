"""
seocho-core: Native acceleration for cosine similarity and rule inference.

Falls back to pure-Python implementations when the Rust extension is unavailable.
"""

try:
    from seocho_core._native import (
        cosine_similarity,
        cosine_similarity_matrix,
        infer_rules_from_nodes,
    )

    NATIVE_AVAILABLE = True
except ImportError:
    from seocho_core._fallback import (
        cosine_similarity,
        cosine_similarity_matrix,
        infer_rules_from_nodes,
    )

    NATIVE_AVAILABLE = False

__all__ = [
    "cosine_similarity",
    "cosine_similarity_matrix",
    "infer_rules_from_nodes",
    "NATIVE_AVAILABLE",
]
