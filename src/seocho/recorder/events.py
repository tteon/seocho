"""Explicit orchestration events; transport timestamps are never causal evidence."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    condition_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    kind: Literal['decision', 'realization']
    action: Literal['plan', 'route', 'fetch', 'solve', 'delegate', 'return', 'synthesize', 'evaluate', 'run', 'budget', 'intervention', 'verify_receipts', 'tool_call', 'review', 'coordinate']
    decision_id: str | None = None
    parent_step: str | None = None
    subtask_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    domain: Literal['inference', 'evaluation'] = 'inference'

    @model_validator(mode='after')
    def linked(self) -> AgentEvent:
        if self.kind == 'realization' and not self.decision_id:
            raise ValueError('Realization requires decision_id')
        if self.action == 'evaluate' and self.domain != 'evaluation':
            raise ValueError('Evaluation events require evaluation domain')
        return self
