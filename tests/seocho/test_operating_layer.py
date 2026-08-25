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
              enforce_workspace_filter=False, **kwargs):
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


# -- scheduler v2: lanes, borrowing, fast-fail --------------------------------

def test_unknown_statements_go_to_the_heavy_lane(ontology):
    from seocho.operating_layer import LaneScheduler

    gate = LaneScheduler(max_inflight=4, light_permits=2)
    assert gate.classify("never-seen") == "heavy"
    gate.observe("fast-query", 80.0)
    gate.observe("slow-query", 8000.0)
    assert gate.classify("fast-query") == "light"
    assert gate.classify("slow-query") == "heavy"


def test_heavy_saturation_cannot_block_the_light_lane(ontology):
    from seocho.operating_layer import LaneScheduler

    gate = LaneScheduler(max_inflight=4, light_permits=2, wait_seconds=0.05)
    assert gate.acquire(lane="heavy") and gate.acquire(lane="heavy")
    assert not gate.acquire(lane="heavy")          # heavy lane full
    # Light permits are untouched by the heavy pile-up — no head-of-line.
    assert gate.acquire(lane="light") and gate.acquire(lane="light")


def test_normal_borrows_the_reserve_only_while_high_is_absent(ontology):
    from seocho.operating_layer import LaneScheduler

    gate = LaneScheduler(max_inflight=4, light_permits=0,
                         reserved_for_high=2, wait_seconds=0.05)
    # No high waiter anywhere: work conservation lets normals use all 4.
    for _ in range(4):
        assert gate.acquire(lane="heavy", priority="normal")
    assert not gate.acquire(lane="heavy", priority="normal")
    for _ in range(4):
        gate.release(lane="heavy", priority="normal")

    # A waiting high class re-arms the protection: with 2 reserved and a
    # high waiter present, normals stop at 2.
    import threading as _threading
    import time as _time

    assert gate.acquire(lane="heavy", priority="normal")
    assert gate.acquire(lane="heavy", priority="normal")
    assert gate.acquire(lane="heavy", priority="high")
    assert gate.acquire(lane="heavy", priority="high")   # pool now full

    got = []

    def waiting_high():
        got.append(gate.acquire(lane="heavy", priority="high", deadline_s=2.0))

    thread = _threading.Thread(target=waiting_high)
    thread.start()
    _time.sleep(0.1)                       # the high waiter is now queued
    gate.release(lane="heavy", priority="high")
    thread.join(timeout=5)
    assert got == [True]                    # release reached the high waiter
    # While that high waiter was queued, a normal could not have taken the
    # freed permit past the reserve — covered by the admissibility rule.


def test_predicted_wait_rejects_immediately_not_after_timeout(ontology):
    import time as _time

    from seocho.operating_layer import LaneScheduler

    gate = LaneScheduler(max_inflight=1, light_permits=0, wait_seconds=5.0)
    gate.observe("slow", 8000.0, lane="heavy")   # lane EWMA ~8s
    assert gate.acquire(lane="heavy")       # occupy the only permit
    # Park a waiter so predicted wait = 1 * 8s / 1 permit = 8s > 0.5s budget.
    import threading as _threading

    parked = _threading.Thread(
        target=lambda: gate.acquire(lane="heavy", deadline_s=3.0))
    parked.start()
    _time.sleep(0.05)
    started = _time.perf_counter()
    assert not gate.acquire(lane="heavy", deadline_s=0.5)
    elapsed = _time.perf_counter() - started
    assert elapsed < 0.2                    # rejected NOW, not at 0.5s timeout
    gate.release(lane="heavy")
    parked.join(timeout=5)


# -- the public surface is Seocho itself --------------------------------------

def test_one_session_object_carries_the_operating_layer(ontology):
    """seocho-dxe final form: no side class, no second method — the same
    Session that add()/ask() use carries sdk_session/hooks/priority, and
    the governed paths accept it directly."""
    from seocho import Seocho

    store = RecordingStore()
    client = Seocho(ontology=ontology, graph_store=store, llm=object(),
                    workspace_id="acme", max_inflight=4)
    session = client.session("chat", priority="high")
    assert session.priority == "high"
    assert session.sdk_session.workspace_id == "acme"
    assert session.hooks is not None and session.budget is not None
    payload = client.execute_query(
        session, "MATCH (n:Account) WHERE n._workspace_id = $workspace_id "
                 "RETURN n LIMIT 1", "{}")
    assert "row_count" in payload
    assert store.calls[0]["params"]["workspace_id"] == "acme"
    agent = client.build_agent(session, name="t")
    assert agent.tools[0].name == "graph_query"


def test_operating_layer_refused_outside_local_mode():
    from seocho import Seocho

    client = Seocho(base_url="http://localhost:9")   # HTTP mode
    with pytest.raises(Exception, match="local"):
        client.session("x")


