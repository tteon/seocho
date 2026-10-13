"""Offline contracts only; no external compatibility or performance evidence."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from seocho.agent.collaboration import (
    Answer, BudgetedModel, BudgetExceeded, Claim, CollaborationExperiment,
    Ledger, Limits, Plan, Review, Selection, Task, validate_plan,
)
from seocho.eval.collaboration import RECIPE_PATHS, RecipeRetriever, score_evidence


def task(name: str = "t1", deps: list[str] | None = None) -> Task:
    return Task(task_id=name, question="Find support", required_slots=["status"],
                relation_paths=["Company-REPORTED->Incident"], depends_on=deps or [])


def tool_context():
    from agents.tool_context import ToolContext
    return ToolContext(context=None, tool_name="retrieve_evidence", tool_call_id="test", tool_arguments="{}")


def run(coro):
    return asyncio.run(coro)


def test_plan_rejects_duplicate_cycle_empty_and_forward_dependencies():
    for tasks in ([], [task(), task()], [task(deps=["t1"])], [task(deps=["later"]) ]):
        with pytest.raises(ValueError):
            validate_plan(tasks, previous=set(), max_tasks=3)
    validate_plan([task("t2", ["t1"])], previous={"t1"}, max_tasks=3)


def test_budget_reserves_before_parallel_dispatch_and_does_not_refund_unknown_usage():
    ledger = Ledger(Limits(max_calls=1))
    calls = []

    async def response(*args, **kwargs):
        calls.append(1)
        await asyncio.sleep(0)
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=0, output_tokens=0))

    async def scenario():
        from agents import ModelSettings
        model = BudgetedModel(SimpleNamespace(get_response=response), ledger)
        results = await asyncio.gather(*[
            model.get_response("s", "q", ModelSettings(), [], None, [], None)
            for _ in range(2)], return_exceptions=True)
        assert sum(isinstance(r, BudgetExceeded) for r in results) == 1
    run(scenario())
    assert len(calls) == ledger.calls == 1
    assert ledger.admission_units > 0
    assert ledger.summary()["usage_complete"] is False
    assert ledger.usage_missing_calls == 1


def test_failed_and_reported_calls_are_accounted():
    from agents import ModelSettings
    ledger = Ledger(Limits())

    async def failed(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError):
        run(BudgetedModel(SimpleNamespace(get_response=failed), ledger).get_response(
            "s", "q", ModelSettings(), [], None, [], None))
    assert ledger.calls == ledger.usage_missing_calls == 1

    async def observed(*args, **kwargs):
        assert args[2].max_tokens == ledger.limits.max_output_tokens
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=11, output_tokens=7))
    run(BudgetedModel(SimpleNamespace(get_response=observed), ledger).get_response(
        "s", "q", ModelSettings(), [], None, [], None))
    assert ledger.observed_input_tokens == 11
    assert ledger.observed_output_tokens == 7
    assert ledger.receipts[0]["status"] == "failed_or_cancelled"


@pytest.mark.parametrize("mode", ["single", "planned_sequential", "planned_parallel", "reviewed_parallel"])
def test_modes_capture_actual_rows_and_reviewer_additional_retrieval(monkeypatch, mode):
    seen = []

    async def runner(agent, text, **kwargs):
        seen.append(agent.name)
        assert "gold" not in text and "expected_refs" not in text
        if agent.name == "Planner":
            output = Plan(tasks=[task()])
        elif agent.name == "Reviewer":
            output = Review(issues=["recheck"], additional_tasks=[task("repair", ["t1"])])
        elif agent.name == "RetrievalWorker":
            result = await agent.tools[0].on_invoke_tool(tool_context(), json.dumps({"recipe_id": "incidents", "node_id": "acme"}))
            assert json.loads(result)[0]["evidence_id"] == "e1"
            output = Selection(evidence_refs=["e1"], missing_slots=[])
        else:
            output = Answer(claims=[Claim(text="Supported", evidence_refs=["e1"])], missing_slots=[])
        return SimpleNamespace(final_output=output)
    monkeypatch.setattr("seocho.agent.collaboration.Runner.run", runner)
    experiment = CollaborationExperiment(model=None, retrieve=lambda r, n: [{"evidence_id": "e1", "source": "s1"}],
                                         recipes=RECIPE_PATHS, workspace_id="w")
    report = run(experiment.run(question="q", anchors=["acme"], required_slots=["status"], mode=mode))
    assert report["status"] == "completed", report["error"]
    assert report["packets"][0]["rows"][0]["source"] == "s1"
    assert len(report["packets"]) == (2 if mode == "reviewed_parallel" else 1)
    assert ("Planner" in seen) == (mode != "single")
    assert ("Reviewer" in seen) == (mode == "reviewed_parallel")
    assert all(e["workspace_id"] == "w" for e in report["exchanges"])
    assert score_evidence(report, ["e1"])["expected_citation_recall"] == 1
    assert score_evidence(report, ["e1"])["answer_correctness"] is None


@pytest.mark.parametrize("where", ["worker", "answer"])
def test_fabricated_citations_fail_without_publishing_answer(monkeypatch, where):
    async def runner(agent, text, **kwargs):
        if agent.name == "RetrievalWorker":
            await agent.tools[0].on_invoke_tool(tool_context(), '{"recipe_id":"incidents","node_id":"acme"}')
            output = Selection(evidence_refs=["invented" if where == "worker" else "e1"], missing_slots=[])
        else:
            output = Answer(claims=[Claim(text="x", evidence_refs=["invented"])], missing_slots=[])
        return SimpleNamespace(final_output=output)
    monkeypatch.setattr("seocho.agent.collaboration.Runner.run", runner)
    report = run(CollaborationExperiment(model=None, retrieve=lambda r, n: [{"evidence_id": "e1"}],
                                        recipes=RECIPE_PATHS, workspace_id="w").run(
        question="q", anchors=["acme"], required_slots=["status"], mode="single"))
    assert report["status"] == "failed"
    assert report["answer"] is None


def test_no_evidence_preserves_required_missing_slots(monkeypatch):
    async def runner(agent, text, **kwargs):
        output = (Selection(evidence_refs=[], missing_slots=[]) if agent.name == "RetrievalWorker"
                  else Answer(claims=[], missing_slots=[]))
        return SimpleNamespace(final_output=output)
    monkeypatch.setattr("seocho.agent.collaboration.Runner.run", runner)
    report = run(CollaborationExperiment(model=None, retrieve=lambda r, n: [],
                                        recipes=RECIPE_PATHS, workspace_id="w").run(
        question="q", anchors=["acme"], required_slots=["status"], mode="single"))
    assert report["answer"]["missing_slots"] == ["status"]


def test_recipe_gate_validates_queries_and_all_workspace_bindings():
    class Store:
        def query(self, query, **kwargs):
            assert kwargs["workspace_id"] == "w"
            assert kwargs["enforce_workspace_filter"] is True
            assert kwargs["params"] == {"node_id": "acme", "workspace_id": "w"}
            assert "LIMIT 40" in query
            assert "c._workspace_id" in query and "i._workspace_id" in query
            if "HAS_EVIDENCE" in query:
                assert all(f"{v}._workspace_id" in query for v in ("k", "a", "m", "h"))
            return []
    retriever = RecipeRetriever(Store(), workspace_id="w")
    for recipe in RECIPE_PATHS:
        assert retriever(recipe, "acme") == []
    with pytest.raises(KeyError):
        retriever("arbitrary-cypher", "acme")


def test_parallel_workers_overlap_and_dependencies_wait(monkeypatch):
    active = 0
    peak = 0
    finished = set()

    async def runner(agent, text, **kwargs):
        nonlocal active, peak
        payload = json.loads(text)
        if agent.name == "Planner":
            output = Plan(tasks=[task("a"), task("b"), task("c", ["a", "b"])])
        elif agent.name == "RetrievalWorker":
            t = payload["task"]
            assert set(t["depends_on"]) <= finished
            if t["task_id"] == "c":
                assert len(payload["dependencies"]) == 2
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            finished.add(t["task_id"])
            output = Selection(evidence_refs=[], missing_slots=["status"])
        else:
            output = Answer(claims=[], missing_slots=["status"])
        return SimpleNamespace(final_output=output)
    monkeypatch.setattr("seocho.agent.collaboration.Runner.run", runner)
    report = run(CollaborationExperiment(model=None, retrieve=lambda r, n: [],
                                        recipes=RECIPE_PATHS, workspace_id="w").run(
        question="q", anchors=["acme"], required_slots=["status"], mode="planned_parallel"))
    assert report["status"] == "completed"
    assert peak == 2
    assert finished == {"a", "b", "c"}


def test_workload_validates_and_seed_stamps_workspace():
    path = Path(__file__).resolve().parents[2] / "examples/graphrag-collaboration/run.py"
    spec = importlib.util.spec_from_file_location("collaboration_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = json.loads(path.with_name("workload.json").read_text())
    module.validate_workload(data)
    writes = []
    module.seed(SimpleNamespace(execute_write=lambda q, **kw: writes.append((q, kw))), data, "w", "neo4j")
    assert len(writes) == 9
    assert all(kw["params"]["workspace_id"] == "w" for _, kw in writes)
    assert all("$workspace_id" in query for query, _ in writes)


@pytest.mark.parametrize("json_mode", [False, True])
def test_actual_sdk_runner_uses_budgeted_model_and_function_tool(json_mode):
    """Exercise the real SDK loop with scripted responses, not a mocked Runner."""
    from agents import Model, ModelResponse, Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    class ScriptedModel(Model):
        def __init__(self):
            self.calls = 0

        async def get_response(self, system_instructions, input, model_settings,
                               tools, output_schema, handoffs, tracing, **kwargs):
            self.calls += 1
            if json_mode:
                assert output_schema is None
                assert model_settings.extra_body["response_format"] == {"type": "json_object"}
                assert "schema" in system_instructions
            if self.calls == 1:
                output = [ResponseFunctionToolCall(type="function_call", name="retrieve_evidence",
                                                  call_id="call1", arguments='{"recipe_id":"incidents","node_id":"acme"}')]
            else:
                text = (Selection(evidence_refs=["e1"], missing_slots=[]).model_dump_json()
                        if self.calls == 2 else
                        Answer(claims=[Claim(text="Found", evidence_refs=["e1"])], missing_slots=[]).model_dump_json())
                if json_mode:
                    text = "<think>private reasoning</think>```json\n" + text + "\n```"
                output = [ResponseOutputMessage(id=f"msg{self.calls}", type="message", role="assistant",
                                                status="completed", content=[ResponseOutputText(
                                                    type="output_text", text=text, annotations=[])])]
            return ModelResponse(output=output, usage=Usage(input_tokens=20, output_tokens=10, total_tokens=30),
                                 response_id=f"r{self.calls}")

        async def stream_response(self, *args, **kwargs):
            raise NotImplementedError
            yield

    model = ScriptedModel()
    report = run(CollaborationExperiment(model=model, retrieve=lambda r, n: [{"evidence_id": "e1"}],
                                        recipes=RECIPE_PATHS, workspace_id="w", json_object_mode=json_mode).run(
        question="q", anchors=["acme"], required_slots=["status"], mode="single"))
    assert report["status"] == "completed", report["error"]
    assert model.calls == report["budget"]["model_calls"] == 3
    assert report["budget"]["tool_calls"] == 1
    assert report["budget"]["observed_input_tokens"] == 60
    assert report["budget"]["usage_complete"] is True
    assert report["answer"]["claims"][0]["evidence_refs"] == ["e1"]


def test_tool_budget_failure_preserves_rows_and_missing_slots(monkeypatch):
    async def runner(agent, text, **kwargs):
        tool = agent.tools[0]
        args = '{"recipe_id":"incidents","node_id":"acme"}'
        await tool.on_invoke_tool(tool_context(), args)
        await tool.on_invoke_tool(tool_context(), args)
        raise AssertionError("second tool call must fail")
    monkeypatch.setattr("seocho.agent.collaboration.Runner.run", runner)
    report = run(CollaborationExperiment(model=None, retrieve=lambda r, n: [{"evidence_id": "e1"}],
                                        recipes=RECIPE_PATHS, workspace_id="w", limits=Limits(max_tool_calls=1)).run(
        question="q", anchors=["acme"], required_slots=["status"], mode="single"))
    assert report["status"] == "budget_exhausted"
    assert report["error"] == "tool_budget"
    assert report["answer"] is None
    assert report["packets"][0]["rows"] == [{"evidence_id": "e1"}]
    assert report["packets"][0]["status"] == "failed_or_cancelled"
    assert report["packets"][0]["missing_slots"] == ["status"]


def test_task_allowance_is_enforced_inside_shared_budget():
    from agents import ModelSettings
    ledger = Ledger(Limits(max_calls=10))

    async def response(*args, **kwargs):
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=2, output_tokens=1))

    model = BudgetedModel(SimpleNamespace(get_response=response), ledger,
                          role="RetrievalWorker:t1", max_calls=1)
    run(model.get_response("s", "q", ModelSettings(), [], None, [], None))
    with pytest.raises(BudgetExceeded, match="task_call_budget"):
        run(model.get_response("s", "q", ModelSettings(), [], None, [], None))
    assert ledger.calls == 1
    assert ledger.receipts[0]["agent_role"] == "RetrievalWorker:t1"


def test_admission_and_timeout_fail_before_unbudgeted_dispatch():
    from agents import ModelSettings
    calls = []

    async def response(*args, **kwargs):
        calls.append(1)
        await asyncio.sleep(0.2)
        return SimpleNamespace(usage=None)

    ledger = Ledger(Limits(max_admission_units=1))
    with pytest.raises(BudgetExceeded, match="admission_budget"):
        run(BudgetedModel(SimpleNamespace(get_response=response), ledger).get_response(
            "s", "q", ModelSettings(), [], None, [], None))
    assert calls == []
    ledger = Ledger(Limits(timeout_seconds=0.01))
    with pytest.raises(asyncio.TimeoutError):
        run(BudgetedModel(SimpleNamespace(get_response=response), ledger).get_response(
            "s", "q", ModelSettings(), [], None, [], None))
    assert ledger.calls == ledger.usage_missing_calls == 1
    assert ledger.receipts[0]["status"] == "failed_or_cancelled"


def test_single_worker_can_use_global_budget_instead_of_default_task_allowance(monkeypatch):
    async def runner(agent, text, **kwargs):
        if agent.name == "RetrievalWorker":
            payload = json.loads(text)
            assert payload["task"]["max_model_calls"] == 11
            assert kwargs["max_turns"] >= 11
            output = Selection(evidence_refs=[], missing_slots=["status"])
        else:
            output = Answer(claims=[], missing_slots=["status"])
        return SimpleNamespace(final_output=output)
    monkeypatch.setattr("seocho.agent.collaboration.Runner.run", runner)
    report = run(CollaborationExperiment(model=None, retrieve=lambda r, n: [], recipes=RECIPE_PATHS,
                                        workspace_id="w", limits=Limits(max_calls=12)).run(
        question="q", anchors=["acme"], required_slots=["status"], mode="single"))
    assert report["status"] == "completed", report["error"]


@pytest.mark.parametrize("mode", ["sdk_manager", "sdk_handoff"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_native_coordination_real_sdk_context_and_shared_budget(mode, json_mode):
    from agents import Model, ModelResponse, Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    class ScriptedCoordination(Model):
        async def get_response(self, system_instructions, input, model_settings,
                               tools, output_schema, handoffs, tracing, **kwargs):
            text = json.dumps(input, default=str)
            names = [t.name for t in tools]
            if handoffs:
                name, arguments = handoffs[0].tool_name, '{}'
            elif 'collect_evidence' in names and 'function_call_output' not in text:
                name, arguments = 'collect_evidence', '{"input":"INJECTED_DELEGATION"}'
            elif 'execute_task' in names and 'function_call_output' not in text:
                assert 'INJECTED_DELEGATION' not in text
                assert 'required_slots' in text and 'recipes' in text
                name, arguments = 'execute_task', json.dumps({'task': task().model_dump()})
            elif 'retrieve_evidence' in names and 'function_call_output' not in text:
                name, arguments = 'retrieve_evidence', '{"recipe_id":"incidents","node_id":"acme"}'
            else:
                final = (Answer(claims=[Claim(text='Supported', evidence_refs=['e1'])], missing_slots=[])
                         if 'Answer only' in system_instructions else
                         Selection(evidence_refs=['e1'], missing_slots=[]))
                output = [ResponseOutputMessage(id='msg', type='message', role='assistant', status='completed',
                           content=[ResponseOutputText(type='output_text', text=final.model_dump_json(), annotations=[])])]
                return ModelResponse(output=output, usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15), response_id='r')
            return ModelResponse(output=[ResponseFunctionToolCall(type='function_call', name=name,
                                 call_id='call', arguments=arguments)],
                                 usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15), response_id='r')

        async def stream_response(self, *args, **kwargs):
            raise NotImplementedError
            yield

    experiment = CollaborationExperiment(model=ScriptedCoordination(),
        retrieve=lambda r, n: [{'evidence_id': 'e1', 'source': 'original'}],
        recipes=RECIPE_PATHS, workspace_id='w', json_object_mode=json_mode)
    report = run(experiment.run(question='q', anchors=['acme'], required_slots=['status'], mode=mode))
    assert report['status'] == 'completed', report['error']
    assert report['answer']['claims'][0]['evidence_refs'] == ['e1']
    assert report['budget']['model_calls'] == (7 if mode == 'sdk_manager' else 6)
    assert report['budget']['tool_calls'] == 3
    assert report['coordination_metrics']['native_handoffs'] == (mode == 'sdk_handoff')
    assert report['coordination_metrics']['agent_tool_calls'] == (mode == 'sdk_manager')
    assert report['context_policy'] == 'task_and_evidence_packets'
    assert all(e['workspace_id'] == 'w' for e in report['coordination_events'])
    assert all(r['context_bytes'] > 0 for r in report['budget']['receipts'])
    experiment.limits = Limits(max_calls=2)
    exhausted = run(experiment.run(question='q', anchors=['acme'], required_slots=['status'], mode=mode))
    assert exhausted['status'] == 'budget_exhausted', exhausted['error']
    assert exhausted['budget']['model_calls'] == 2
    assert exhausted['answer'] is None


def test_accumulated_model_context_is_bounded_before_dispatch():
    from agents import ModelSettings
    dispatched = []

    async def response(*args, **kwargs):
        dispatched.append(True)
        return SimpleNamespace(usage=None)

    ledger = Ledger(Limits(max_context_bytes=100))
    with pytest.raises(BudgetExceeded, match='context_budget'):
        run(BudgetedModel(SimpleNamespace(get_response=response), ledger).get_response(
            'instructions', [{'role': 'user', 'content': 'x' * 101}], ModelSettings(), [], None, [], None))
    assert ledger.calls == 0
    assert dispatched == []
