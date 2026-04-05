from agents import Agent
def create_reasoning_agent(handoffs) -> Agent:
    return Agent(
        name="ReasoningAgent",
        instructions="You perform multi-hop graph reasoning. Extract paths and synthesize an explanation.",
        handoffs=handoffs
    )
