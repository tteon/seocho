"""Shared memory + OS organs under REAL OpenAI-Agents-SDK concurrency.

Upgrades the sequential organ probes to genuine multi-agent conditions:

PHASE 1 — shared memory + intern (cross-WRITER convergence):
  Two SDK writer agents concurrently ingest into ONE workspace, each holding HALF of
  a fact about the same entity ("Atlas Gateway"): W1 writes ownership, W2 writes the
  incident. A reader agent then asks a question answerable ONLY by joining both
  writers' facts through the converged canonical node ("Which team owns the entity
  involved in incident INC-7?"). Convergence census (one node, sources from both
  writers) + join answer = shared memory working through the allocator's
  content-addressed ids — deterministically, even though each writer had its own
  client (the address, not a shared in-process table, is what converges).

PHASE 2 — RCU pin under IN-FLIGHT mutation (upgrades probe 2's honesty caveat):
  Four reader agents run CONCURRENTLY (asyncio.gather) while a mutator coroutine
  renames relationship types on the live ontology MID-FLIGHT (not between requests).
  pin ON: prompt+policy from the frozen snapshot -> immune. pin OFF: live policy
  diverges from the DB-introspected prompt -> spurious guardrail rejections.
  Per-reader clients avoid metadata races; rejection/answer counts are the metric.
"""

import asyncio
import importlib.util
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))

_spec = importlib.util.spec_from_file_location(
    "sem", os.path.join(_ROOT, "scripts", "agentos", "probe_tenant_semantics.py"))
_sem = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_sem)
except Exception:
    pass
_mx_spec = importlib.util.spec_from_file_location(
    "matrix", os.path.join(_ROOT, "scripts", "agentos", "e2e_arm_organ_matrix.py"))
_mx = importlib.util.module_from_spec(_mx_spec)
_mx_spec.loader.exec_module(_mx)
_mx._load_mara()

from agents import Agent, Runner, function_tool  # noqa: E402  (module level: SDK type hints)

DB, URI, PW = "deptlpg", "bolt://localhost:17687", "h0gatepass"
WS = "shmem"

W1_DOC = ("Atlas Gateway is a core service. Atlas Gateway is owned by the Platform "
          "Team. The Platform Team maintains Atlas Gateway.")
W2_DOC = ("Atlas Gateway was involved in incident INC-7. INC-7 was a latency spike "
          "affecting Atlas Gateway last Tuesday.")
JOIN_Q = "Which team owns the entity that was involved in incident INC-7?"

READER_QS = [
    "Which team owns Atlas Gateway?",
    "What incident was Atlas Gateway involved in?",
    "What does the Platform Team own?",
    "Which entity was involved in INC-7?",
]
RENAMES = {"INVOLVED_IN": "PART_OF", "OWNED_BY": "MANAGED_BY"}


def make_client(ws=WS):
    from seocho import Seocho
    onto = _sem.dept_ontology()
    return Seocho.local(onto, llm="mara/gpt-oss-120b", graph=URI, neo4j_user="neo4j",
                        neo4j_password=PW, api_key=os.environ.get("MARA_API_KEY"),
                        workspace_id=ws), onto


