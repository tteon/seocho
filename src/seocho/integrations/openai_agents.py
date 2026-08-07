"""Expose SEOCHO's ontology guardrail and graph access to the OpenAI Agents SDK.

A deliberately thin adapter, kept separate from the core for the reason every comparable
system separates it: Neo4j ships `neo4j-graphrag` and `langchain-neo4j` as two packages, and
Mem0 and Zep both describe themselves as working alongside *any* orchestrator. Binding the
core to one framework would also undercut the thing this project set out to show — that the
graph and the ontology are the durable asset while the model, and by extension the loop, are
swappable.

**What the SDK provides and SEOCHO does not:** a place to hang tool-argument validation
(`tool_input_guardrail` runs before the tool executes and can reject with a message the model
sees), separate span types for guardrails and tool calls, and step-level control of the loop.

**What SEOCHO provides and the SDK does not:** the rules. The SDK gives a hook; it has no
notion of a schema, so it cannot know that `TRANSFER` runs Account→Account, that an anchor
must bind to `acct_no` rather than `id`, or that a query with no anchor will expand from every
node. Those are the checks that turned a 38-million-db-hit query into a rejected one, and they
live here.

The pairing is therefore additive rather than a migration: the guardrail body is the same
`validate_text2cypher_fallback` the deterministic path already uses, so both surfaces enforce
one rulebook.

Usage::

    from seocho.integrations.openai_agents import build_graph_agent

    agent = build_graph_agent(ontology=ontology, graph_store=store, database="finbench")
    result = await Runner.run(agent, "Which accounts are under common control?")
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# The SDK is an optional dependency: importing this module must not break a deployment that
# does not use it, and the error when it is missing should name the fix.
try:  # pragma: no cover - exercised by the import-guard test
    from agents import (
        Agent,
        ModelSettings,
        ToolGuardrailFunctionOutput,
        function_tool,
        tool_input_guardrail,
    )
    from agents.tool_context import ToolContext

    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SDK_AVAILABLE = False


def _require_sdk() -> None:
    if not _SDK_AVAILABLE:
        raise ImportError(
            "the openai-agents SDK is not installed; `pip install openai-agents` to use "
            "seocho.integrations.openai_agents (the SEOCHO core does not need it)")


@dataclass
class GuardrailLedger:
    """Every guardrail decision, kept so rejections are measurable rather than anecdotal.

    Rejections are the interesting signal, not an error condition: three of the four defects
    this experiment found were visible first as a rejection string, and counting them by
    reason is how a drift in generation quality shows up before accuracy moves.
    """

    allowed: int = 0
    rejected: int = 0
    reasons: Dict[str, int] = field(default_factory=dict)

    def record(self, violations: Sequence[str]) -> None:
        if not violations:
            self.allowed += 1
            return
        self.rejected += 1
        for v in violations:
            # Keep the violation kind, drop the payload: `unknown_labels:Workspace,Foo`
            # aggregates with `unknown_labels:Bar` rather than fragmenting the tally.
            kind = str(v).split(":", 1)[0]
            self.reasons[kind] = self.reasons.get(kind, 0) + 1

    def summary(self) -> Dict[str, Any]:
        total = self.allowed + self.rejected
        return {
            "calls": total,
            "allowed": self.allowed,
            "rejected": self.rejected,
            "rejection_rate": round(self.rejected / total, 4) if total else None,
            "by_reason": dict(sorted(self.reasons.items(), key=lambda kv: -kv[1])),
        }


def make_ontology_guardrail(ontology: Any, *, ledger: Optional[GuardrailLedger] = None,
                            cypher_arg: str = "cypher"):
    """A tool-input guardrail that enforces the ontology on generated Cypher.

    Runs before the tool executes, so a query that would scan the graph never reaches the
    database. The rejection text is returned to the model, which is what turns the SDK's
    guardrail into a repair loop — SEOCHO's deterministic path spends an explicit
    ``max_repair_attempts`` iteration on the same job.

    Rejects rather than raises. A tripwire would abort the run, and a schema violation is an
    ordinary, recoverable event: the model usually fixes it when told what was wrong. Raising
    is reserved for the caller to configure.
    """
    _require_sdk()
    from ..query.hybrid_planner import policy_from_ontology
    from ..query.workload_compiler import validate_text2cypher_fallback

    policy = policy_from_ontology(ontology)
    ledger = ledger if ledger is not None else GuardrailLedger()

    @tool_input_guardrail
    def ontology_guardrail(data: "ToolContext") -> "ToolGuardrailFunctionOutput":
        ctx = getattr(data, "context", data)
        raw = getattr(ctx, "tool_arguments", "") or "{}"
        try:
            args = json.loads(raw)
        except (TypeError, ValueError):
            ledger.record(["unparsable_arguments"])
            return ToolGuardrailFunctionOutput.reject_content(
                "tool arguments were not valid JSON; re-emit the call")

        cypher = str(args.get(cypher_arg) or "")
        if not cypher.strip():
            ledger.record(["missing_cypher"])
            return ToolGuardrailFunctionOutput.reject_content(
                f"the `{cypher_arg}` argument was empty")

        try:
            params = json.loads(args.get("params_json") or "{}")
        except (TypeError, ValueError):
            params = {}
        violations = validate_text2cypher_fallback(cypher, params=params, policy=policy)
        ledger.record(violations)
        if violations:
            return ToolGuardrailFunctionOutput.reject_content(
                "the query violates the graph schema and was not executed: "
                + ", ".join(violations)
                + ". Re-emit it using only the declared labels, relationship types and "
                  "parameters.")
        return ToolGuardrailFunctionOutput.allow()

    ontology_guardrail.ledger = ledger  # type: ignore[attr-defined]
    return ontology_guardrail


def make_graph_tool(graph_store: Any, *, database: str, row_cap: int = 50,
                    query_timeout_s: float = 30.0):
    """A read-only Cypher tool over the graph store.

    Two bounds are applied here rather than trusted to the model. The row cap is the same
    ``max_result_rows`` the deterministic path uses, and the timeout goes on the *transaction*
    — ``session.run(..., timeout=x)`` silently forwards the keyword into query parameters and
    bounds nothing, a mistake made twice in this project before it was written down.
    """
    _require_sdk()

    @function_tool(
        name_override="run_cypher",
        description_override=(
            "Run one read-only Cypher query against the financial graph and return rows as "
            "JSON. Use only labels and relationship types from the supplied schema. Pass "
            "values as named Cypher parameters ($name) and supply them in params_json as a "
            "JSON object, e.g. {\"acct\": 42}. Include a LIMIT."),
    )
    def run_cypher(cypher: str, params_json: str = "{}") -> str:
        # Parameters arrive as a JSON *string*, not a dict. The SDK builds a strict JSON
        # schema from the signature and rejects `Dict[str, Any]` outright, because a strict
        # schema cannot express an open-ended object. Taking the string keeps the tool
        # callable and keeps parameterisation — which the guardrail needs, since it checks
        # that the anchor arrived as a parameter rather than an inlined literal.
        try:
            params = json.loads(params_json or "{}")
        except (TypeError, ValueError):
            return json.dumps({"error": "params_json was not valid JSON"})
        if not isinstance(params, dict):
            return json.dumps({"error": "params_json must be a JSON object"})
        driver = getattr(graph_store, "_driver", None) or getattr(graph_store, "driver", None)
        if driver is None:
            return json.dumps({"error": "graph store exposes no driver"})
        from neo4j.exceptions import Neo4jError

        with driver.session(database=database) as session:
            tx = session.begin_transaction(timeout=query_timeout_s)
            try:
                result = tx.run(cypher, **params)
                rows = [dict(r) for _, r in zip(range(row_cap), result)]
                truncated = result.peek() is not None
                tx.commit()
            except Neo4jError as exc:
                tx.close()
                return json.dumps({"error": exc.code, "message": str(exc)[:300]})
        return json.dumps(
            {
                "rows": rows,
                "row_count": len(rows),
                # Stated explicitly because a bounded result presented as a complete one is
                # the failure this experiment measured at 0 disclosures out of 20.
                "truncated": truncated,
                "row_cap": row_cap,
            },
            default=str,
        )

    return run_cypher


def _mara_model(model: str) -> Any:
    """Point the SDK at MARA's OpenAI-compatible endpoint.

    The models measured throughout this project are MARA-served (gpt-oss-120b, DeepSeek-V3.1,
    MiniMax-M2.5). Routing the SDK to them keeps the framework swap from silently becoming a
    model swap as well, which would confound any before/after comparison.
    """
    _require_sdk()
    from agents import OpenAIChatCompletionsModel
    from openai import AsyncOpenAI

    api_key = os.getenv("MARA_API_KEY")
    if not api_key:
        raise RuntimeError("MARA_API_KEY is not set")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("MARA_BASE_URL", "https://api.cloud.mara.com/v1"),
    )
    return OpenAIChatCompletionsModel(model=model, openai_client=client)


def build_graph_agent(*, ontology: Any, graph_store: Any, database: str,
                      model: str = "gpt-oss-120b", name: str = "graph_analyst",
                      row_cap: int = 50, extra_tools: Sequence[Any] = (),
                      ledger: Optional[GuardrailLedger] = None) -> "Agent":
    """A graph-querying agent whose tool is guarded by the ontology.

    The schema block is the same one the deterministic planner sends, so the agent is told
    what SEOCHO's own path is told — including the two facts a schema of labels alone cannot
    carry: which end of a same-label relationship the anchor sits on, and that a relationship
    is heavy-tailed enough that an unbounded expansion will not return.
    """
    _require_sdk()
    from ..query.hybrid_planner import policy_from_ontology, schema_for_prompt

    policy = policy_from_ontology(ontology)
    schema = schema_for_prompt(ontology, policy)
    guardrail = make_ontology_guardrail(ontology, ledger=ledger)
    tool = make_graph_tool(graph_store, database=database, row_cap=row_cap)
    tool.tool_input_guardrails = [guardrail]

    instructions = (
        "You are a financial-crime analyst querying a graph database with Cypher.\n\n"
        "Schema (use only these labels, relationship types and parameters):\n"
        + json.dumps(schema, indent=2, default=str)
        + "\n\nRules:\n"
        "- Every matched node must carry the tenant scope shown in the schema.\n"
        "- Bind the account the question is about by its declared identity key.\n"
        "- Include a LIMIT; the tool caps rows regardless.\n"
        "- If the tool reports `truncated: true`, say so in your answer rather than "
        "presenting a partial result as complete.\n"
        "- If the schema cannot express the question, say what is missing instead of "
        "inventing labels."
    )

    return Agent(
        name=name,
        instructions=instructions,
        model=_mara_model(model),
        model_settings=ModelSettings(temperature=0.0),
        tools=[tool, *extra_tools],
    )


def as_specialist_tool(agent: "Agent", *, tool_name: str, tool_description: str) -> Any:
    """Expose a SEOCHO agent to a manager agent as a callable tool.

    ``Agent.as_tool`` differs from a handoff in the way that matters here: the sub-agent
    receives generated input rather than the conversation, and control returns to the caller.
    It keeps its own instructions, model and guardrails, so a graph specialist stays bound by
    the ontology even when a manager that knows nothing about the schema invokes it.

    The SDK's own guidance is to prefer this over a handoff when one agent should own the
    final answer or combine several specialists — which is the shape of an investigation, where
    a case agent draws on graph, document and sanctions specialists and writes one narrative.
    """
    _require_sdk()
    return agent.as_tool(tool_name=tool_name, tool_description=tool_description)


__all__ = [
    "GuardrailLedger",
    "as_specialist_tool",
    "build_graph_agent",
    "make_graph_tool",
    "make_ontology_guardrail",
]
