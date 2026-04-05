from agents import Agent
def create_ontology_agent() -> Agent:
    return Agent(
        name="OntologyDesigner",
        instructions="You help users design knowledge graph ontologies by analyzing domain descriptions, identifying entities/relationships, and outputting YAML schema.",
    )
