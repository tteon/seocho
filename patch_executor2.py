with open('src/seocho/query/executor.py', 'r') as f:
    content = f.read()

content = content.replace(
    'def __init__(self, *, graph_store: Any, database: str) -> None:\n        self.graph_store = graph_store\n        self.database = database',
    'def __init__(self, *, graph_store: Any, database: str, workspace_id: str = "default") -> None:\n        self.graph_store = graph_store\n        self.database = database\n        self.workspace_id = workspace_id'
)

with open('src/seocho/query/executor.py', 'w') as f:
    f.write(content)
