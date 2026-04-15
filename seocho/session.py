"""
Session — agent-level SDK interface with context and tracing.

A Session maintains conversation state across ``add()`` and ``ask()`` calls.
Instead of independent chat completions, each operation runs through an
agent with tool use, with explicit fallback to the canonical local engine
when the agent path is unavailable. All operations within a session roll up
into a single parent trace.

Usage::

    from seocho import Seocho, Ontology
    from seocho.store import Neo4jGraphStore, OpenAIBackend

    s = Seocho(ontology=onto, graph_store=store, llm=llm)

    # Agent-level session (recommended)
    session = s.session("finance_analysis")
    session.add("NVIDIA revenue was $26.9B in 2024...")
    session.add("Apple CEO Tim Cook announced...")
    answer = session.ask("Compare NVIDIA and Apple revenue")
    session.close()

    # Session as context manager
    with s.session("research") as session:
        session.add("...")
        answer = session.ask("...")
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence

from .agent.context import SessionContext
from .agent.contracts import normalize_execution_mode
from .agent.factory import (
    create_indexing_agent,
    create_query_agent,
    create_supervisor_agent,
)

logger = logging.getLogger(__name__)


class Session:
    """Agent-level session with context persistence and tracing.

    Each call to ``add()`` runs an IndexingAgent that decides how to
    extract, validate, and write. Each call to ``ask()`` runs a
    QueryAgent that builds and executes queries. When the agent path
    fails, the session falls back to the canonical local engine used by
    ``Seocho`` and records the degraded path in trace/context metadata.

    Parameters
    ----------
    name:
        Session name for identification and tracing.
    ontology:
        The Ontology driving extraction and querying.
    graph_store:
        GraphStore for Neo4j/DozerDB.
    llm:
        LLMBackend for agent reasoning.
    vector_store:
        Optional VectorStore for similarity search.
    database:
        Default target database.
    extraction_prompt:
        Optional custom PromptTemplate.
    agent_config:
        Optional AgentConfig for quality thresholds.
    """

    def __init__(
        self,
        *,
        name: str = "",
        ontology: Any,
        graph_store: Any,
        llm: Any,
        vector_store: Any = None,
        database: str = "neo4j",
        extraction_prompt: Any = None,
        agent_config: Any = None,
        workspace_id: str = "default",
        ontology_profile: str = "default",
    ) -> None:
        self.session_id = str(uuid.uuid4())[:12]
        self.name = name or f"session-{self.session_id}"
        self.ontology = ontology
        self.graph_store = graph_store
        self.llm = llm
        self.vector_store = vector_store
        self.database = database
        self.extraction_prompt = extraction_prompt
        self.agent_config = agent_config
        self.workspace_id = workspace_id
        self.ontology_profile = str(ontology_profile or "default")

        self.context = SessionContext()
        from .ontology_context import OntologyContextCache

        self._ontology_context_cache = OntologyContextCache(max_size=8)
        self._ontology_context = self._ontology_context_cache.get(
            ontology,
            workspace_id=workspace_id,
            profile=self.ontology_profile,
        )
        self._trace = None
        self._closed = False

        # Start session trace
        try:
            from .tracing import begin_session, is_tracing_enabled
            if is_tracing_enabled():
                self._trace = begin_session(self.session_id, self.name)
        except Exception:
            pass

        # Lazy agent creation
        self._indexing_agent = None
        self._query_agent = None
        self._pipeline_engine = None

    def _get_indexing_agent(self) -> Any:
        """Create or return the indexing agent."""
        if self._indexing_agent is None:
            self._indexing_agent = create_indexing_agent(
                ontology=self.ontology,
                graph_store=self.graph_store,
                llm=self.llm,
                extraction_prompt=self.extraction_prompt,
            )
        return self._indexing_agent

    def _get_query_agent(self) -> Any:
        """Create or return the query agent."""
        if self._query_agent is None:
            self._query_agent = create_query_agent(
                ontology=self.ontology,
                graph_store=self.graph_store,
                llm=self.llm,
                vector_store=self.vector_store,
            )
        return self._query_agent

    def _get_pipeline_engine(self) -> Any:
        """Create or return the canonical local engine fallback."""
        if self._pipeline_engine is None:
            from .client import _LocalEngine

            self._pipeline_engine = _LocalEngine(
                ontology=self.ontology,
                graph_store=self.graph_store,
                llm=self.llm,
                workspace_id=self.workspace_id,
                extraction_prompt=self.extraction_prompt,
                agent_config=self.agent_config,
                ontology_profile=self.ontology_profile,
            )
        return self._pipeline_engine

    @property
    def _execution_mode(self) -> str:
        """Resolve execution mode from agent_config."""
        if self.agent_config is not None:
            return normalize_execution_mode(getattr(self.agent_config, 'execution_mode', 'pipeline'))
        return "pipeline"

    def add(
        self,
        content: str,
        *,
        database: Optional[str] = None,
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Index content into the knowledge graph.

        Execution depends on ``agent_config.execution_mode``:

        - ``"pipeline"`` (default) — deterministic pipeline, no LLM reasoning about flow
        - ``"agent"`` — LLM agent with tool use (extract/validate/score/write)
        - ``"supervisor"`` — supervisor routes automatically

        Parameters
        ----------
        content:
            The text to index.
        database:
            Target database (defaults to session default).
        category:
            Document category for prompt selection.
        metadata:
            Additional metadata.

        Returns
        -------
        Dict with source_id, nodes_created, relationships_created, etc.
        """
        if self._closed:
            raise RuntimeError("Session is closed")

        db = database or self.database
        start = time.time()

        mode = self._execution_mode
        if mode == "agent":
            result = self._add_via_agent(content, db, category, metadata)
        else:
            # "pipeline" and "supervisor" both use deterministic pipeline for add()
            result = self._add_via_pipeline(content, db, category, metadata)

        elapsed = time.time() - start

        # Update context
        source_id = result.get("source_id", "")
        nodes = result.get("nodes_created", 0)
        rels = result.get("relationships_created", 0)
        self.context.add_indexing(
            source_id,
            nodes,
            rels,
            content,
            mode=str(result.get("mode", "agent") or "agent"),
            degraded=bool(result.get("degraded", False)),
            fallback_from=str(result.get("fallback_from", "")),
            fallback_reason=str(result.get("fallback_reason", "")),
        )

        # Register extracted entities/relationships in structured cache
        if "extracted_nodes" in result:
            self.context.register_entities(result["extracted_nodes"], source_id, db)
        if "extracted_relationships" in result:
            self.context.register_relationships(result["extracted_relationships"], source_id)

        # Trace
        if self._trace:
            self._trace.log_span(
                "session.add",
                input_data={"text_preview": content[:200], "database": db, "category": category},
                output_data={
                    "source_id": source_id,
                    "nodes": nodes,
                    "relationships": rels,
                    "mode": str(result.get("mode", "agent") or "agent"),
                },
                metadata={
                    "elapsed_seconds": round(elapsed, 2),
                    "degraded": bool(result.get("degraded", False)),
                    "fallback_from": str(result.get("fallback_from", "")),
                    "fallback_reason": str(result.get("fallback_reason", "")),
                    "ontology_context": result.get(
                        "ontology_context",
                        self._ontology_context.metadata(usage="agent_indexing"),
                    ),
                },
                tags=["indexing"],
            )

        return result

    def run(
        self,
        message: str,
        *,
        database: Optional[str] = None,
    ) -> str:
        """Send a message through the supervisor agent (hand-off mode).

        Requires ``execution_mode="supervisor"`` and ``handoff=True``
        in :class:`~seocho.agent_config.AgentConfig`.  The supervisor
        routes to IndexingAgent or QueryAgent based on the message.

        Parameters
        ----------
        message:
            Any natural-language message.
        database:
            Target database.

        Raises
        ------
        RuntimeError
            If handoff is not enabled in the agent config.

        Example::

            config = AgentConfig(execution_mode="supervisor", handoff=True)
            s = Seocho(ontology=onto, graph_store=store, llm=llm, agent_config=config)

            with s.session("analysis") as sess:
                sess.run("Samsung CEO is Jay Y. Lee")  # → IndexingAgent
                answer = sess.run("Who is Samsung's CEO?")  # → QueryAgent
        """
        if self._closed:
            raise RuntimeError("Session is closed")

        # Explicit check — handoff must be opted in
        cfg = self.agent_config
        handoff_enabled = (
            cfg is not None
            and normalize_execution_mode(getattr(cfg, 'execution_mode', 'pipeline')) == 'supervisor'
            and getattr(cfg, 'handoff', False)
        )
        if not handoff_enabled:
            raise RuntimeError(
                "run() requires explicit opt-in: "
                "AgentConfig(execution_mode='supervisor', handoff=True). "
                "Use add() for indexing and ask() for querying, "
                "or set the 'supervisor' preset."
            )

        db = database or self.database
        start = time.time()

        # Pass structured context (entities/relationships), not full history
        agent_ctx = self.context.to_agent_context(ontology=self.ontology)
        agent_ctx = "\n\n".join(
            item for item in [self._ontology_context.agent_context, agent_ctx] if item
        )
        context_block = f"\n\n{agent_ctx}" if agent_ctx else ""

        full_message = f"{message}{context_block}\n[Target database: {db}]"
        result_text = self._run_via_supervisor(full_message, db)

        elapsed = time.time() - start

        if self._trace:
            self._trace.log_span(
                "session.run",
                input_data={"message": message[:200], "database": db},
                output_data={"response_preview": result_text[:300]},
                metadata={
                    "elapsed_seconds": round(elapsed, 2),
                    "ontology_context": self._ontology_context.metadata(usage="agent"),
                },
                tags=["supervisor", "handoff"],
            )

        return result_text

    def _run_via_supervisor(self, message: str, database: str) -> str:
        """Run through supervisor agent with hand-off."""
        from agents import Runner

        supervisor = self._get_supervisor_agent()
        result = asyncio.run(Runner.run(supervisor, message))
        return result.final_output or "No response from agent."

    def _get_supervisor_agent(self) -> Any:
        """Create or return the supervisor agent."""
        if not hasattr(self, '_supervisor_agent') or self._supervisor_agent is None:
            policy = getattr(self.agent_config, 'routing_policy', None) if self.agent_config else None
            self._supervisor_agent = create_supervisor_agent(
                ontology=self.ontology,
                graph_store=self.graph_store,
                llm=self.llm,
                vector_store=self.vector_store,
                extraction_prompt=self.extraction_prompt,
                routing_policy=policy,
            )
        return self._supervisor_agent

    def ask(
        self,
        question: str,
        *,
        database: Optional[str] = None,
        reasoning_mode: bool = True,
    ) -> str:
        """Ask a question through the query agent.

        The agent builds intent, calls text2cypher, executes, and
        synthesizes an answer. If results are empty, the agent retries
        with broader queries.

        Parameters
        ----------
        question:
            Natural-language question.
        database:
            Target database.
        reasoning_mode:
            Enable automatic query repair.
        Returns
        -------
        The synthesized answer string.
        """
        if self._closed:
            raise RuntimeError("Session is closed")

        db = database or self.database

        # Check query cache first
        cached = self.context.get_cached_answer(question)
        if cached is not None:
            self.context.add_query(question, cached, mode="cache")
            return cached

        start = time.time()

        # Pass structured context (entities, not history)
        agent_ctx = self.context.to_agent_context(ontology=self.ontology)
        agent_ctx = "\n\n".join(
            item for item in [self._ontology_context.agent_context, agent_ctx] if item
        )
        context_msg = f"\n\n{agent_ctx}\n[Target database: {db}]" if agent_ctx else ""

        mode = self._execution_mode
        if mode == "agent":
            query_result = self._ask_via_agent(question + context_msg, db)
        else:
            query_result = self._ask_via_pipeline(question, db, reasoning_mode)

        answer = str(query_result.get("answer", "") or "")

        elapsed = time.time() - start
        self.context.add_query(
            question,
            answer,
            mode=str(query_result.get("mode", "agent") or "agent"),
            degraded=bool(query_result.get("degraded", False)),
            fallback_from=str(query_result.get("fallback_from", "")),
            fallback_reason=str(query_result.get("fallback_reason", "")),
        )
        self.context.cache_query(question, answer)

        # Trace
        if self._trace:
            self._trace.log_span(
                "session.ask",
                input_data={"question": question, "database": db},
                output_data={
                    "answer_preview": answer[:300],
                    "mode": str(query_result.get("mode", "agent") or "agent"),
                },
                metadata={
                    "elapsed_seconds": round(elapsed, 2),
                    "degraded": bool(query_result.get("degraded", False)),
                    "fallback_from": str(query_result.get("fallback_from", "")),
                    "fallback_reason": str(query_result.get("fallback_reason", "")),
                    "ontology_context": self._ontology_context.metadata(usage="agent_query"),
                },
                tags=["query"],
            )

        return answer

    def _add_via_agent(
        self,
        content: str,
        database: str,
        category: str,
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Run indexing through the agent with tool use."""
        from agents import Runner

        agent = self._get_indexing_agent()
        user_msg = (
            f"Index this text into database '{database}' with category '{category}'.\n\n"
            f"Text:\n{content}"
        )
        if metadata:
            user_msg += f"\n\nMetadata: {json.dumps(metadata, default=str)}"
        user_msg += f"\n\n{self._ontology_context.agent_context}"

        try:
            result = asyncio.run(Runner.run(agent, user_msg))
            # Parse agent's final output for structured result
            parsed = self._parse_indexing_result(result.final_output, content)
            parsed["ontology_context"] = self._ontology_context.metadata(usage="agent_indexing")
            return parsed
        except Exception as exc:
            logger.warning("Agent indexing failed, falling back to pipeline: %s", exc)
            fallback = self._add_via_pipeline(content, database, category, metadata)
            fallback["degraded"] = True
            fallback["fallback_from"] = "agent"
            fallback["fallback_reason"] = str(exc)
            return fallback

    def _ask_via_agent(self, question: str, database: str) -> Dict[str, Any]:
        """Run query through the agent with tool use."""
        from agents import Runner

        agent = self._get_query_agent()
        user_msg = (
            f"Answer this question using database '{database}':\n\n"
            f"{question}"
        )

        try:
            result = asyncio.run(Runner.run(agent, user_msg))
            return {
                "answer": result.final_output or "No answer could be generated.",
                "mode": "agent",
                "degraded": False,
                "fallback_from": "",
                "fallback_reason": "",
                "ontology_context": self._ontology_context.metadata(usage="agent_query"),
            }
        except Exception as exc:
            logger.warning("Agent query failed, falling back to pipeline: %s", exc)
            fallback = self._ask_via_pipeline(question, database, True)
            fallback["degraded"] = True
            fallback["fallback_from"] = "agent"
            fallback["fallback_reason"] = str(exc)
            return fallback

    def _add_via_pipeline(
        self,
        content: str,
        database: str,
        category: str,
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Direct pipeline fallback (no agent reasoning)."""
        pipeline = self._get_pipeline_engine()

        # Single add() call — no separate extract() to avoid double LLM calls
        memory = pipeline.add(
            content,
            database=database,
            category=category,
            metadata=metadata,
        )

        # For context cache: query what was actually written to the graph
        extracted_nodes: List[Dict] = []
        extracted_rels: List[Dict] = []
        if memory.status == "active" and self.graph_store is not None:
            try:
                sid = memory.memory_id
                rows = self.graph_store.query(
                    "MATCH (n) WHERE n._source_id = $sid "
                    "RETURN labels(n)[0] AS label, n.name AS name, properties(n) AS props",
                    params={"sid": sid}, database=database,
                )
                for row in rows:
                    extracted_nodes.append({
                        "label": row.get("label", ""),
                        "properties": {k: v for k, v in (row.get("props") or {}).items()
                                       if not k.startswith("_") and k != "id"},
                    })
            except Exception:
                pass

        return {
            "source_id": memory.memory_id,
            "nodes_created": int(memory.metadata.get("nodes_created", 0) or 0),
            "relationships_created": int(memory.metadata.get("relationships_created", 0) or 0),
            "chunks_processed": int(memory.metadata.get("chunks_processed", 0) or 0),
            "validation_errors": list(memory.metadata.get("validation_errors", []) or []),
            "write_errors": list(memory.metadata.get("write_errors", []) or []),
            "ok": memory.status == "active",
            "mode": "pipeline",
            "degraded": False,
            "fallback_from": "",
            "fallback_reason": "",
            "ontology_context": dict(memory.metadata.get("ontology_context", {}) or {}),
            "extracted_nodes": extracted_nodes,
            "extracted_relationships": extracted_rels,
        }

    def _ask_via_pipeline(self, question: str, database: str, reasoning_mode: bool) -> Dict[str, Any]:
        """Direct pipeline fallback for querying."""
        pipeline = self._get_pipeline_engine()
        answer = pipeline.ask(
            question,
            database=database,
            reasoning_mode=reasoning_mode,
        )
        return {
            "answer": answer,
            "mode": "pipeline",
            "degraded": False,
            "fallback_from": "",
            "fallback_reason": "",
            "ontology_context": self._ontology_context.metadata(usage="pipeline_query"),
        }

    def _parse_indexing_result(self, agent_output: str, original_text: str) -> Dict[str, Any]:
        """Parse the agent's final output into a structured result."""
        import re

        result = {
            "source_id": str(uuid.uuid4()),
            "nodes_created": 0,
            "relationships_created": 0,
            "ok": True,
            "mode": "agent",
            "degraded": False,
            "fallback_from": "",
            "fallback_reason": "",
            "agent_response": agent_output,
        }

        if not agent_output:
            return result

        # Try to find JSON in the agent output (tool results often contain it)
        json_patterns = re.findall(r'\{[^{}]*"nodes_written"\s*:\s*(\d+)[^{}]*\}', agent_output)
        if json_patterns:
            result["nodes_created"] = int(json_patterns[-1])

        json_rels = re.findall(r'\{[^{}]*"relationships_written"\s*:\s*(\d+)[^{}]*\}', agent_output)
        if json_rels:
            result["relationships_created"] = int(json_rels[-1])

        # Fallback: look for natural language mentions
        if result["nodes_created"] == 0:
            nodes_match = re.search(r"(\d+)\s*node", agent_output, re.IGNORECASE)
            if nodes_match:
                result["nodes_created"] = int(nodes_match.group(1))

        if result["relationships_created"] == 0:
            rels_match = re.search(r"(\d+)\s*(?:relationship|edge|rel)", agent_output, re.IGNORECASE)
            if rels_match:
                result["relationships_created"] = int(rels_match.group(1))

        # Check for error indicators
        if "error" in agent_output.lower() and result["nodes_created"] == 0:
            result["ok"] = False

        return result

    def traces(self) -> List[Dict[str, Any]]:
        """Return all trace spans from this session."""
        if self._trace:
            return self._trace.spans
        return []

    def ask_stream(
        self,
        question: str,
        *,
        database: Optional[str] = None,
    ):
        """Stream a query response token by token.

        Requires ``execution_mode="agent"`` or ``"supervisor"``.
        Yields partial text chunks as the agent generates them.

        Usage::

            for chunk in sess.ask_stream("Who is Samsung CEO?"):
                print(chunk, end="", flush=True)
        """
        if self._closed:
            raise RuntimeError("Session is closed")
        if self._execution_mode == "pipeline":
            # Pipeline mode: no streaming, yield full answer
            answer = self.ask(question, database=database)
            yield answer
            return

        db = database or self.database
        agent_ctx = self.context.to_agent_context(ontology=self.ontology)
        context_block = f"\n\n{agent_ctx}" if agent_ctx else ""
        full_msg = f"{question}{context_block}\n[Target database: {db}]"

        try:
            from agents import Runner
            agent = self._get_query_agent()

            async def _stream():
                result = Runner.run_streamed(agent, full_msg)
                async for event in result.stream_events():
                    if hasattr(event, 'data') and hasattr(event.data, 'delta'):
                        yield event.data.delta

            import asyncio
            loop = asyncio.new_event_loop()
            try:
                ait = _stream().__aiter__()
                while True:
                    try:
                        chunk = loop.run_until_complete(ait.__anext__())
                        yield chunk
                    except StopAsyncIteration:
                        break
            finally:
                loop.close()
        except Exception as exc:
            logger.warning("Streaming failed, falling back: %s", exc)
            answer = self.ask(question, database=database)
            yield answer

    def close(self) -> Dict[str, Any]:
        """Close the session and finalize traces.

        Returns a summary of the session.
        """
        if self._closed:
            return {"status": "already_closed"}

        self._closed = True
        summary = {
            "session_id": self.session_id,
            "name": self.name,
            "indexed_documents": len(self.context.indexed_sources),
            "total_nodes": self.context.total_nodes,
            "total_relationships": self.context.total_relationships,
            "queries_answered": len(self.context.queries),
            "degraded_operations": sum(
                1
                for record in [*self.context.indexed_sources, *self.context.queries]
                if bool(record.get("degraded", False))
            ),
            "context_summary": self.context.summary(),
        }

        if self._trace:
            trace_summary = self._trace.end()
            summary["trace"] = trace_summary

        return summary

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        status = "closed" if self._closed else "active"
        return (
            f"Session(name={self.name!r}, id={self.session_id!r}, "
            f"status={status}, docs={len(self.context.indexed_sources)}, "
            f"queries={len(self.context.queries)})"
        )
