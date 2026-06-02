# Evaluation Metrics — Vector vs. Graph (FinDER)

How we score answers in the vector-vs-graph retrieval study, and why. Every
answer (vector / graph / vector&graph, across all ontology arms) is scored the
**same way**, so the comparison is fair (no metric advantages one lane).

We score in **three tiers**, cheap → expensive, deterministic → semantic. The
first two are free and reproducible; the third is the headline correctness
metric.

---

## Why three metrics?

Each metric answers a different question, and each alone is misleading:

| Metric | Question it answers | Cost | Catches | Misses |
|--------|--------------------|------|---------|--------|
| `number_overlap` | "Did the answer surface the right numbers?" | free | numeric recall | reasoning, direction, correctness of use |
| `token_f1` | "How lexically close is the answer to the gold text?" | free | wording overlap | paraphrase, units, arithmetic |
| `judge_score` | "Is the answer actually correct vs the gold answer?" | 1 LLM call | semantic correctness, trend, final answer | (bounded by judge reliability) |

Reporting all three side by side keeps us honest: a high `judge_score` with low
`overlap` means the model got the answer right in different words; a high
`overlap` with low `judge_score` means it parroted numbers but reached the wrong
conclusion.

The ground truth in every case is the dataset's own gold `answer`
(`examples/datasets/finder/all_slices.csv`). All three metrics compare the candidate answer
against that gold answer.

---

## 1. `number_overlap` — numeric recall (deterministic)

**Purpose.** A fast, model-free proxy for "did the answer recover the financial
figures in the gold answer?" Financial QA is number-heavy, so numeric recall is
a cheap first signal.

**Implementation** (`finder_4arm_sample.py` / `finder_vector_arm.py`):
```python
_NUM_RE = re.compile(r"-?\$?\d[\d,]*\.?\d*(?:%| million| billion| thousand)?", re.IGNORECASE)
def _nums(t): return {n.replace(",","").strip().lower() for n in _NUM_RE.findall(str(t))}
overlap = len(_nums(gold) & _nums(answer)) / len(_nums(gold))   # 0.0 – 1.0
```
- Extract numeric tokens (handles `$`, `%`, `,`, "million/billion/thousand").
- Strip separators, lowercase, take the **set intersection**.
- Divide by the count of distinct gold numbers → recall in [0, 1].

**Example** (UR revenue CAGR): gold has 13 distinct numbers; an answer that
reproduces 6 of them → `overlap = 6/13 = 0.46`. `1.0` = every gold number
present; `0.0` = none (typically a "no data" answer).

**Strengths.** Deterministic, instant, reproducible, intuitive for finance.
**Limits.** Numbers only — ignores reasoning and whether the number was *used*
correctly; coincidental matches (e.g., a year `2023`) count; set-based, so order
and units are ignored.

---

## 2. `token_f1` — lexical overlap with the gold text (deterministic)

**Purpose.** Standard SQuAD-style text-similarity baseline: how much of the gold
answer's wording the candidate covers (precision × recall on tokens). Comparable
to numbers reported elsewhere in QA literature.

**Implementation** (`finder_judge.py`):
```python
def token_f1(pred, gold):
    norm = lambda s: re.sub(r"[^a-z0-9 ]"," ", str(s).lower()).split()
    p, g = norm(pred), norm(gold)
    common = Counter(p) & Counter(g)
    ns = sum(common.values())
    if ns == 0: return 0.0
    prec, rec = ns/len(p), ns/len(g)
    return 2*prec*rec/(prec+rec)
```
- Lowercase, strip punctuation, split to tokens.
- `precision` = shared tokens / candidate length; `recall` = shared / gold length.
- `F1` = harmonic mean → [0, 1].

**Strengths.** Deterministic, standard, no model needed.
**Limits.** Penalizes correct paraphrases and concise answers; rewards verbose
copying; blind to numeric/unit correctness. Useful as a sanity baseline, not as
the verdict.

---

## 3. `judge_score` — LLM-as-judge correctness (the headline metric)

**Purpose.** The metric that actually decides "is this answer correct?" — a
strong LLM compares the candidate to the gold answer and the question, focused on
factual correctness (final answer, key figures with units/period, and trend
direction), ignoring style.

