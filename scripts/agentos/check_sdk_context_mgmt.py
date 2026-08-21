"""Does OpenAI-Agents-SDK context management actually work on top of SEOCHO?

Three live checks against the dept_eng/dept_sales homonym graph (deptlpg — "Atlas" is
Engineering's pipeline AND Sales' customer):

CHECK A (the SDK-idiomatic FIX pattern): ONE shared agent whose tool reads the tenant
  from ``RunContextWrapper[TenantCtx]`` — run twice with different ``context=``;
  each run must answer under ITS OWN tenant's meaning. Proves per-run local context
  reaches SEOCHO and selects the workspace.

CHECK B (current closure pattern, concurrency): two agents, each with tools CLOSED
  OVER one tenant-bound client, run CONCURRENTLY (asyncio.gather) on the same homonym
  question. Neither answer may carry the other tenant's markers — validates closure
  isolation + the engine's ContextVar run-context (B7) under interleaved runs.

CHECK C (the gap, demonstrated): a closure-pattern agent bound to dept_eng, invoked
  with ``context=TenantCtx('dept_sales')`` — the SDK context is silently IGNORED
  (no tool reads the wrapper), so the answer stays Engineering's. This is the wiring
  gap between our factory pattern and the SDK's per-run context contract.

Markers are deterministic (no judge): eng = pipeline/canary/rollback/sre/outage;
sales = renewal/contract/csm/sso/customer account.
"""

import asyncio
import importlib.util
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))

_spec = importlib.util.spec_from_file_location(
    "sem", os.path.join(_ROOT, "scripts", "agentos", "probe_tenant_semantics.py"))
_sem = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_sem)
except Exception:
    pass  # only needs mx._load_mara + dept_ontology; probe main() not run

_mx_spec = importlib.util.spec_from_file_location(
    "matrix", os.path.join(_ROOT, "scripts", "agentos", "e2e_arm_organ_matrix.py"))
_mx = importlib.util.module_from_spec(_mx_spec)
_mx_spec.loader.exec_module(_mx)
_mx._load_mara()

ENG = ["pipeline", "canary", "rollback", "sre", "outage"]
SALES = ["renewal", "contract", "csm", "sso", "customer account"]
Q = "What is Atlas, which team owns it, and what happened with it recently?"
DB, URI, PW = "deptlpg", "bolt://localhost:17687", "h0gatepass"

# module level so the SDK's get_type_hints() can resolve annotations (no
# `from __future__ import annotations` for the same reason)
from dataclasses import dataclass
from agents import Agent, RunContextWrapper, Runner, function_tool


@dataclass
class TenantCtx:
    workspace_id: str


def markers(ans: str):
    a = ans.lower()
    return [m for m in ENG if m in a], [m for m in SALES if m in a]


def make_client(ws: str):
    from seocho import Seocho
    return Seocho.local(
        _sem.dept_ontology(), llm="mara/gpt-oss-120b", graph=URI, neo4j_user="neo4j",
        neo4j_password=PW, api_key=os.environ.get("MARA_API_KEY"), workspace_id=ws)


async def main() -> None:
    from seocho.integrations.openai_agents import _mara_model

    model = _mara_model("gpt-oss-120b")
    clients = {"dept_eng": make_client("dept_eng"), "dept_sales": make_client("dept_sales")}
    results = {}

    # ---------- CHECK A: context-AWARE tool (the fix pattern) ----------------
    @function_tool
    def ask_memory(wrapper: RunContextWrapper[TenantCtx], question: str) -> str:
        """Answer a question from this tenant's governed knowledge graph."""
        ws = wrapper.context.workspace_id           # <- per-run SDK context selects tenant
        return str(clients[ws].ask(question, engine="structured", database=DB))

    shared_agent = Agent[TenantCtx](
        name="memory-agent",
        instructions="Answer using the ask_memory tool. Always call it exactly once.",
        tools=[ask_memory], model=model)

    print("=== CHECK A: one shared agent, per-run context= selects the tenant ===", flush=True)
    a_res = {}
    for ws in ("dept_eng", "dept_sales"):
        r = await Runner.run(shared_agent, input=Q, context=TenantCtx(ws), max_turns=4)
        ans = str(r.final_output)
        e, s = markers(ans)
        own = e if ws == "dept_eng" else s
        other = s if ws == "dept_eng" else e
        a_res[ws] = {"own_markers": own, "other_markers": other, "answer": ans[:180]}
        print(f"  [{ws}] own={own} OTHER={other} :: {ans[:110]}", flush=True)
    results["A_context_aware"] = a_res

    # ---------- CHECK B: closure pattern, concurrent two-tenant runs ---------
    def closure_agent(ws: str) -> Agent:
        client = clients[ws]

        @function_tool
        def ask_graph(question: str) -> str:
            """Answer a question from the knowledge graph."""
            return str(client.ask(question, engine="structured", database=DB))

        return Agent(name=f"agent-{ws}",
                     instructions="Answer using the ask_graph tool. Always call it exactly once.",
                     tools=[ask_graph], model=model)

    print("\n=== CHECK B: closure agents, CONCURRENT runs (isolation under interleaving) ===",
          flush=True)
    eng_run, sales_run = await asyncio.gather(
        Runner.run(closure_agent("dept_eng"), input=Q, max_turns=4),
        Runner.run(closure_agent("dept_sales"), input=Q, max_turns=4))
    b_res = {}
    for ws, r in (("dept_eng", eng_run), ("dept_sales", sales_run)):
        ans = str(r.final_output)
        e, s = markers(ans)
        own = e if ws == "dept_eng" else s
        other = s if ws == "dept_eng" else e
        b_res[ws] = {"own_markers": own, "other_markers": other, "answer": ans[:180]}
        print(f"  [{ws}] own={own} OTHER={other} :: {ans[:110]}", flush=True)
    results["B_closure_concurrent"] = b_res

    # ---------- CHECK C: the gap — context= silently ignored by closures -----
    print("\n=== CHECK C: closure agent (eng-bound) called with context=sales — ignored? ===",
          flush=True)
    r = await Runner.run(closure_agent("dept_eng"), input=Q,
                         context=TenantCtx("dept_sales"), max_turns=4)
    ans = str(r.final_output)
    e, s = markers(ans)
    ignored = bool(e) and not s
    results["C_gap_context_ignored"] = {"eng_markers": e, "sales_markers": s,
                                        "context_silently_ignored": ignored,
                                        "answer": ans[:180]}
    print(f"  eng_markers={e} sales_markers={s} -> context silently ignored: {ignored}",
          flush=True)

    out = os.path.join(_ROOT, "outputs", "agentos", "sdk_context_mgmt_check.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(results, open(out, "w"), indent=2)
    print(f"\n=== wrote {out} ===", flush=True)

    # verdict
    a_ok = all(v["own_markers"] and not v["other_markers"] for v in a_res.values())
    b_ok = all(v["own_markers"] and not v["other_markers"] for v in b_res.values())
    print(f"\nVERDICT: A(per-run context works)={a_ok}  B(concurrent isolation)={b_ok}  "
          f"C(closure ignores context=)={results['C_gap_context_ignored']['context_silently_ignored']}",
          flush=True)


if __name__ == "__main__":
    asyncio.run(main())
