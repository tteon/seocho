from agents import Agent
def create_graph_builder_agent() -> Agent:
    return Agent(
        name="GraphBuilder",
        instructions="You construct graphs intelligently, detect duplicates, suggest missing relationships, and validate consistency against ontologies.",
    )
