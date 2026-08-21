"""The SEOCHO agent OS as one object.

`from seocho import Seocho` gives a client; `client.session(...)` gives ONE
Session that is the whole operating layer — every OS subsystem is a method on
it, not a free function taking the session as an argument:

    memory      sess.add(...) / sess.ask(...) / sess.resolve(mention)
    scheduling  sess.query(...)         # shared admission gate
    isolation   sess.query(...)         # workspace pinned server-side
    execution   sess.agent()            # governed openai-agents Agent
    resources   sess.budget / sess.priority
    observ.     sess.os_stats()

The governance knobs are opt-in on the constructor (all off by default). This
script needs no live database — it shows the surface and the wiring; the
`query`/`resolve` calls are what you would run against your DozerDB.

Run:  python examples/agent_designs/os_unified_surface.py
"""

from __future__ import annotations

from seocho import Seocho
from seocho.ontology import NodeDef, Ontology, P


def build_client() -> Seocho:
    ontology = Ontology(
        name="demo", graph_model="lpg",
        nodes={"Company": NodeDef(
            properties={"name": P(str), "sector": P(str)},
            identity_keys=["name", "sector"])},
        relationships={},
    )
    # Governance is opt-in: bound in-flight graph work, give one session a
    # per-run token budget, keep the interactive class ahead of batch.
    return Seocho(
        ontology=ontology,
        graph_store=_DemoStore(),          # your DozerDB GraphStore in real use
        llm=object(),                      # your LLM backend in real use
        workspace_id="acme",
        max_inflight=8,                    # scheduling: bounded concurrency
        reserved_for_high=1,               # scheduling: protect interactive
        token_budget=100_000,              # resources: per-session budget
        agent_row_cap=50,                  # memory: bounded, disclosed reads
    )


class _DemoStore:
    """Stand-in graph store so the example runs offline. A real deployment
    passes a DozerDB-backed GraphStore; the governed path is identical."""

    def query(self, cypher, *, params=None, database=None,
              enforce_workspace_filter=False):
        # Pretend the workspace holds one Company node, addressed by its
        # composite identity id (what the write-time interner would have set).
        if "n.id = $addr" in cypher:
            return [{"n": {"name": "Chipotle Mexican Grill, Inc.",
                           "sector": "restaurant",
                           "id": params.get("addr")}}]
        return []


def main() -> None:
    client = build_client()

    # ONE object, every subsystem a method on it.
    with client.session("analyst", priority="high") as sess:
        print("workspace :", sess.workspace_id)
        print("priority  :", sess.priority)

        # memory / interning: resolve a surface mention to its canonical node,
        # reusing the exact write-time identity function (read-time interning).
        hit = sess.resolve("Chipotle Mexican Grill, Inc.",
                           label="Company", sector="restaurant")
        print("resolve   :", hit["method"], "->", hit["address"])
        print("            node:", hit["node"]["name"])

        # scheduling + isolation: a governed read. The workspace is pinned
        # server-side; the shared admission gate bounds concurrency.
        rows = sess.query(
            "MATCH (n:Company) WHERE n._workspace_id = $workspace_id "
            "AND n.id = $addr RETURN n LIMIT 1",
            addr=hit["address"])
        print("query     :", len(rows), "row(s), workspace pinned by the layer")

        # execution: an agent whose only graph access is this governed session.
        agent = sess.agent(name="analyst_agent")
        print("agent     :", agent.name, "tool:", agent.tools[0].name)
        # In real use:
        #   from agents import Runner
        #   Runner.run(agent, "which sector is Chipotle in?",
        #              session=sess.sdk_session, hooks=sess.hooks)

        # observability: shared pool + this session's budget/priority.
        print("os_stats  :", sess.os_stats())


if __name__ == "__main__":
    main()
