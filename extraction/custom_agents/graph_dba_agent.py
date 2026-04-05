import logging
from agents import Agent, function_tool, RunContextWrapper

logger = logging.getLogger(__name__)

def create_graph_dba_agent(db_name: str, schema_info: str, neo4j_conn) -> Agent:
    _db = db_name
    _schema = schema_info

    @function_tool
    def query_db(context: RunContextWrapper, query: str) -> str:
        shared_mem = getattr(getattr(context, "context", None), "shared_memory", None)
        if shared_mem is not None:
            cached = shared_mem.get_cached_query(_db, query)
            if cached is not None:
                return f"[CACHED] {cached}"
        result = neo4j_conn.run_cypher(query, database=_db)
        if shared_mem is not None:
            shared_mem.cache_query_result(_db, query, result)
        return result

    @function_tool
    def get_schema() -> str:
        return _schema

    agent = Agent(
        name=f"Agent_{_db}",
        instructions=(
            f"You are a knowledge graph specialist for the '{_db}' database.\n\n"
            f"Schema:\n{_schema}\n\n"
            "When answering questions:\n"
            "1. Use get_schema() to verify available node labels and relationships.\n"
            "2. Use query_db() to execute Cypher queries against your database.\n"
            "3. Provide factual answers based on query results.\n"
            "4. If the question is outside your database's scope, state that clearly."
        ),
        tools=[query_db, get_schema],
    )
    logger.info("Created agent for database '%s'.", _db)
    return agent
