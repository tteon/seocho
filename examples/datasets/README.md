# Example Datasets

Sample data for SEOCHO E2E testing and evaluation.

## Files

| File | Records | Source | Description |
|------|---------|--------|-------------|
| `finder_sample.json` | 10 | FinDER (SEC 10-K) | Financial Q&A with ground truth answers |

## FinDER Sample Format

```json
{
  "id": "finder_001",
  "text": "PTC Inc. reported total revenue of $2.1 billion...",
  "question": "What was PTC's revenue growth in fiscal 2023?",
  "expected_answer": "PTC reported total revenue of $2.1 billion...",
  "category": "Financials",
  "reasoning_type": "Subtraction"
}
```

Categories: Financials, Company Overview, Governance, Legal, Risk, Shareholder Return, Accounting

## Running E2E Evaluation

```bash
python examples/e2e_evaluation.py
```

This will:
1. Load the sample dataset
2. Index into separate LPG (`seochoe2elpg`) and RDF (`seochoe2erdf`) databases
3. Query both and compare answers
4. Save results to `examples/datasets/results/`
5. Log all traces to Opik (if configured)

## Neo4j Database Naming

Neo4j requires: lowercase, no hyphens, no underscores, start with letter.

| Purpose | Database Name |
|---------|--------------|
| LPG evaluation | `seochoe2elpg` |
| RDF evaluation | `seochoe2erdf` |
| FinDER LPG (existing) | `finderlpg` |
| FinDER RDF (existing) | `finderrdf` |

## Full FinDER Dataset

For the complete 5,703-record dataset:

```python
from datasets import load_dataset
ds = load_dataset("Linq-AI-Research/FinDER")["train"]
```

Requires HuggingFace token (gated dataset).
