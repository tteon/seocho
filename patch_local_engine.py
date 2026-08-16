with open('src/seocho/local_engine.py', 'r') as f:
    content = f.read()

content = content.replace(
    'executor = GraphQueryExecutor(graph_store=self.graph_store, database=database)',
    'executor = GraphQueryExecutor(graph_store=self.graph_store, database=database, workspace_id=self.workspace_id)'
)

content = content.replace(
    'active_executor = executor or GraphQueryExecutor(\n            graph_store=self.graph_store,\n            database=database,\n        )',
    'active_executor = executor or GraphQueryExecutor(\n            graph_store=self.graph_store,\n            database=database,\n            workspace_id=self.workspace_id,\n        )'
)

content = content.replace(
    'explained = (executor or GraphQueryExecutor(\n                graph_store=self.graph_store, database=database,\n            )).explain(QueryPlan(question="", cypher=cypher, params=params))',
    'explained = (executor or GraphQueryExecutor(\n                graph_store=self.graph_store, database=database, workspace_id=self.workspace_id,\n            )).explain(QueryPlan(question="", cypher=cypher, params=params))'
)

with open('src/seocho/local_engine.py', 'w') as f:
    f.write(content)
