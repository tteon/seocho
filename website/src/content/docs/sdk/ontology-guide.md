---
title: Ontology Guide
description: Design the schema that drives your knowledge graph
---

# Ontology Guide

Your ontology is the single source of truth — it defines what exists, how things connect, and what constraints apply.

## Nodes

```python
"Company": NodeDef(
    description="A registered business entity",
    properties={
        "name":         P(str, unique=True),
        "ticker":       P(str, index=True),
        "founded_year": P(int),
        "is_public":    P(bool),
    },
    aliases=["Corp", "Firm"],
    same_as="schema:Organization",
)
```

**Property shorthand `P()`:**

| Example | Effect |
|---------|--------|
| `P(str)` | String, no constraints |
| `P(str, unique=True)` | UNIQUE + required |
| `P(str, index=True)` | Indexed for fast lookups |
| `P(int)` | Integer — validated after extraction |
| `P(str, required=True)` | Must be present |

## Relationships

```python
"WORKS_AT": RelDef(
    source="Person", target="Company",
    cardinality="MANY_TO_ONE",
    description="Employment relationship",
    same_as="schema:worksFor",
)
```

**Cardinality** drives validation and denormalization:

| Value | Meaning | Denorm safe? |
|-------|---------|-------------|
| `MANY_TO_MANY` | No limit (default) | Never embed |
| `MANY_TO_ONE` | Each source → at most 1 target | Embed target into source |
| `ONE_TO_MANY` | Each target → at most 1 source | Embed source into target |
| `ONE_TO_ONE` | Exclusive both ways | Embed both directions |

## JSON-LD: Save and Load

JSON-LD is the canonical storage format. `@context` connects your types to schema.org, SKOS, etc.

```python
# Save
ontology.to_jsonld("schema.jsonld")

# Load
ontology = Ontology.from_jsonld("schema.jsonld")

# Also supports YAML
ontology.to_yaml("schema.yaml")
ontology = Ontology.from_yaml("schema.yaml")
```

Example JSON-LD output:

```json
{
  "@context": {
    "schema": "https://schema.org/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "seocho": "https://seocho.dev/ontology/"
  },
  "@id": "seocho:my_domain",
  "@type": "seocho:Ontology",
  "name": "my_domain",
  "nodes": {
    "Company": {
      "sameAs": "schema:Organization",
      "properties": {
        "name": {"type": "string", "unique": true}
      }
    }
  }
}
```

## SHACL Validation

SHACL shapes are derived automatically — you never write them manually.

```python
# View derived shapes
shacl = ontology.to_shacl()

# Validate extracted data
errors = ontology.validate_with_shacl(extracted_data)
# ["Node 'p1' (Person).age: expected integer, got str"]
# ["Node 'p1' has 2 [:WORKS_AT] but cardinality is MANY_TO_ONE"]
```

Use `strict_validation=True` in `s.add()` to reject invalid data before writing.

## Confidence Scoring

```python
scores = ontology.score_extraction(extracted_data)
print(scores["overall"])  # 0.0–1.0
print(scores["nodes"][0]["details"])
# {"label_match": 1.0, "property_completeness": 0.8, "type_correctness": 1.0}
```

## Denormalization

Flatten graph data using cardinality-safe rules:

```python
plan = ontology.denormalization_plan()
# Person can embed Company fields (MANY_TO_ONE → safe)

flat = ontology.to_denormalized_view(nodes, relationships)
# Person now has company_name, company_ticker embedded

clean_nodes, clean_rels = ontology.normalize_view(flat)
# Back to normalized form
```

## Multi-Ontology

Bind different ontologies to different databases:

```python
s.register_ontology("finance_db", finance_ontology)
s.register_ontology("hr_db", hr_ontology)

s.add("revenue data...", database="finance_db")  # uses finance_ontology
s.add("employee data...", database="hr_db")       # uses hr_ontology
```

## Database Constraints

```python
# Generate Cypher
for stmt in ontology.to_cypher_constraints():
    print(stmt)
# CREATE CONSTRAINT constraint_Company_name_unique IF NOT EXISTS
#   FOR (n:Company) REQUIRE n.name IS UNIQUE

# Or apply directly
s.ensure_constraints(database="mydb")
```
