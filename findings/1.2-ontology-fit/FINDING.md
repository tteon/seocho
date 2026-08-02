# 1.2  Is FIBO the right ontology for these questions?

**✔ supported**

## Question

Before asking whether the ontology helped, does it even cover what the corpus asks about?

## Hypothesis, written before the run

FIBO is the industry reference model for exactly this domain, so its vocabulary should name what the questions are about and its own competency questions should be the same kind of question.

## Method

Two independent checks. Vocabulary coverage: how much of each category's question vocabulary FIBO can name. Question similarity: competency questions generated mechanically from FIBO's classes and properties, compared to the real questions by meaning, against a control of how similar the real questions are to each other.

## Measured

| | |
|---|---|
| Shareholder return | 0.721 (control 0.816) |
| Financials | 0.669 (control 0.803) |
| Accounting | 0.663 (control 0.790) |
| Legal | 0.655 (control 0.792) |
| Footnotes | 0.647 (control 0.792) |
| Company overview | 0.615 (control 0.777) |
| Governance | 0.615 (control 0.786) |
| Risk | 0.592 (control 0.831) |

Artifact: `outputs/evaluation/mdm_fedcat/log2026-cq-similarity-v1/cq_similarity.json`
Trace: `outputs/evaluation/mdm_fedcat/log2026-cq-similarity-v1/trace.jsonl`

## Reading

_Interpretation, separate from the measurement above._

Supported with one clean exception. Question similarity runs 0.59 to 0.72 against a within-corpus control of 0.78 to 0.83, so FIBO asks the same kind of question the corpus asks, at roughly four fifths of the similarity two real questions have to each other.

Risk is the exception and it is a clean one: lowest similarity at 0.59 and the highest internal control at 0.83, which is the signature of a coherent topic that the ontology does not cover. The vocabulary measurement agrees — cybersecurity, cyber and ERM are absent from real FIBO. Risk should be treated as out of scope rather than as an ontology failure.

Separately, FIBO's chosen labels are not the words the filings use. Of 279 alias pairs whose counts can be trusted, the alias beats the formal label in 43: LLC over limited liability company, EBITDA over its expansion, parent company over total controlling interest party. This is a register difference rather than a coverage gap — FIBO declares all of those itself — and it is why the synonym layer had to be a separate condition rather than folded in.

## What this does not support

Similarity of phrasing and topic, not of answerability. A high score means FIBO asks this kind of question, not that a graph built from it can answer one.

## Reproduce

```bash
python3 experiments/minimal/cq_similarity.py && python3 experiments/minimal/ontology_task_fit.py && python3 experiments/minimal/alias_register.py
```
