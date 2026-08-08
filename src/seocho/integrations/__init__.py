"""Adapters that expose SEOCHO's core to external agent frameworks.

Kept out of the core on purpose. Every comparable layer draws the same line — Neo4j ships
`neo4j-graphrag` and `langchain-neo4j` separately, and Mem0 and Zep both describe themselves
as working alongside any orchestrator — because binding a schema layer to one framework halves
who can use it. Here it also protects the project's own thesis: if the graph and ontology are
the durable asset, the loop must be replaceable without touching them.

Each adapter is optional at import time; a missing framework raises only when its module is
imported, never when SEOCHO is.
"""
