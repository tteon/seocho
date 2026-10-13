from __future__ import annotations

import contextvars
from typing import Any, Callable
from uuid import uuid4

import httpx
from agents import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

_tags: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar('recorder_tags', default={})


class RecordedModel(Model):
    def __init__(self, model: Model, tags: dict[str, str], attempts: list[dict[str, str]]) -> None:
        self.model = model
        self.tags = tags
        self.attempts = attempts

    def for_run(self, run_id: str, condition_id: str = '') -> RecordedModel:
        return RecordedModel(self.model, {**self.tags, 'X-Run-Id': run_id,
                             'X-Condition-Id': condition_id}, self.attempts)

    def for_agent(self, role: str) -> RecordedModel:
        tags = {**self.tags, 'X-Agent-Id': role}
        tags['X-Record-Domain'] = 'evaluation' if role == 'Evaluator' else 'inference'
        if ':' in role:
            tags['X-Subtask-Id'] = role.split(':', 1)[1]
        elif '_task_' in role:
            tags['X-Subtask-Id'] = 'task_' + role.rsplit('_task_', 1)[1]
        return RecordedModel(self.model, tags, self.attempts)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        token = _tags.set({**self.tags, 'X-Step-Id': self.tags.get('X-Step-Id') or uuid4().hex})
        try:
            return await self.model.get_response(*args, **kwargs)
        finally:
            _tags.reset(token)

    async def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        token = _tags.set({**self.tags, 'X-Step-Id': self.tags.get('X-Step-Id') or uuid4().hex})
        try:
            async for item in self.model.stream_response(*args, **kwargs):
                yield item
        finally:
            _tags.reset(token)


def recorded_client(*, recorder_url: str, model: str, workspace_id: str, run_id: str,
                    tier: str, template_version: str, timeout: float = 120,
                    transport: httpx.AsyncBaseTransport | None = None,
                    admit: Callable[[httpx.Request], None] | None = None) -> tuple[RecordedModel, AsyncOpenAI]:
    from urllib.parse import urlsplit
    parsed = urlsplit(recorder_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('Explicit Recorder URL required')
    if not all([workspace_id, run_id, template_version]):
        raise ValueError('Recorder identity fields required')
    attempts: list[dict[str, str]] = []

    async def tag_request(request: httpx.Request) -> None:
        tags = _tags.get()
        if not tags.get('X-Agent-Id'):
            raise RuntimeError('Untagged SDK request blocked before dispatch')
        if admit:
            admit(request)
        call_id = uuid4().hex
        request.headers.update({**tags, 'X-Call-Id': call_id})
        attempts.append({'call_id': call_id, 'run_id': tags['X-Run-Id'],
                         'agent_id': tags['X-Agent-Id'], 'step_id': tags['X-Step-Id']})

    async def verify_response(response: httpx.Response) -> None:
        if response.headers.get('X-Call-Id') != response.request.headers.get('X-Call-Id'):
            raise RuntimeError('Recorder acknowledgement missing; bypass/storage failure detected')

    http = httpx.AsyncClient(transport=transport, event_hooks={'request': [tag_request],
                             'response': [verify_response]}, timeout=timeout)
    client = AsyncOpenAI(api_key='recorder-local', base_url=recorder_url, http_client=http,
                         max_retries=0, timeout=timeout)
    base_tags = {'X-Workspace-Id': workspace_id, 'X-Run-Id': run_id, 'X-Tier': tier,
                 'X-Template-Version': template_version}
    return RecordedModel(OpenAIChatCompletionsModel(model=model, openai_client=client), base_tags, attempts), client
