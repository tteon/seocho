# Pre-registration — condition C at 280 cases (tag s2)

Written and committed before the run starts. Nothing below has been measured
at this sample size on this condition.

    scale       280 cases, 35 per category across all eight — the *same* 280
                cases as tag s1, because select_cases() is deterministic on
                seed 42 and a fixed per-category quota. Identity of the two
                samples is what makes every A-versus-C comparison below a
                paired comparison, and it will be verified by case-id set
                equality before any analysis runs, not assumed
    condition   C only — real FIBO, 70 classes, the condition the alignment
                key was discovered on
    models      DeepSeek-V3.1, gpt-oss-120b, MiniMax-M2.7 (same three as s1)
    volume      840 extractions, ~4,200 model calls at s1's measured rate;
                C's prompt is longer than A's (70 class descriptions), so
                token cost per call is higher than s1's
    schedule    staged by provider quota: gpt-oss first, MiniMax after its s1
                share finishes, DeepSeek after the daily 1,500-call window
                resets (s1 consumed 1,040 today)
    tag         s2, isolating databases and workspaces from s1 and v2

## Why this run exists

Two of the study's central claims currently rest on sixteen cases:

1. The alignment key — anchor keying finding 6× the comparable pairs and 43×
   the disagreements of name keying — was found on condition C at n=16. Its
   replication on condition A is already registered (scale-up SC-H4), but A is
   not where it was found, and a result that only replicates away from its
   discovery condition is a different and weaker result.
2. The name-agreement gap between no-ontology and FIBO (A 0.339 vs C 0.203 at
   n=16) is the headline of Part 1 and has never been measured at a size where
   the interval is worth printing.

s1 supplies A at 280. This run supplies C at 280 on the identical case set,
which is the missing half of both claims.

## Hypotheses

**S2-H1 — the alignment key replicates at scale on its discovery condition.**
Anchor keying yields several times the comparable pairs of name keying on C at
280 cases, and reveals disagreements invisible to name matching, in
proportions comparable to n=16 (183 vs 30 pairs; 87 vs 2 disagreements).
*Disconfirmed if* the anchor advantage shrinks materially with scale, which
would mean the sixteen-case result was a small-sample artefact and the paper's
centre does not hold.

**S2-H2 — the name-agreement gap persists as a paired comparison.** A's
comparable-key rate exceeds C's on the same 280 cases, with a bootstrap
interval excluding zero.
*Disconfirmed if* the interval crosses zero, which would demote Part 1's
headline from a finding to a small-sample suggestion — and would be reported
as exactly that.

**S2-H3 — the scale-error rate is not a condition artefact.** Roughly a
quarter of anchored figures differed from their printed source by ×1000 or
×1e6 in the pooled v2 data. That rate on C at 280 should be of the same order.
*Disconfirmed if* the rate collapses or doubles, either of which would mean
the v2 figure was not stable enough to print.

**S2-H4 — FIBO's content advantage survives scale.** C captures at least as
much of the gold figures as A on the paired sample (v2: 0.292 vs 0.253), even
while losing on names.
*Disconfirmed if* A captures more at 280, which would remove the "the ontology
buys content at the price of names" reading and leave the ontology with no
measured extraction benefit at all.

## What will not be claimed

Same boundaries as s1's registration: correctness is "the figure appears in
the gold answer"; the category-balanced draw is not the corpus's own
proportions; nothing here re-tests synonyms or hierarchy (D and E stay at
n=16). Prompt-length is still uncontrolled between A and C — any C effect
could in principle be a longer-prompt effect, and the length control remains
unrun.

## Analysis fixed in advance

    python3 experiments/export_snapshots.py               --tag s2 --arms C
    python3 experiments/materialize_anchors.py            --tag s2
    python3 experiments/minimal/arm_results.py            --tag s2 --arms C
    python3 experiments/minimal/validity.py               --tag s2 --arms C
    python3 experiments/minimal/provenance_keying.py      --tag s2 --arms C
    python3 experiments/minimal/verification_value.py     --tag s2 --arms C
    python3 experiments/minimal/routing_ceiling.py        --tag s2 --arms C
    python3 experiments/load_categories.py                --tag s2

The paired A-versus-C comparison joins s1 and s2 artifacts on case id. Primary
outcome for S2-H1 is the ratio of anchor-keyed to name-keyed comparable pairs;
for S2-H2 the paired difference in comparable-key rate with a case-resampled
interval. Failures are recorded as failures per case and never imputed; N
attempted and N scored are reported for every table.
