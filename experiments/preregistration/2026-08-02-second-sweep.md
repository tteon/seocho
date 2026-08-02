# Pre-registration — second extraction sweep

Committed while the run is in flight and before any of its results have been
read. The run's progress at the time of writing is recorded below so the claim
that this predates the result can be checked rather than trusted.

    run log     outputs/evaluation/sweep_logs/rx_0802_1153.log
    progress    93 of 192 extractions complete
    conditions  A (no schema), C (real FIBO), D (FIBO + synonyms),
                E (FIBO + subsumption hierarchy)
    tag         v2, isolating databases and workspaces from the first sweep

## Why this sweep exists

The first sweep had two defects that its own analysis exposed.

The property slots were not equal across conditions. The FIBO conditions
received `name`, `value` and `period` because I wrote that set by hand; the
no-schema condition declared only `name`. Any difference in how often a value or
a period was filled therefore measures which condition was handed the slot. This
sweep gives every condition the same three-property floor, and adds FIBO's own
declared datatype properties on top only where FIBO declares them.

There was no condition carrying FIBO's hierarchy. The prompt renderer emits a
flat list of class names, so the two FIBO conditions never saw that a chief
executive officer is an executive. Entailment over the shipped classes more than
doubles the classes with a parent in scope, from 7 to 15, and resolves relation
endpoints from 4 to 28, so condition E puts that structure into the class
descriptions where the extractor can read it.

## Hypotheses

Stated as directions, each with what would count as disconfirming.

**H1 — the property floor removes the period effect, not the agreement effect.**
With every condition declaring `period`, the fill-rate gap between FIBO and the
baseline should shrink substantially. The agreement result should not move,
because agreement is keyed on the name and no condition's naming changed.
*Disconfirmed if* the agreement ordering changes materially once properties are
equal, which would mean the first sweep's headline was a property artifact.

**H2 — the hierarchy raises agreement above plain FIBO.** Condition E should
beat condition C on comparable-key rate. Two extractors handed "a chief
executive officer is a kind of executive" have a shared, coarser name available
where they would otherwise pick different specific ones.
*Disconfirmed if* E is at or below C. Given that C and D were not separable from
each other, the honest expectation is that E will also fail to separate, and
that outcome is the more likely one.

**H3 — the content result survives.** In the first sweep, FIBO captured more of
the gold figures (0.292 against 0.253) and produced far higher agreement on
extracted values (0.520 against 0.147) while losing on names. Both should hold
with equal property slots. The value-overlap figure is the one most at risk,
because the baseline's missing `value` declaration may have depressed it.
*Disconfirmed if* the value-overlap gap closes once the baseline can declare a
value, which would mean the content advantage was also a property artifact.

**H4 — no condition escapes the fragmentation.** Every FIBO condition should
still produce more distinct fact names than the baseline, since none of them
reduces the class count.
*Disconfirmed if* E produces fewer distinct names than C, which would suggest
the hierarchy lets extractors converge on a shared level of description.

## What will not be claimed

The synonym and hierarchy conditions differ from plain FIBO by additions to the
prompt, so a difference could come from the prompt being longer rather than from
what was added. No condition here controls for prompt length, and any effect
attributed to synonyms or hierarchy carries that caveat until a length control
runs.

Sixteen cases and three models. Every difference will be reported with an
interval from resampling cases, and a difference whose interval crosses zero
will be described as not separated.

## Analysis fixed in advance

    comparability   experiments/minimal/arm_results.py --tag v2
    intervals       experiments/minimal/validity.py --tag v2 --arms A,C,D,E
    mechanism       experiments/minimal/mechanism.py --tag v2
    correctness     experiments/minimal/correctness.py --tag v2 --arms A,C,D,E

Primary comparison is the comparable-key rate on normalized names. The
identifier-keyed rate is secondary and reported alongside, because the
identifier is a string the model invented and its rate is a floor rather than a
measurement of the schema.
