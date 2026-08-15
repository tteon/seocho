"""AgentOS: the five pillars, held as unit contracts.

Scalability is the design constraint, so the tests that matter most here
are the concurrent ones: N sessions sharing one layer must stay isolated
(memory), bounded (execution/scheduling), and individually budgeted
(resource) — with tenancy pinned no matter what the model sends.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from seocho.operating_layer import BudgetExceededError, SeochoOS
from seocho.ontology import NodeDef, Ontology, P, RelDef

pytest.importorskip("agents", reason="openai-agents SDK not installed")


@pytest.fixture
def ontology() -> Ontology:
    return Ontology(
        name="agentos_test", graph_model="lpg",
        nodes={"Account": NodeDef(properties={"acct_no": P(int, unique=True)})},
        relationships={"TRANSFER": RelDef(source="Account", target="Account")},
    )


class RecordingStore:
    """Fake governed store: records params, optionally blocks to hold permits."""

    def __init__(self, rows=None, gate: threading.Event | None = None):
        self.rows = rows if rows is not None else [{"n": 1}]
        self.calls: list[dict] = []
        self.gate = gate
        self.inflight = 0
        self.max_seen = 0
        self._lock = threading.Lock()

    def query(self, cypher, *, params=None, database=None,
              enforce_workspace_filter=False):
        with self._lock:
            self.inflight += 1
            self.max_seen = max(self.max_seen, self.inflight)
        try:
            self.calls.append({"cypher": cypher, "params": dict(params or {}),
                               "enforced": enforce_workspace_filter,
                               "database": database})
            if self.gate is not None:
                self.gate.wait(timeout=5)
            return list(self.rows)
        finally:
            with self._lock:
                self.inflight -= 1


def make_os(ontology, store, **kw):
    defaults = dict(database="testdb", workspace_id="acme")
    defaults.update(kw)
    return SeochoOS(ontology=ontology, graph_store=store, **defaults)


# -- memory: conversation namespace ---------------------------------------

def test_sdk_session_protocol_round_trip(ontology):
    os_layer = make_os(ontology, RecordingStore())
    session = os_layer.session("s1")

    async def scenario():
        await session.sdk_session.add_items([{"role": "user", "content": "hi"}])
        await session.sdk_session.add_items([{"role": "assistant", "content": "yo"}])
        items = await session.sdk_session.get_items()
        assert [i["role"] for i in items] == ["user", "assistant"]
        assert (await session.sdk_session.pop_item())["role"] == "assistant"
        await session.sdk_session.clear_session()
        assert await session.sdk_session.get_items() == []

    asyncio.run(scenario())


def test_sessions_are_private_to_each_other(ontology):
    os_layer = make_os(ontology, RecordingStore())
    a, b = os_layer.session("a"), os_layer.session("b")

    async def scenario():
        await a.sdk_session.add_items([{"role": "user", "content": "secret"}])
        assert await b.sdk_session.get_items() == []

    asyncio.run(scenario())
    assert os_layer.session("a") is a          # stable handle per id


# -- governance/tenancy: pinning ------------------------------------------

def test_workspace_params_are_pinned_not_trusted(ontology):
    store = RecordingStore()
    os_layer = make_os(ontology, store)
    session = os_layer.session("s")
    payload = os_layer.execute_query(
        session, "MATCH (n:Account) WHERE n._workspace_id = $workspace_id "
                 "RETURN n LIMIT 1",
        json.dumps({"workspace_id": "SOMEONE_ELSE", "ws": "SOMEONE_ELSE",
                    "acct": 7}))
    call = store.calls[0]
    assert call["params"]["workspace_id"] == "acme"      # injected value lost
    assert call["params"]["ws"] == "acme"
    assert call["params"]["acct"] == 7                   # benign params kept
    assert call["enforced"] is True                      # fail-closed reads
    assert json.loads(payload)["row_count"] == 1


def test_row_cap_disclosed_as_truncation(ontology):
    store = RecordingStore(rows=[{"n": i} for i in range(80)])
    os_layer = make_os(ontology, store, row_cap=50)
    payload = json.loads(os_layer.execute_query(
        os_layer.session("s"), "MATCH ... RETURN n LIMIT 100"))
    assert payload["row_count"] == 50
    assert payload["truncated"] is True                  # never silent


# -- execution/scheduling: one shared gate ---------------------------------

def test_admission_bounds_concurrent_sessions(ontology):
    gate = threading.Event()
    store = RecordingStore(gate=gate)
    os_layer = make_os(ontology, store, max_inflight=2, admission_wait_s=0.2)
    results: list[str] = []

    def worker(i: int) -> None:
        session = os_layer.session(f"s{i}")
        try:
            results.append(os_layer.execute_query(session, "MATCH n RETURN n"))
        except Exception as exc:
            # Admission rejection PROPAGATES out of the tool by design — the
            # SDK surfaces it to the model as a tool failure, not a payload.
            results.append(type(exc).__name__)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    # Let the two admitted calls park on the gate, the rest hit the deadline.
    import time

    time.sleep(0.6)
    gate.set()
    for thread in threads:
        thread.join(timeout=10)

    assert store.max_seen <= 2                           # the bound held
    rejected = [r for r in results if "QueryAdmissionRejected" in r]
    served = [r for r in results if "row_count" in r]
    assert len(served) == 2 and len(rejected) == 3


# -- resource: per-session budget -------------------------------------------

def test_budget_stops_the_run_with_a_structured_error(ontology):
    os_layer = make_os(ontology, RecordingStore(), token_budget=100)
    session = os_layer.session("s")

    class Usage:
        total_tokens = 60

    class Response:
        usage = Usage()

    hooks = session.hooks

    async def scenario():
        await hooks.on_llm_end(None, None, Response())      # 60 — fine
        with pytest.raises(BudgetExceededError):
            await hooks.on_llm_end(None, None, Response())  # 120 > 100

    asyncio.run(scenario())


# -- the SDK-facing assembly -------------------------------------------------

def test_build_agent_wires_governed_tool_and_guardrail(ontology):
    os_layer = make_os(ontology, RecordingStore())
    session = os_layer.session("s")
    agent = os_layer.build_agent(session, name="t")
    assert agent.name == "t"
    assert len(agent.tools) == 1
    tool = agent.tools[0]
    assert tool.name == "graph_query"
    assert tool.tool_input_guardrails            # ontology guardrail attached
    assert "truncated: true" in agent.instructions


# -- scheduling: the priority reserve ----------------------------------------

def test_reserve_keeps_capacity_for_high_priority(ontology):
    from seocho.operating_layer import PriorityAdmission

    gate = PriorityAdmission(max_inflight=4, reserved_for_high=2,
                             wait_seconds=0.05)
    # Normals may occupy at most max_inflight - reserved.
    assert gate.acquire("normal") and gate.acquire("normal")
    assert not gate.acquire("normal")          # third normal blocked
    # The reserve is there for high — both reserved permits admit.
    assert gate.acquire("high") and gate.acquire("high")
    assert not gate.acquire("high")            # pool truly exhausted
    gate.release("normal")
    assert gate.acquire("normal")              # release wakes the class


def test_zero_reserve_degrades_to_plain_bounded_admission(ontology):
    from seocho.operating_layer import PriorityAdmission

    gate = PriorityAdmission(max_inflight=2, reserved_for_high=0,
                             wait_seconds=0.05)
    assert gate.acquire("normal") and gate.acquire("high")
    assert not gate.acquire("high")            # no favoritism without reserve


def test_reserve_must_leave_normal_capacity(ontology):
    from seocho.operating_layer import PriorityAdmission

    with pytest.raises(ValueError):
        PriorityAdmission(max_inflight=2, reserved_for_high=2)