async def main() -> None:
    from seocho.integrations.openai_agents import _mara_model
    from seocho.query.arm_config import ArmConfig
    model = _mara_model("gpt-oss-120b")
    results = {}

    # reset shared workspace
    c0, _ = make_client()
    c0._engine.graph_store.ensure_database(DB)
    drv = c0._engine.graph_store._driver
    with drv.session(database=DB) as s:
        s.run("MATCH (n {_workspace_id:$w}) DETACH DELETE n", w=WS)

    # ---------------- PHASE 1: concurrent writers -> converged join ----------
    print("=== PHASE 1: two SDK writers, one shared workspace, cross-writer join ===",
          flush=True)

    def writer_agent(tag: str):
        client, _ = make_client()          # own client, SAME workspace = shared memory

        @function_tool
        def store_document(text: str) -> str:
            """Store the given document into the shared knowledge graph."""
            r = client.add(text, source_type=f"writer_{tag}")
            md = r.metadata if hasattr(r, "metadata") else {}
            return f"stored nodes={md.get('nodes_created')} rels={md.get('relationships_created')}"

        return Agent(name=f"writer-{tag}",
                     instructions="Store the user's document EXACTLY as given using "
                                  "store_document, then reply 'done'.",
                     tools=[store_document], model=model)

    t0 = time.time()
    await asyncio.gather(
        Runner.run(writer_agent("w1"), input=f"Store this document: {W1_DOC}", max_turns=4),
        Runner.run(writer_agent("w2"), input=f"Store this document: {W2_DOC}", max_turns=4))
    print(f"  concurrent writes done in {time.time()-t0:.1f}s", flush=True)

    with drv.session(database=DB) as s:
        rows = list(s.run(
            "MATCH (n {_workspace_id:$w}) WHERE toLower(n.name) CONTAINS 'atlas gateway' "
            "RETURN labels(n)[0] AS l, n.id AS id, n.name AS name, "
            "size(coalesce(n._sources,[])) AS srcs", w=WS))
        gw_nodes = [dict(r) for r in rows]
    converged = len([r for r in gw_nodes if r["l"] not in
                     ("Document", "DocumentVersion", "Chunk", "Section")]) == 1
    print(f"  'Atlas Gateway' entity nodes: {[(r['l'], r['id'], r['srcs']) for r in gw_nodes if r['l'] not in ('Document','DocumentVersion','Chunk','Section')]}",
          flush=True)
    print(f"  CONVERGED to one canonical node: {converged}", flush=True)

    reader, r_onto = make_client()
    _mx.wire_rcu(reader, r_onto, WS)

    @function_tool
    def ask_graph(question: str) -> str:
        """Answer a question from the shared knowledge graph."""
        return str(reader.ask(question, engine="structured", database=DB))

    reader_agent = Agent(name="reader",
                         instructions="Answer using the ask_graph tool. Call it exactly once.",
                         tools=[ask_graph], model=model)
    jr = await Runner.run(reader_agent, input=JOIN_Q, max_turns=4)
    join_ans = str(jr.final_output)
    join_ok = "platform" in join_ans.lower()
    print(f"  JOIN across writers: ok={join_ok} :: {join_ans[:140]}", flush=True)
    results["phase1"] = {"converged": converged, "gateway_nodes": gw_nodes,
                         "join_ok": join_ok, "join_answer": join_ans[:200]}

    # ---------------- PHASE 2: in-flight mutation, pin ON vs OFF -------------
    print("\n=== PHASE 2: 4 concurrent readers, ontology mutated IN-FLIGHT ===", flush=True)

    async def run_arm(arm: ArmConfig):
        # one client PER reader (metadata is per-engine; avoids cross-run races),
        # all sharing one live ontology OBJECT (the shared mutable state RCU guards)
        shared_onto = _sem.dept_ontology()
        readers = []
        metas = []
        for i, q in enumerate(READER_QS):
            from seocho import Seocho
            cl = Seocho.local(shared_onto, llm="mara/gpt-oss-120b", graph=URI,
                              neo4j_user="neo4j", neo4j_password=PW,
                              api_key=os.environ.get("MARA_API_KEY"), workspace_id=WS)
            _mx.wire_rcu(cl, shared_onto, WS)
            cl._engine._structured_arm = arm

            def make_tool(client, sink):
                @function_tool
                def ask_shared(question: str) -> str:
                    """Answer a question from the shared knowledge graph."""
                    ans = str(client.ask(question, engine="structured", database=DB))
                    md = client.last_query_metadata
                    sink.append({"answered": md["answer_source"] == "structured",
                                 "rejected": md["structured"]["guardrail_rejected"],
                                 "violations": md["structured"]["guardrail_violations"][:2]})
                    return ans
                return ask_shared

            sink: list = []
            metas.append(sink)
            readers.append(Agent(name=f"reader-{i}",
                                 instructions="Answer using ask_shared. Call it exactly once.",
                                 tools=[make_tool(cl, sink)], model=model))

        async def mutator():
            await asyncio.sleep(float(os.getenv("MUT_DELAY", "4.0")))  # land while readers are in flight
            for old, new in RENAMES.items():
                if old in shared_onto.relationships:
                    shared_onto.relationships[new] = shared_onto.relationships.pop(old)
            return "mutated"

        outs = await asyncio.gather(
            *[Runner.run(a, input=q, max_turns=4) for a, q in zip(readers, READER_QS)],
            mutator())
        flat = [m[-1] if m else {"answered": False, "rejected": False, "violations": ["no_tool_call"]}
                for m in metas]
        return {"answered": sum(1 for m in flat if m["answered"]),
                "rejected": sum(1 for m in flat if m["rejected"]),
                "n": len(flat), "detail": flat,
                "answers": [str(o.final_output)[:100] for o in outs[:-1]]}

    for arm in (ArmConfig.governed(), ArmConfig.governed().without("pin")):
        r = await run_arm(arm)
        results[f"phase2_{arm.name}"] = r
        print(f"  [{arm.name:16s}] answered={r['answered']}/{r['n']} rejected={r['rejected']}"
              f" violations={[m['violations'] for m in r['detail'] if m['rejected']]}", flush=True)

    out = os.path.join(_ROOT, "outputs", "agentos", "sdk_shared_memory_organs.json")
    json.dump(results, open(out, "w"), indent=2)
    print(f"\n=== wrote {out} ===", flush=True)
    g = results.get("phase2_governed", {})
    np_ = results.get("phase2_governed-no-pin", {})
    print(f"\nVERDICT: shared-memory convergence={results['phase1']['converged']} "
          f"cross-writer join={results['phase1']['join_ok']} | in-flight mutation: "
          f"pinned answered={g.get('answered')}/{g.get('n')} rejected={g.get('rejected')} "
          f"vs no-pin answered={np_.get('answered')}/{np_.get('n')} rejected={np_.get('rejected')}",
          flush=True)


if __name__ == "__main__":
    asyncio.run(main())
