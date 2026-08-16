import re

filename = 'tests/seocho/test_cypher_builder.py'
with open(filename, 'r') as f:
    content = f.read()

# Revert the accidental substitution on `client.ask(..., database = "neo4j", **kwargs)`
content = re.sub(r'database\s*=\s*"neo4j", \*\*kwargs', r'database="neo4j"', content)
content = re.sub(r'database="neo4j"\)', r'database="neo4j")', content) # just cleanup if needed

with open(filename, 'w') as f:
    f.write(content)