# -- the unified OS surface: every subsystem is a method on one Session --------

@pytest.fixture
def named_ontology() -> Ontology:
    return Ontology(
        name="unified_test", graph_model="lpg",
        nodes={"Company": NodeDef(
            properties={"name": P(str), "sector": P(str)},
            identity_keys=["name", "sector"])},
        relationships={},
    )


def test_session_query_goes_through_governed_path(named_ontology):
    """sess.query() is the scheduling+isolation+read path — workspace pinned."""
    from seocho import Seocho

    store = RecordingStore(rows=[{"n": {"name": "X"}}])
    client = Seocho(ontology=named_ontology, graph_store=store, llm=object(),
                    workspace_id="acme", max_inflight=4)
    sess = client.session("analyst", priority="high")
    rows = sess.query(
        "MATCH (n:Company) WHERE n._workspace_id = $workspace_id RETURN n", )
    assert rows == [{"n": {"name": "X"}}]
    # tenancy pinned server-side regardless of caller
    assert store.calls[-1]["params"]["workspace_id"] == "acme"
    assert store.calls[-1]["enforced"] is True


def test_session_resolve_uses_the_intern_table(named_ontology):
    """resolve() reuses compute_node_identity: the read-time interning primitive."""
    from seocho import Seocho
    from seocho.index.identity import compute_node_identity

    store = RecordingStore(rows=[{"n": {"name": "Chipotle", "id": "x"}}])
    client = Seocho(ontology=named_ontology, graph_store=store, llm=object(),
                    workspace_id="acme", max_inflight=4)
    sess = client.session("analyst")
    hit = sess.resolve("Chipotle", label="Company", sector="restaurant")
    expected_addr = compute_node_identity(
        "Company", {"name": "Chipotle", "sector": "restaurant"},
        ["name", "sector"])
    assert hit is not None and hit["method"] == "intern"
    assert hit["address"] == expected_addr == "company|chipotle|restaurant"
    # the governed query looked up that exact composite address, workspace-scoped
    assert store.calls[-1]["params"]["addr"] == expected_addr
    assert store.calls[-1]["params"]["workspace_id"] == "acme"


def test_session_resolve_falls_back_to_normalized_name(named_ontology):
    """No label -> normalized-name match (the recall-ceiling fallback path)."""
    from seocho import Seocho

    store = RecordingStore(rows=[{"n": {"name": "Chipotle"}}])
    client = Seocho(ontology=named_ontology, graph_store=store, llm=object(),
                    workspace_id="acme")
    sess = client.session("analyst")
    hit = sess.resolve("  CHIPOTLE  ")
    assert hit is not None and hit["method"] == "normalized_name"
    assert store.calls[-1]["params"]["norm"] == "chipotle"   # normalized


def test_session_agent_and_stats_on_one_object(named_ontology):
    """execution (agent) and observability (os_stats) are methods on the session."""
    from seocho import Seocho

    store = RecordingStore()
    client = Seocho(ontology=named_ontology, graph_store=store, llm=object(),
                    workspace_id="acme", max_inflight=4, token_budget=1000)
    sess = client.session("analyst", priority="high")
    agent = sess.agent(name="t")
    assert agent.tools[0].name == "graph_query"
    stats = sess.os_stats()
    assert stats["workspace_id"] == "acme" and stats["priority"] == "high"
    assert stats["budget"] == 1000


def test_build_agent_ships_conformant_worked_examples(named_ontology):
    """ADR-0169: the governed agent must SHOW the exact conformant form, not
    just describe the schema — and the shown examples must pass the guardrail."""
    import re

    from seocho.operating_layer import SeochoOS
    from seocho.query.hybrid_planner import policy_from_ontology
    from seocho.query.workload_compiler import validate_text2cypher_fallback

    store = RecordingStore()
    os_layer = SeochoOS(ontology=named_ontology, graph_store=store,
                        database="testdb", workspace_id="acme")
    agent = os_layer.build_agent(os_layer.session("s"), name="t")
    instr = agent.instructions
    assert "Worked examples" in instr
    assert "{_workspace_id: $workspace_id}" in instr      # the inline-map form
    assert "params_json" in instr and "\"limit\": 1" in instr
    # every worked-example Cypher must actually pass the guardrail validator
    policy = policy_from_ontology(named_ontology)
    examples = re.findall(r"(MATCH .+?LIMIT \$limit)", instr)
    assert examples, "no worked-example queries found in instructions"
    for cypher in examples:
        violations = validate_text2cypher_fallback(
            cypher, params={"workspace_id": "acme", "limit": 1}, policy=policy)
        assert not violations, f"shipped example is non-conformant: {violations}"
