"""Offline encoder inputs with immutable, explicit vector/model provenance."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .information import normalized


class PrecomputedEncoder:
    """Consume exported local model embeddings; no download or API inference."""
    def __init__(self, path: Path) -> None:
        raw = path.read_bytes()
        data = json.loads(raw)
        if not data.get('model') or not data.get('revision') or data.get('kind') not in ('semantic','lexical'):
            raise ValueError('Vector file requires model, revision and explicit kind')
        self.identity = f"{data['model']}@{data['revision']}:sha256:{hashlib.sha256(raw).hexdigest()}"
        self.semantic = data['kind']=='semantic'
        self.vectors: dict[str,list[float]] = {}
        dimension = None
        for row in data['vectors']:
            vector = [float(v) for v in row['vector']]
            if not vector or not all(math.isfinite(v) for v in vector) or not any(vector):
                raise ValueError('Finite nonzero vectors required')
            if dimension is not None and len(vector)!=dimension:
                raise ValueError('Vector dimensions differ')
            dimension = len(vector)
            key = normalized(row['text'])
            if key in self.vectors and self.vectors[key]!=vector:
                raise ValueError('Conflicting normalized text embeddings')
            self.vectors[key] = vector
        if not self.vectors:
            raise ValueError('Embedding file is empty')

    def encode(self, text: str) -> list[float]:
        try:
            return self.vectors[normalized(text)]
        except KeyError:
            raise ValueError('Precomputed embedding missing; refusing silent lexical fallback') from None
