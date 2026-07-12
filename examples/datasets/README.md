# Example Datasets

Small sample data for SEOCHO onboarding, smoke checks, and documented examples.

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

## Running The Quickstart Flow

```bash
uv run seocho run examples/run/quickstart.yaml
```

This will:

1. Load the quickstart schema and documents from `examples/run/`
2. Index them with the embedded graph backend
3. Ask the bundled questions
4. Write a run report under the generated `runs/` directory

For config-driven variants, see `docs/RUN_SPECS.md` and
`examples/run/sweep-enforcement/`.

## Boundary

- the bundled tutorial sample is for onboarding and local smoke checks
- benchmark and performance claims should use a user-supplied private corpus
- do not publish tutorial-sample results as benchmark evidence