**Implementation** (`finder_judge.py`): one LLM call per answer, `temperature=0`,
fixed prompt, structured JSON out. Verdict → score: `correct=1.0`,
`partial=0.5`, `incorrect=0.0`.

**Judge prompt** (`finder_judge@v1`):
```
SYSTEM:
You are a strict evaluator for financial question answering. You receive a
QUESTION, a GOLD answer (ground truth), and a CANDIDATE answer from a system.
Judge ONLY the factual correctness of CANDIDATE relative to GOLD — ignore
writing style, verbosity, and formatting.

Rules:
- GOLD is the ground truth; judge CANDIDATE against it.
- Weigh: (1) the final answer/conclusion, (2) the key financial figures with
  units and period, (3) the direction/trend (increase/decrease) when asked.
- Numbers match if equal after removing thousand separators and within normal
  rounding (54.4% ~= 54%). Wrong scale (thousands vs millions) or sign = mismatch.
- A CANDIDATE that says "no data"/"not in context"/refuses, or fabricates
  figures not in GOLD, is INCORRECT.
- Do NOT credit coincidental numbers (e.g., years) when the answer is wrong.
- Strict partial credit: only when core figures are right but the final answer
  is incomplete or a secondary part is wrong.

Output STRICT JSON only:
{"verdict":"correct|partial|incorrect","score":1.0,
 "matched":["..."],"missing_or_wrong":["..."],"rationale":"1-2 sentences"}

USER:
QUESTION:        {{query}}
GOLD ANSWER:     {{expected_answer}}
CANDIDATE ANSWER:{{answer}}
```

**Why structured + rationale.** The judge returns not just a score but
`matched`, `missing_or_wrong`, and a `rationale` — so every verdict is
**auditable** (we can see *why* an answer scored 0.5, not just the number).

**Example** (Fiserv EPS, case 620be13e): the candidate quoted the EPS figures but
then claimed P/E data was absent, contradicting the gold → judge verdict
`incorrect`, rationale: *"correctly quotes EPS but asserts absence of P/E /
growth implications, directly contradicting GOLD."* `number_overlap` alone would
have given partial credit for the matching EPS numbers; the judge catches the
wrong conclusion.

**Judge model.** grok-4.3, `temperature=0`. **Disclosure (important):** the
answer generator is also grok-4.3, so there is a potential self-preference bias.
It is **uniform across all lanes** (vector, graph, and hybrid answers are all
grok-generated), so the *relative* comparison stays fair; absolute scores may be
lenient. A cross-vendor judge (e.g. GPT) or a 2-judge panel would remove this and
is a drop-in change (`--judge-llm`).

**Limits.** Bounded by judge reliability; mitigated by `temperature=0` (repeatable),
a strict rubric, refusal-as-incorrect, and the audit fields. JSON parse failures
default to `incorrect` (no silent skips).

---

## How it runs end to end

1. **Generation** (`finder_vector_arm.py`, `finder_4arm_sample.py`) produces and
   saves one JSON per answer (vector / graph / vector&graph × ontology arm),
   each with `number_overlap` already computed.
2. **Scoring** (`finder_judge.py`) is a separate **offline pass** over the saved
   answers — it adds `token_f1` and the `judge_*` fields. Decoupling generation
   from judging means we can re-judge (different judge model, panel) without
   re-running the expensive retrieval/generation, and every lane is judged by the
   identical prompt + model.
3. **Aggregation** groups by `(slice, retrieval_mode, ontology_arm)` and reports
   `judge_score_mean`, `token_f1_mean`, `overlap_mean`, and the correct/partial/
   incorrect counts.

## Fairness & reproducibility (the rules we hold to)

- **Same metric for every lane** — vector, graph, and hybrid (and every ontology
  arm) are scored by the identical functions and the identical judge prompt.
- **Deterministic where possible** — overlap and token_f1 are pure functions;
  the judge runs at `temperature=0`. Same inputs reproduce the same scores.
- **Auditable** — judge rationale + matched/missing fields are stored, not just
  the score.
- **Disclosed asymmetries** — generator == judge (grok) is stated up front.
- **Read the three together** — no single metric is the verdict; divergences
  between them are themselves findings.
