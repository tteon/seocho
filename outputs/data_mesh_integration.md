# Data Mesh Integration: Neo4j & DataHub

This document outlines how the **SEOCHO** platform bridges the **Semantic Layer** (Neo4j) with the **Technical Metadata Catalog** (DataHub) to enable a Data Mesh architecture.

## 1. Conceptual Model

In a Data Mesh, we distinguish between the "Business Semantics" (what data means) and the "Technical Implementation" (where data lives).

| Feature | Neo4j (Knowledge Graph) | DataHub (Metadata Catalog) |
| :--- | :--- | :--- |
| **Role** | **Semantic Mesh** | **Technical Catalog** |
| **Entities** | `BusinessFunction`, `Product`, `Concept` | `Dataset`, `Database`, `Stream` |
| **Ontology** | **FIBO** (Financial Industry Business Ontology) | Standard Technical Schema |
| **Focus** | "How does 'Credit Risk' relate to 'Treasury'?" | "Where is the `daily_trades` table located?" |

## 2. Integration Workflow

The integration is bi-directional but distinct in purpose:

### A. Bottom-Up Sync (Technical -> Semantic)
1. **Ingestion**: DataHub ingests metadata from physical sources (MySQL, Kafka, Snowflake).
2. **Projection**: Key technical assets (e.g., critical tables) are projected into Neo4j as nodes.
3. **Enrichment**: In Neo4j, these nodes are mapped to FIBO concepts (e.g., `daily_trades` table $\rightarrow$ `fibo-fnd-dt-fd:Data`).

### B. Top-Down Governance (Semantic -> Technical)
1. **Definition**: Domain Owners define Data Products in Neo4j (e.g., `Liquidity Dashboard`).
2. **Policy**: Policies (e.g., "All Risk products must have PII tagging") are defined in the Graph.
3. **Enforcement**: These policies are pushed to DataHub as **Tags** or **Glossary Terms**.

## 3. Mock Data Structure (FIBO Style)

The `demos/data_mesh_mock.py` script generates a reference implementation of the Semantic Mesh:

```cypher
// Domain (Business Function)
(:`fibo-fnd-gao-obj:BusinessFunction` {name: 'Treasury'})

// Product (Data Product)
(:`fibo-fnd-arr-prod:Product` {name: 'Liquidity Dashboard'})

// Data (Dataset)
(:`fibo-fnd-dt-fd:Data` {name: 'Daily Cash Position'})
```

## 4. Tutorial Scenario: Financial Metadata

The script `demos/demo_fibo_metadata.py` demonstrates how to map a real-world **Bond Security Mapping** to the DataHub graph:

### A. Administrative Metadata (Governance)
- **Owner**: Compliance Officer (`urn:li:corpuser:compliance_officer`)
- **Properties**:
    - `Jurisdiction`: US
    - `TradableStatus`: Active
    - `RegulatoryClass`: HQLA

### B. Structural Metadata (Schema)
- **Fields**:
    - `ISIN` (Tagged with `FIBO.SovereignDebt`)
    - `CouponRate`
    - `MaturityDate`
    - `Issuer`

This simulation shows how "Administrative" data becomes Graph Edges (Ownership) and "Structural" data becomes Schema Nodes in the unified catalog.
