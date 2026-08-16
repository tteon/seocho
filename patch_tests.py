import re

files_to_patch = [
    'tests/seocho/test_cypher_builder.py',
    'tests/seocho/test_session_agent.py',
    'tests/seocho/test_ontology_artifacts.py',
    'tests/seocho/test_arbiter.py',
    'tests/seocho/test_cypher_builder.py',
]

for filename in files_to_patch:
    with open(filename, 'r') as f:
        content = f.read()

    # Find all definitions of `def query(self, ...` and replace `**kwargs` or add `workspace_id=None, enforce_workspace_filter=False` if not present.
    # The simplest is to replace `database: str = "neo4j"):` with `database: str = "neo4j", **kwargs):`
    # and `database="neo4j"):` with `database="neo4j", **kwargs):`
    content = re.sub(r'database(: str)?\s*=\s*("neo4j"|None)\s*\)', r'database\1 = \2, **kwargs)', content)
    # Also handle `database=None):`
    content = re.sub(r'database=None\s*\)', r'database=None, **kwargs)', content)

    with open(filename, 'w') as f:
        f.write(content)
