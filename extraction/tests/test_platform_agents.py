import asyncio
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from platform_agents import PlatformSessionStore, BackendSpecialistAgent, FrontendSpecialistAgent


def test_session_store_append_and_clear():
    store = PlatformSessionStore(max_turns=3)
    session_id = "s1"
    store.append(session_id, "user", "hello")
    store.append(session_id, "assistant", "hi")
    history = store.get(session_id)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    store.clear(session_id)
    assert store.get(session_id) == []


def test_frontend_specialist_extracts_entity_candidates():
    agent = FrontendSpecialistAgent()
    payload = {
        "response": "ok",
        "trace_steps": [{"type": "SEMANTIC"}, {"type": "GENERATION"}],
        "semantic_context": {
            "matches": {
                "Neo4j": [
                    {
                        "database": "kgnormal",
                        "node_id": 1,
                        "display_name": "Neo4j",
                        "labels": ["Database"],
                        "final_score": 1.2,
                        "source": "fulltext",
                    }
                ]
            }
        },
    }
    ui = agent.build_ui_payload(mode="semantic", runtime_payload=payload)
    assert ui["trace_summary"]["SEMANTIC"] == 1
    assert ui["entity_candidates"][0]["question_entity"] == "Neo4j"


def test_backend_specialist_dispatches_modes():
    async def router_runner(payload):
        return {"response": f"router:{payload['message']}", "trace_steps": []}

    async def debate_runner(payload):
        return {"response": f"debate:{payload['message']}", "trace_steps": []}

    async def semantic_runner(payload):
        return {"response": f"semantic:{payload['message']}", "trace_steps": []}

    agent = BackendSpecialistAgent()
    out_router = asyncio.run(
        agent.execute(
            mode="router",
            router_runner=router_runner,
            debate_runner=debate_runner,
            semantic_runner=semantic_runner,
            request_payload={"message": "x"},
        )
    )
    out_debate = asyncio.run(
        agent.execute(
            mode="debate",
            router_runner=router_runner,
            debate_runner=debate_runner,
            semantic_runner=semantic_runner,
            request_payload={"message": "y"},
        )
    )
    out_semantic = asyncio.run(
        agent.execute(
            mode="semantic",
            router_runner=router_runner,
            debate_runner=debate_runner,
            semantic_runner=semantic_runner,
            request_payload={"message": "z"},
        )
    )

    assert out_router["response"] == "router:x"
    assert out_debate["response"] == "debate:y"
    assert out_semantic["response"] == "semantic:z"

