from __future__ import annotations

import re
import unicodedata
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evidence_id: str
    text: str
    source: str


class RetrievalBackend(Protocol):
    async def retrieve(self, question: str, *, top_k: int, workspace_id: str) -> list[Evidence]: ...


class ReferenceBackend(Protocol):
    async def fetch(self, question: str, *, workspace_id: str) -> list[Evidence]: ...


def normalized(text: str) -> str:
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text)).strip().casefold()


class GoldGuard:
    """Conservative exact/substring flag; suspicion does not prove contamination."""
    def __init__(self, gold_answers: list[str]) -> None:
        self._gold = [normalized(g) for g in gold_answers if normalized(g)]

    def check(self, evidence: list[Evidence]) -> None:
        for item in evidence:
            text = normalized(item.text)
            if any(gold in text for gold in self._gold):
                # Never include the matched gold or evidence text in inference diagnostics.
                raise ValueError('LEAK_SUSPECTED: reference delivery blocked')


class FixedInformation:
    """Frozen evidence fixture, deliberately no query-dependent ranking."""
    def __init__(self, rows: list[Evidence], workspace_id: str) -> None:
        self.rows = rows
        self.workspace_id = workspace_id

    def _check(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ValueError('Workspace mismatch')

    async def retrieve(self, question: str, *, top_k: int, workspace_id: str) -> list[Evidence]:
        self._check(workspace_id)
        return self.rows[:top_k]

    async def fetch(self, question: str, *, workspace_id: str) -> list[Evidence]:
        self._check(workspace_id)
        return list(self.rows)
