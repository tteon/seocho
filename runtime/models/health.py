from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel


class HealthComponent(BaseModel):
    name: str
    status: Literal["ready", "degraded", "blocked"]
    detail: str = ""


class HealthResponse(BaseModel):
    scope: Literal["runtime", "batch"]
    status: Literal["ready", "degraded", "blocked"]
    generated_at: str
    components: List[HealthComponent]
