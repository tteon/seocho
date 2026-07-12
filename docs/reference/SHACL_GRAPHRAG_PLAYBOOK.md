# SHACL + GraphRAG Playbook

This document captures a practical approach for using SHACL as a contract layer between:

- Relational data (SQL DDL + CSV exports)
- RDF knowledge graphs (Turtle in a triple store)
- LLM-assisted transformation and query generation

## 1. Why SHACL in This Pipeline

SHACL works well as a structural bridge because it describes:

- Entity shapes (classes and properties)
- Constraints (types, cardinality, patterns, ranges)
- Relationships (including foreign-key style references)
- Rules (optional computed/inferred triples)

In this workflow, SHACL is the durable reference model. LLM output becomes more stable when it is constrained by shapes and explicit constraints.

## 2. SQL -> SHACL -> TARQL -> RDF Workflow

### Step 1: Export source assets

- Export SQL DDL from the source database
- Export table data as CSV files (or Excel package for smaller databases)

### Step 2: Generate SHACL from DDL (one-time per schema version)

Use an LLM to generate SHACL from DDL with strict requirements:

- Map SQL types to RDF/XSD datatypes
- Convert PK/FK structure into IRI-safe shape design
- Define constraints (`sh:minCount`, `sh:maxCount`, `sh:pattern`, etc.)
- Use stable IRIs for node/property/constraint shapes (avoid blank nodes where possible)
- Add `sh:name`, `sh:description`, and implementation notes for downstream mapping

Treat this SHACL file as the source-of-truth schema contract.

### Step 3: Generate TARQL from SHACL

Use the SHACL model to generate TARQL transformations for each table CSV.

Expected behavior:

- Primary keys -> deterministic entity IRIs
- Foreign keys -> object property IRIs
- Null/empty values -> omitted triples
- Correct datatype casting (`xsd:date`, `xsd:dateTime`, `xsd:decimal`, etc.)

### Step 4: Run conversion and validate

Run TARQL to produce Turtle, then validate with SHACL.

```bash
pyshacl -s hr_database_shacl.ttl \
  -d hr_database_complete.ttl \
  -f human
```

### Step 5: Load into triple store

```bash
curl -X POST -H "Content-Type: text/turtle" \
  --data-binary @hr_database_complete.ttl \
  http://localhost:3030/hr/data
```

## 3. Querying Strategy with LLM + SHACL

Preferred pattern:

1. Provide SHACL to the LLM
2. Ask the LLM to generate SPARQL for a specific report question
3. Execute SPARQL against the triple store
4. Return structured result (table/JSON/Turtle)
5. Let LLM render user-facing narrative from grounded results

This reduces hallucination risk because generated text is grounded in query results rather than free-form completion.

## 4. Structural vs Inferential Ontologies

Pragmatic split:

- SHACL: structural validity and rule-driven transformations
- OWL: formal logical inference semantics

Use SHACL when you need:

- Data contracts for ingestion/conversion
- Deterministic validation feedback
- Tooling-friendly shape definitions for agents/LLMs

Use OWL when you need:

- Formal reasoning semantics across class/property axioms

These approaches are complementary, not mutually exclusive.

## 5. Design Recommendations

- Keep SHACL as a reusable contract artifact per schema version
- Prefer parameterized IRI patterns for entity identity stability
- Keep frequently asked analytics as curated report queries (not unrestricted open queries)
- Separate:
  - Core schema (shapes/constraints)
  - Taxonomy (SKOS concepts/enums)
  - Rule modules (inference/computed triples)
- Track provenance for enriched narrative content that is not directly grounded in the graph

## 6. Common Validation Pitfalls

- Overly strict patterns (for example code fields that include hyphens)
- Datatype drift in CSV exports
- Missing optional-field handling for empty/NULL literals
- FK references not converted to canonical object IRIs

When violations are frequent but semantically acceptable, adjust shape constraints instead of forcing lossy source edits.

## 7. How This Fits SEOCHO

Within SEOCHO, this pattern aligns with:

- `extraction/schema_manager.py` for schema-driven operation
- `extraction/graph_loader.py` for graph ingestion
- Agent workflows that generate and execute graph queries
- Evaluation flows that need traceable, grounded answers

This playbook can be used as a baseline for enterprise SQL-to-KG onboarding and agent-grounded report generation.
