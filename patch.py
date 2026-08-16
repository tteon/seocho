with open('src/seocho/tools.py', 'r') as f:
    content = f.read()

content = content.replace(
    'records = graph_store.query(cypher, params=params, database=database)',
    'records = graph_store.query(\n                cypher,\n                params=params,\n                database=database,\n                workspace_id=workspace_id,\n                enforce_workspace_filter=True,\n            )'
)

with open('src/seocho/tools.py', 'w') as f:
    f.write(content)
