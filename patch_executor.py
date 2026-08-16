with open('src/seocho/query/executor.py', 'r') as f:
    content = f.read()

content = content.replace(
    'records = self.graph_store.query(\n                plan.cypher,\n                params=plan.params,\n                database=self.database,\n            )',
    'records = self.graph_store.query(\n                plan.cypher,\n                params=plan.params,\n                database=self.database,\n                workspace_id=self.workspace_id,\n                enforce_workspace_filter=True,\n            )'
)

with open('src/seocho/query/executor.py', 'w') as f:
    f.write(content)
