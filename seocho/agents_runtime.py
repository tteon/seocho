"""
OpenAI Agents SDK adapter layer.

This module isolates direct SDK run/trace calls from business logic to
reduce runtime breakage when SDK signatures evolve.

Canonical location: ``seocho.agents_runtime``. The legacy import path
``extraction.agents_runtime`` still works via a re-export shim so existing
server-side code (``extraction/debate.py``, server tests) keeps compiling.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from typing import Any, Iterator

from agents import Runner, trace


class AgentsRuntimeAdapter:
    """Compatibility adapter for Agent SDK runtime calls."""

    def __init__(self, runner_cls=Runner, trace_fn=trace):
        self._runner_cls = runner_cls
        self._trace_fn = trace_fn
        self._runner_run = getattr(runner_cls, "run", None)
        self._runner_run_streamed = getattr(runner_cls, "run_streamed", None)
        self._primary_agent_param = self._detect_agent_parameter(self._runner_run)

    @staticmethod
    def _detect_agent_parameter(run_callable: Any) -> str:
        """Resolve runner agent parameter name across SDK versions."""
        if run_callable is None:
            return "starting_agent"

        try:
            params = inspect.signature(run_callable).parameters
        except (TypeError, ValueError):
            return "starting_agent"

        if "starting_agent" in params:
            return "starting_agent"
        if "agent" in params:
            return "agent"
        return "starting_agent"

    @staticmethod
    def _is_parameter_mismatch(exc: TypeError, parameter_name: str) -> bool:
        message = str(exc)
        return "unexpected keyword argument" in message and parameter_name in message

    async def run(self, *, agent: Any, input: str, context: Any = None):
        """Execute an agent run with signature compatibility fallback."""
        if self._runner_run is None:
            raise RuntimeError("Agents Runner.run is unavailable.")

        candidate_params = [self._primary_agent_param]
        alt = "agent" if self._primary_agent_param == "starting_agent" else "starting_agent"
        candidate_params.append(alt)

        last_error: TypeError | None = None
        for param_name in candidate_params:
            kwargs: dict[str, Any] = {param_name: agent, "input": input}
            if context is not None:
                kwargs["context"] = context
            try:
                return await self._runner_run(**kwargs)
            except TypeError as exc:
                if not self._is_parameter_mismatch(exc, param_name):
                    raise
                last_error = exc

        if last_error is not None:
            raise last_error
        raise RuntimeError("Failed to execute agent run due to unknown SDK signature mismatch.")

    def run_streamed(self, *, agent: Any, input: str, context: Any = None) -> Any:
        """Execute a streaming agent run with signature compatibility fallback.

        Returns the streaming result object whose ``stream_events()`` async
        iterator yields run events. Callers consume it the same way they
        would consume ``Runner.run_streamed`` directly.
        """
        if self._runner_run_streamed is None:
            raise RuntimeError("Agents Runner.run_streamed is unavailable.")

        candidate_params = [self._primary_agent_param]
        alt = "agent" if self._primary_agent_param == "starting_agent" else "starting_agent"
        candidate_params.append(alt)

        last_error: TypeError | None = None
        for param_name in candidate_params:
            kwargs: dict[str, Any] = {param_name: agent, "input": input}
            if context is not None:
                kwargs["context"] = context
            try:
                return self._runner_run_streamed(**kwargs)
            except TypeError as exc:
                if not self._is_parameter_mismatch(exc, param_name):
                    raise
                last_error = exc

        if last_error is not None:
            raise last_error
        raise RuntimeError("Failed to execute streaming agent run due to unknown SDK signature mismatch.")

    @contextmanager
    def trace(self, name: str) -> Iterator[None]:
        with self._trace_fn(name):
            yield


_DEFAULT_RUNTIME = AgentsRuntimeAdapter()


def get_agents_runtime() -> AgentsRuntimeAdapter:
    return _DEFAULT_RUNTIME
