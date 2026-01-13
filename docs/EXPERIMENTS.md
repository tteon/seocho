# Experiments Design

This document explains the evaluation framework design for the Graph RAG system.

---

## Overview

We evaluate three retrieval methods in isolation and combination:

| Method | Database | Strength |
|--------|----------|----------|
| **LPG** | Neo4j | Structured facts, relationships |
| **RDF** | Neo4j | Ontology, type hierarchies |
| **HYBRID** | LanceDB | Semantic + keyword search |

---

## Macro Experiments

System-level comparisons answering: "Which overall configuration works best?"

### M1: Full System with Manager
```
Orchestrator Agent
├── LPG Sub-Agent (facts)
├── RDF Sub-Agent (definitions)
└── HYBRID Sub-Agent (context)
```
**Hypothesis**: Best overall performance via specialized routing.

### M2: Full System Single Agent
One agent with all tools.
**Hypothesis**: Simpler but may have routing confusion.

### M3: LPG + HYBRID (No RDF)
**Hypothesis**: Sufficient for factual queries without ontology.

### M4: RDF + HYBRID (No LPG)
**Hypothesis**: Semantic understanding without structured facts.

---

## Ablation Study

Component-level analysis answering: "What does each method contribute?"

| ID | Config | Tests |
|----|--------|-------|
| A1 | LPG only | Structured retrieval in isolation |
| A2 | RDF only | Semantic retrieval in isolation |
| A3 | HYBRID only | Text retrieval baseline |
| A4 | LPG+RDF | Graph combination without text |
| A5 | LPG+HYBRID | Facts + text without ontology |
| A6 | RDF+HYBRID | Semantics + text without facts |

---

## Metrics Framework

### Answer Quality (from Opik)
- **AnswerRelevance**: Output addresses the question
- **Usefulness**: Practical value of the answer
- **Hallucination**: Fabricated information detection

### Retrieval Quality (Custom)
- **RoutingAccuracy**: Correct tool selection based on intent
- **ContextPrecision**: Retrieved content relevance
- **DatabaseSelectionQuality**: LPG vs RDF appropriateness

### Agent Behavior (Custom)
- **ConflictResolutionScore**: Hierarchy of Truth compliance
- **ToolCallQuality**: Query syntax correctness

---

## Hierarchy of Truth

When sources conflict:

1. **Definitions/Types** → Trust RDF
2. **Numbers/Facts** → Trust LPG
3. **General Context** → Use HYBRID

---

## Running Experiments

```bash
# Macro experiments only
python -m src.cli.evaluate --macro

# Ablation study only
python -m src.cli.evaluate --ablation

# Specific configuration
python -m src.cli.evaluate --modes lpg,hybrid

# Everything
python -m src.cli.evaluate --all
```

---

## Expected Insights

After running all experiments:

1. **M1 vs M2**: Manager architecture impact
2. **A1/A2/A3**: Individual method baselines
3. **A4/A5/A6**: Synergy effects between methods
4. **Metric correlations**: Which metrics predict answer quality
