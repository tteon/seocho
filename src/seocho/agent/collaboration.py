"""Opt-in, budgeted evidence assembly using the OpenAI Agents SDK.

Admission units are conservative request-size estimates, not provider tokens.
Only observed provider usage is reported as tokens. Streaming is unsupported.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Literal
from uuid import uuid4

from agents import Agent, Model, ModelSettings, RunConfig, Runner, RunHooks, function_tool, handoff
from pydantic import BaseModel, ConfigDict, Field

from .exchange import AgentExchange

Mode = Literal["single", "planned_sequential", "planned_parallel", "reviewed_parallel", "sdk_manager", "sdk_handoff"]
MODES = ("single", "planned_sequential", "planned_parallel", "reviewed_parallel", "sdk_manager", "sdk_handoff")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Task(StrictModel):
    task_id: str
    question: str
    required_slots: list[str]
    relation_paths: list[str]
    depends_on: list[str]
    max_model_calls: int = Field(default=5, ge=1)


class Plan(StrictModel):
    tasks: list[Task]


class Selection(StrictModel):
    evidence_refs: list[str]
    missing_slots: list[str]


class Review(StrictModel):
    issues: list[str]
    additional_tasks: list[Task]


class Claim(StrictModel):
    text: str
    evidence_refs: list[str]


class Answer(StrictModel):
    claims: list[Claim]
    missing_slots: list[str]


class Packet(StrictModel):
    workspace_id: str
    task: Task
    rows: list[dict[str, Any]]
    evidence_refs: list[str]
    missing_slots: list[str]
    status: str


@dataclass(frozen=True)
class Limits:
    max_calls: int = 24
    max_admission_units: int = 240_000
    max_output_tokens: int = 4096
    timeout_seconds: float = 120
    max_tasks: int = 6
    max_turns: int = 24
    concurrency: int = 3
    max_tool_calls: int = 24
    max_context_bytes: int = 24_000

    def __post_init__(self) -> None:
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError("All limits must be positive")


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Ledger:
    limits: Limits
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    tool_calls: int = 0
    admission_units: int = 0
    observed_input_tokens: int = 0
    observed_output_tokens: int = 0
    usage_missing_calls: int = 0
    receipts: list[dict[str, Any]] = field(default_factory=list)

    def remaining_seconds(self) -> float:
        return max(0.0, self.limits.timeout_seconds - (time.monotonic() - self.started))

    def reserve(self, request_units: int) -> None:
        # No await between checking and charging: atomic within one event loop.
        if self.remaining_seconds() <= 0:
            raise BudgetExceeded("time_budget")
        if self.calls >= self.limits.max_calls:
            raise BudgetExceeded("call_budget")
        if self.admission_units + request_units > self.limits.max_admission_units:
            raise BudgetExceeded("admission_budget")
        self.calls += 1
        self.admission_units += request_units

    def reserve_tool(self) -> None:
        if self.remaining_seconds() <= 0:
            raise BudgetExceeded("time_budget")
        if self.tool_calls >= self.limits.max_tool_calls:
            raise BudgetExceeded("tool_budget")
        self.tool_calls += 1

    def summary(self) -> dict[str, Any]:
        return {
            "model_calls": self.calls, "tool_calls": self.tool_calls,
            "admission_units_estimated": self.admission_units,
            "observed_input_tokens": self.observed_input_tokens,
            "observed_output_tokens": self.observed_output_tokens,
            "usage_missing_calls": self.usage_missing_calls,
            "usage_complete": self.calls > 0 and self.usage_missing_calls == 0,
            "elapsed_seconds": time.monotonic() - self.started,
            "limits": vars(self.limits), "receipts": self.receipts,
        }


class BudgetedModel(Model):
    """Charge every SDK model turn, including repair turns, before dispatch."""

    def __init__(self, delegate: Model, ledger: Ledger, *, json_object_mode: bool = False, role: str = "unspecified",
                 max_calls: int | None = None) -> None:
        self.delegate = delegate.for_agent(role) if hasattr(delegate, "for_agent") else delegate
        self.role = role
        self.max_calls = max_calls
        self.local_calls = 0
        self.ledger = ledger
        self.json_object_mode = json_object_mode

    async def get_response(
        self, system_instructions: Any, input: Any, model_settings: Any,
        tools: Any, output_schema: Any, handoffs: Any, tracing: Any,
        **kwargs: Any,
    ) -> Any:
        schema = output_schema.json_schema() if output_schema else None
        if self.json_object_mode and schema:
            system_instructions = (system_instructions or "") + (
                "\nAfter any reasoning, emit ONLY the final JSON object matching this schema; "
                "no markdown or explanation. Tools may be called before the final JSON.\n" + json.dumps(schema)
            )
            model_settings = replace(model_settings, extra_body={
                **(model_settings.extra_body or {}), "response_format": {"type": "json_object"},
            })
        serialized = json.dumps(
            [system_instructions, input, schema,
             [(t.name, getattr(t, "params_json_schema", {})) for t in tools],
             [getattr(h, "tool_name", "") for h in handoffs]], default=str,
        )
        context_bytes = len(json.dumps(input, ensure_ascii=False, default=str).encode("utf-8"))
        if context_bytes > self.ledger.limits.max_context_bytes:
            raise BudgetExceeded("context_budget")
        units = len(serialized.encode("utf-8")) + self.ledger.limits.max_output_tokens
        if self.max_calls is not None and self.local_calls >= self.max_calls:
            raise BudgetExceeded(f"task_call_budget:{self.role}")
        self.ledger.reserve(units)
        self.local_calls += 1
        receipt: dict[str, Any] = {"admission_units_estimated": units, "status": "dispatched",
                                   "agent_role": self.role, "context_bytes": context_bytes}
        call_started = time.monotonic()
        self.ledger.receipts.append(receipt)
        settings = replace(model_settings, max_tokens=self.ledger.limits.max_output_tokens)
        try:
            response = await asyncio.wait_for(
                self.delegate.get_response(
                    system_instructions, input, settings, tools,
                    None if self.json_object_mode else output_schema,
                    handoffs, tracing, **kwargs,
                ), timeout=self.ledger.remaining_seconds(),
            )
        except BaseException:
            receipt["status"] = "failed_or_cancelled"
            receipt["elapsed_seconds"] = time.monotonic() - call_started
            self.ledger.usage_missing_calls += 1
            raise
        receipt["elapsed_seconds"] = time.monotonic() - call_started
        usage = response.usage
        observed = bool(usage and (usage.input_tokens or usage.output_tokens))
        if observed:
            self.ledger.observed_input_tokens += usage.input_tokens
            self.ledger.observed_output_tokens += usage.output_tokens
            receipt.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        else:
            self.ledger.usage_missing_calls += 1
        receipt.update(status="completed", usage_reported=observed)
        if self.json_object_mode and schema and not any(
            getattr(item, "type", "") == "function_call" for item in response.output
        ):
            from ..llm_structured import extract_json_object
            for item in response.output:
                if getattr(item, "type", "") == "message":
                    for content in item.content:
                        if getattr(content, "type", "") == "output_text":
                            content.text = json.dumps(extract_json_object(content.text))
        return response

    async def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Budgeted experiment supports non-streaming runs only")
        yield  # pragma: no cover


def validate_plan(tasks: list[Task], *, previous: set[str], max_tasks: int) -> None:
    if not tasks or len(tasks) > max_tasks:
        raise ValueError("Plan must contain between one and max_tasks tasks")
    known = set(previous)
    for task in tasks:
        if not task.task_id.strip() or task.task_id in known or not task.question.strip():
            raise ValueError("Task IDs must be nonempty and unique; questions cannot be empty")
        if not task.required_slots or any(not s.strip() for s in task.required_slots):
            raise ValueError("Tasks require named answer slots")
        if not task.relation_paths or any(not p.strip() for p in task.relation_paths):
            raise ValueError("Tasks require relation paths")
        if not set(task.depends_on) <= known:
            raise ValueError("Dependencies must name earlier tasks; cycles/forward references forbidden")
        known.add(task.task_id)


class CollaborationExperiment:
    """Assemble graph evidence with code and native SDK coordination; never change normal SDK routing.

    ``retrieve`` is a host-owned, workspace-bound callable returning rows with
    stable ``evidence_id`` fields. Models cannot supply arbitrary Cypher.
    """

    def __init__(
        self, *, model: Model, retrieve: Callable[[str, str], list[dict[str, Any]]],
        recipes: dict[str, str], workspace_id: str, limits: Limits | None = None,
        json_object_mode: bool = False,
        retrieve_search: Callable[[str, str, str], list[dict[str, Any]]] | None = None,
    ) -> None:
        if not workspace_id.strip() or not recipes:
            raise ValueError("workspace_id and retrieval recipes are required")
        self.model = model
        self.retrieve = retrieve
        self.retrieve_search = retrieve_search
        self.recipes = recipes
        self.workspace_id = workspace_id
        self.limits = limits or Limits()
        self.json_object_mode = json_object_mode

    async def run(
        self, *, question: str, anchors: list[str], required_slots: list[str], mode: Mode,
    ) -> dict[str, Any]:
        if mode not in MODES or not question.strip() or not anchors or not required_slots:
            raise ValueError("Valid mode, question, anchors and required_slots are required")
        ledger = Ledger(self.limits)
        run_id = str(uuid4())
        packets: list[Packet] = []
        reviews: list[dict[str, Any]] = []
        exchanges: list[dict[str, Any]] = []
        retrieval_receipts: list[dict[str, Any]] = []
        coordination_events: list[dict[str, Any]] = []
        agent_designs: list[dict[str, Any]] = []
        plan: list[Task] = []
        answer: Answer | None = None
        status, error = "completed", None
        semaphore = asyncio.Semaphore(self.limits.concurrency)
        config = RunConfig(tracing_disabled=True, trace_include_sensitive_data=False)

        def exchange(sender: str, recipient: str, kind: str, refs: list[str]) -> None:
            item = AgentExchange(
                exchange_id=str(uuid4()), workspace_id=self.workspace_id, run_id=run_id,
                sender_agent_id=sender, recipient_agent_id=recipient, message_type=kind,
                evidence_refs=tuple(refs),
            )
            item.emit_trace()
            exchanges.append({
                "sender": sender, "recipient": recipient, "message_type": kind,
                "evidence_refs": refs, "workspace_id": self.workspace_id,
            })

        workspace_id = self.workspace_id

        class CoordinationHooks(RunHooks):
            async def on_agent_start(self, context: Any, agent: Agent) -> None:
                coordination_events.append({"kind": "agent_start", "agent": agent.name,
                                            "workspace_id": workspace_id})

            async def on_handoff(self, context: Any, from_agent: Agent, to_agent: Agent) -> None:
                ledger.reserve_tool()
                coordination_events.append({"kind": "handoff", "sender": from_agent.name,
                                            "recipient": to_agent.name, "workspace_id": workspace_id})

        # Hooks retain names and workspace identity, never question or evidence text.
        hooks = CoordinationHooks()

        def make_agent(name: str, instruction: str, output: Any, tools: list[Any],
                       *, task_id: str = "", max_calls: int | None = None) -> Agent:
            agent_designs.append({"name": name, "task_id": task_id, "instructions": instruction,
                                 "tools": [t.name for t in tools], "output_type": output.__name__,
                                 "max_model_calls": max_calls, "max_turns": self.limits.max_turns,
                                 "design_version": "v1"})
            return Agent(name=name, instructions=instruction,
                         model=BudgetedModel(self.model, ledger, json_object_mode=self.json_object_mode,
                                             role=f"{name}:{task_id}" if task_id else name, max_calls=max_calls),
                         tools=tools, output_type=output,
                         model_settings=ModelSettings(parallel_tool_calls=False))

        async def invoke(name: str, instruction: str, payload: Any, output: Any,
                         tools: list[Any] | None = None, *, task_id: str = "",
                         max_calls: int | None = None) -> Any:
            text = json.dumps(payload, ensure_ascii=False, default=str)
            if len(text.encode("utf-8")) > self.limits.max_context_bytes:
                raise BudgetExceeded("context_budget")
            async with semaphore:
                result = await Runner.run(
                    make_agent(name, instruction, output, tools or [], task_id=task_id, max_calls=max_calls),
                    text, max_turns=self.limits.max_turns, run_config=config, hooks=hooks,
                )
            return result.final_output

        async def worker(task: Task) -> Packet:
            rows: dict[str, dict[str, Any]] = {}

            @function_tool(failure_error_function=None)
            async def retrieve_evidence(recipe_id: str, node_id: str, search_text: str = "") -> str:
                """Retrieve bounded graph evidence; search_text contains subquestion keywords when supported."""
                if recipe_id not in self.recipes or node_id not in anchors:
                    raise ValueError("Unknown recipe or anchor")
                ledger.reserve_tool()
                if search_text and self.retrieve_search is None:
                    raise ValueError("This workload does not support keyword search")
                if self.retrieve_search is not None:
                    found = await asyncio.to_thread(self.retrieve_search, recipe_id, node_id, search_text)
                else:
                    found = await asyncio.to_thread(self.retrieve, recipe_id, node_id)
                result = json.dumps(found, ensure_ascii=False, default=str)
                if len(result.encode("utf-8")) > self.limits.max_context_bytes:
                    raise BudgetExceeded("retrieval_context_budget")
                for row in found:
                    ref = row.get("evidence_id")
                    if not isinstance(ref, str) or not ref:
                        raise ValueError("Every retrieved row requires an evidence_id")
                    if ref in rows and rows[ref] != row:
                        raise ValueError("Conflicting rows reuse an evidence_id")
                    rows[ref] = row
                retrieval_receipts.append({
                    "workspace_id": self.workspace_id, "task_id": task.task_id,
                    "recipe_id": recipe_id, "anchor": node_id, "search_text": search_text, "rows": found,
                })
                return result

            dependencies = [p.model_dump() for p in packets if p.task.task_id in task.depends_on]
            try:
                selection = await invoke(
                    "RetrievalWorker",
                    "Retrieve graph rows needed for the task. Use only permitted recipes and anchors. "
                    "Return evidence_refs from rows you actually retrieved. Name any unfilled required "
                    "slots. A conflict is evidence to preserve, not resolve by inventing a fact. "
                    "If keyword search is supported, search_text should contain concise subquestion "
                    "keywords, not the full original question. Different searches can fill different slots.",
                    {"task": task.model_dump(), "anchors": anchors, "recipes": self.recipes,
                     "dependencies": dependencies, "keyword_search_supported": self.retrieve_search is not None},
                    Selection, [retrieve_evidence],
                    task_id=task.task_id, max_calls=min(task.max_model_calls, self.limits.max_calls),
                )
            except BaseException:
                packets.append(Packet(
                    workspace_id=self.workspace_id, task=task, rows=list(rows.values()),
                    evidence_refs=[], missing_slots=list(task.required_slots), status="failed_or_cancelled",
                ))
                raise
            if not set(selection.evidence_refs) <= rows.keys():
                raise ValueError("Worker invented evidence references")
            missing = list(selection.missing_slots)
            if not selection.evidence_refs:
                missing = sorted(set(missing) | set(task.required_slots))
            packet = Packet(
                workspace_id=self.workspace_id, task=task,
                rows=[rows[ref] for ref in dict.fromkeys(selection.evidence_refs)],
                evidence_refs=list(dict.fromkeys(selection.evidence_refs)),
                missing_slots=missing, status="partial" if missing else "retrieved",
            )
            exchange(task.task_id, "Answerer", "evidence", packet.evidence_refs)
            packets.append(packet)
            return packet

        async def execute(tasks: list[Task]) -> None:
            pending = list(tasks)
            while pending:
                completed = {p.task.task_id for p in packets}
                ready = [t for t in pending if set(t.depends_on) <= completed]
                if mode in ("single", "planned_sequential"):
                    ready = ready[:1]
                jobs = [asyncio.create_task(worker(t)) for t in ready]
                try:
                    await asyncio.gather(*jobs)
                except BaseException:
                    for job in jobs:
                        job.cancel()
                    await asyncio.gather(*jobs, return_exceptions=True)
                    raise
                pending = [t for t in pending if t not in ready]

        async def sdk_coordinate(base: dict[str, Any]) -> None:
            @function_tool(failure_error_function=None)
            async def execute_task(task: Task) -> str:
                """Execute one bounded task; return the same evidence packet as the code scheduler."""
                ledger.reserve_tool()
                validate_plan([task], previous={t.task_id for t in plan}, max_tasks=1)
                if len(plan) >= self.limits.max_tasks:
                    raise BudgetExceeded("task_budget")
                if not set(task.depends_on) <= {p.task.task_id for p in packets}:
                    raise ValueError("Dependencies must have completed packets")
                plan.append(task)
                exchange("EvidenceCoordinator", task.task_id, "task", [])
                return (await worker(task)).model_dump_json()

            instruction = (
                "Coordinate graph retrieval for the supplied question. Decompose it into bounded "
                "tasks and use execute_task for every task. Use only the supplied recipes and anchors. "
                "Dependencies must name previously completed tasks. Leave model calls for the final "
                "answerer; every coordinator and worker turn spends the common budget. "
                f"Execute at most {self.limits.max_tasks} tasks. "
                "Return selected evidence_refs and missing_slots after collecting packets. Do not answer."
            )
            specialist = make_agent("EvidenceCoordinator", instruction, Selection, [execute_task])
            text = json.dumps(base, ensure_ascii=False)
            if len(text.encode("utf-8")) > self.limits.max_context_bytes:
                raise BudgetExceeded("context_budget")
            if mode == "sdk_manager":
                async def extract(result: Any) -> str:
                    coordination_events.append({"kind": "agent_tool_return", "sender": specialist.name,
                                                "recipient": "Manager", "workspace_id": self.workspace_id})
                    return json.dumps({"packets": [p.model_dump() for p in packets]}, ensure_ascii=False)

                specialist_tool = specialist.as_tool(
                    tool_name="collect_evidence", tool_description="Delegate retrieval for the original question.",
                    custom_output_extractor=extract, run_config=config, max_turns=self.limits.max_turns,
                    hooks=hooks, failure_error_function=None,
                )
                # Ignore free-form delegated input; every arm gets the same host-owned task context.
                original_invoke = specialist_tool.on_invoke_tool

                async def bounded_delegate(context: Any, arguments: str) -> Any:
                    ledger.reserve_tool()
                    coordination_events.append({"kind": "agent_tool_call", "sender": "Manager",
                                                "recipient": specialist.name, "workspace_id": self.workspace_id})
                    delegated = {**base, "packets": [p.model_dump() for p in packets],
                                 "remaining_model_calls": self.limits.max_calls - ledger.calls}
                    return await original_invoke(context, json.dumps({"input": json.dumps(delegated, ensure_ascii=False)}))

                specialist_tool.on_invoke_tool = bounded_delegate
                entry = make_agent("Manager", "Delegate retrieval using collect_evidence. Inspect returned "
                                   "packets, delegate again if needed within the common budget, and return "
                                   "evidence_refs and missing_slots. The separate Answerer synthesizes the answer.",
                                   Selection, [specialist_tool])
            else:
                def packet_context(data: Any) -> Any:
                    # Preserve SDK history in run records, but pass only trusted task context downstream.
                    return data.clone(input_history=text, pre_handoff_items=(), input_items=())

                entry = make_agent("Router", "Transfer control to EvidenceCoordinator to retrieve "
                                   "evidence for the supplied question. Do not answer or invent references.",
                                   Selection, [])
                entry.handoffs = [handoff(specialist, input_filter=packet_context)]
                agent_designs[-1]["handoffs"] = [specialist.name]
            await Runner.run(entry, text, max_turns=self.limits.max_turns, run_config=config, hooks=hooks)
            if not packets:
                raise ValueError("Coordinator finished without executing a retrieval task")

        async def pipeline() -> None:
            nonlocal plan, answer
            base = {"question": question, "anchors": anchors,
                    "required_slots": required_slots, "recipes": self.recipes,
                    "remaining_model_calls": self.limits.max_calls - ledger.calls}
            if mode in ("sdk_manager", "sdk_handoff"):
                await sdk_coordinate(base)
            elif mode == "single":
                plan = [Task(task_id="single", question=question, required_slots=required_slots,
                             relation_paths=list(self.recipes.values()), depends_on=[],
                             max_model_calls=max(1, self.limits.max_calls - 1))]
            else:
                proposal = await invoke(
                    "Planner", "Decompose the question into bounded graph retrieval tasks. "
                    "Use named required_slots and relation_paths available in recipes. Dependencies "
                    "must refer to earlier tasks. Independent tasks should have empty depends_on. "
                    "Allocate max_model_calls per task, including tool follow-up turns, using the "
                    "common remaining budget; leave room for the final answerer. "
                    f"Produce at most {self.limits.max_tasks} tasks.", base, Plan,
                )
                plan = proposal.tasks
                validate_plan(plan, previous=set(), max_tasks=self.limits.max_tasks)
            if mode not in ("sdk_manager", "sdk_handoff"):
                for task in plan:
                    exchange("Planner", task.task_id, "task", [])
                await execute(plan)
            if mode == "reviewed_parallel":
                base["remaining_model_calls"] = self.limits.max_calls - ledger.calls
                review = await invoke(
                    "Reviewer", "Inspect retrieved evidence for missing slots, conflicting evidence "
                    "and unmet relation paths. Never add facts. Request additional retrieval tasks "
                    "only if they can address an issue using the supplied recipes. Dependencies "
                    "must name existing or earlier additional tasks. Return an empty list if done. "
                    f"At most {self.limits.max_tasks} additional tasks; one review round only.",
                    {**base, "packets": [p.model_dump() for p in packets]}, Review,
                )
                reviews.append(review.model_dump())
                if review.additional_tasks:
                    validate_plan(review.additional_tasks, previous={t.task_id for t in plan},
                                  max_tasks=self.limits.max_tasks)
                    for task in review.additional_tasks:
                        exchange("Reviewer", task.task_id, "repair_task", [])
                    await execute(review.additional_tasks)
            answer = await invoke(
                "Answerer", "Answer only from the provided graph evidence. Each claim must cite "
                "supporting evidence_refs. Preserve conflicts, dates and all missing_slots. "
                "Do not infer absent facts. For numerical calculations name the operands, units, "
                "periods and operation in the cited claim. Return no claims when evidence is insufficient.",
                {"question": question, "required_slots": required_slots,
                 "packets": [p.model_dump() for p in packets]}, Answer,
            )
            refs = {ref for p in packets for ref in p.evidence_refs}
            for claim in answer.claims:
                if not claim.evidence_refs or not set(claim.evidence_refs) <= refs:
                    raise ValueError("Answer has missing or unknown evidence references")
            answer.missing_slots = sorted(set(answer.missing_slots) |
                                         {s for p in packets for s in p.missing_slots})

        try:
            await asyncio.wait_for(pipeline(), timeout=self.limits.timeout_seconds)
        except (BudgetExceeded, asyncio.TimeoutError) as exc:
            status, error = "budget_exhausted", str(exc) or "time_budget"
            answer = None
        except Exception as exc:
            # SDK tools wrap nested failures in UserError. Preserve budget semantics
            # through the exception chain without classifying error strings.
            cause: BaseException | None = exc
            visited: set[int] = set()
            while cause is not None and id(cause) not in visited:
                visited.add(id(cause))
                if isinstance(cause, (BudgetExceeded, asyncio.TimeoutError)):
                    status, error = "budget_exhausted", str(cause) or "time_budget"
                    break
                cause = cause.__cause__ or cause.__context__
            else:
                status, error = "failed", f"{type(exc).__name__}: {exc}"
            answer = None
        return {
            "schema_version": "seocho.collaboration.v1", "run_id": run_id,
            "workspace_id": self.workspace_id, "mode": mode, "question": question,
            "status": status, "error": error, "plan": [t.model_dump() for t in plan],
            "packets": [p.model_dump() for p in packets], "reviews": reviews,
            "answer": answer.model_dump() if answer else None,
            "budget": ledger.summary(), "exchanges": exchanges,
            "retrieval_receipts": retrieval_receipts,
            "coordination_events": coordination_events, "agent_designs": agent_designs,
            "context_policy": "task_and_evidence_packets",
            "coordination_metrics": {
                "native_handoffs": sum(e["kind"] == "handoff" for e in coordination_events),
                "agent_tool_calls": sum(e["kind"] == "agent_tool_call" for e in coordination_events),
                "duplicate_retrievals": len(retrieval_receipts) - len({
                    (r["recipe_id"], r["anchor"], r["search_text"]) for r in retrieval_receipts}),
                "tasks_executed": len(packets),
                "context_bytes_total": sum(r["context_bytes"] for r in ledger.receipts),
            },
        }
