"""
Specialized agents for custom interactive chat platform orchestration.

BackendSpecialistAgent:
- orchestrates execution mode dispatch (router/debate/semantic)

FrontendSpecialistAgent:
- shapes runtime output into UI-friendly cards, trace summary, and entity candidates
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional


class PlatformSessionStore:
    """In-memory session store for platform chat."""

    def __init__(self, max_turns: int = 100):
        self.max_turns = max_turns
        self._lock = threading.Lock()
        self._sessions: Dict[str, List[Dict[str, Any]]] = {}

    def append(self, session_id: str, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            history = self._sessions.setdefault(session_id, [])
            history.append(
                {
                    "role": role,
                    "content": content,
                    "metadata": metadata or {},
                }
            )
            if len(history) > self.max_turns:
                del history[: len(history) - self.max_turns]

    def get(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._sessions.get(session_id, []))

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


class BackendSpecialistAgent:
    """Backend execution orchestrator for platform modes."""

    async def execute(
        self,
        mode: str,
        router_runner,
        debate_runner,
        semantic_runner,
        request_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        if mode == "debate":
            debate_payload = self._to_payload(await debate_runner(request_payload))
            if debate_payload.get("debate_state") == "blocked":
                semantic_payload = self._to_payload(await semantic_runner(request_payload))
                semantic_payload["runtime_control"] = {
                    "requested_mode": "debate",
                    "executed_mode": "semantic",
                    "reason": "debate_blocked",
                }
                semantic_payload["fallback_from"] = {
                    "mode": "debate",
                    "debate_state": "blocked",
                    "agent_statuses": debate_payload.get("agent_statuses", []),
                }
                return semantic_payload
            return debate_payload
        if mode == "router":
            result = await router_runner(request_payload)
            return self._to_payload(result)
        result = await semantic_runner(request_payload)
        return self._to_payload(result)

    @staticmethod
    def _to_payload(result: Any) -> Dict[str, Any]:
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if isinstance(result, dict):
            return result
        return {"response": str(result), "trace_steps": []}


class FrontendSpecialistAgent:
    """Frontend payload formatter."""

    def build_ui_payload(self, mode: str, runtime_payload: Dict[str, Any]) -> Dict[str, Any]:
        trace_steps = runtime_payload.get("trace_steps", [])
        trace_count_by_type: Dict[str, int] = {}
        for step in trace_steps:
            step_type = str(step.get("type", "UNKNOWN"))
            trace_count_by_type[step_type] = trace_count_by_type.get(step_type, 0) + 1

        cards = [
            {
                "kind": "summary",
                "title": f"Mode: {mode}",
                "body": runtime_payload.get("response", ""),
            },
            {
                "kind": "trace",
                "title": "Trace Steps",
                "body": f"{len(trace_steps)} steps",
            },
        ]

        entity_candidates = self._extract_entity_candidates(runtime_payload)

        return {
            "cards": cards,
            "trace_summary": trace_count_by_type,
            "entity_candidates": entity_candidates,
        }

    @staticmethod
    def _extract_entity_candidates(runtime_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        semantic_context = runtime_payload.get("semantic_context", {})
        matches = semantic_context.get("matches", {})
        groups: List[Dict[str, Any]] = []
        for question_entity, candidates in matches.items():
            group_candidates = []
            for row in candidates:
                group_candidates.append(
                    {
                        "database": row.get("database"),
                        "node_id": row.get("node_id"),
                        "display_name": row.get("display_name"),
                        "labels": row.get("labels", []),
                        "score": row.get("final_score"),
                        "source": row.get("source"),
                    }
                )
            groups.append(
                {
                    "question_entity": question_entity,
                    "candidates": group_candidates,
                }
            )
        return groups
