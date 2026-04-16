# Example Datasets

Sample data for SEOCHO E2E testing and evaluation.

## Files

| File | Records | Source | Description |
|------|---------|--------|-------------|
| `tutorial_filings_sample.json` | 10 | Bundled tutorial sample | Filing-domain onboarding sample with expected answers |

## Tutorial Sample Format

```json
{
  "id": "case_001",
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
4. Save results to `outputs/evaluation/e2e/`
5. Log all traces to Opik (if configured)

## Neo4j Database Naming

Neo4j requires: lowercase, no hyphens, no underscores, start with letter.

| Purpose | Database Name |
|---------|--------------|
| LPG evaluation | `seochoe2elpg` |
| RDF evaluation | `seochoe2erdf` |

## Boundary

- the bundled tutorial sample is for onboarding and local smoke checks
- benchmark and performance claims should use a user-supplied private corpus
- do not publish tutorial-sample results as benchmark evidence
