# Pre-registration — the alignment contract (G-), extraction guardrails
# that make independent agents alignable by construction

Committed before any G-condition extraction. Prefix **G-**.

## The idea, and where it comes from

Every Part 1 result says the ontology does not align independent extractors;
every alignment win in this study came from provenance recovered AFTER
extraction (72–80% recoverable). The question this run asks: can the
extraction PROMPT be a contract that ships alignment with the output —
guardrails that make two models' graphs alignable by construction, whatever
the models are?

Each guardrail slot is the inverse of a measured failure:

    G1  verbatim source quote required per figure   <- anchor recovery ceiling
    G2  explicit unit-scale enum slot               <- 26.7% scale errors
    G3  deterministic id grammar (entity.metric.period) <- name fragmentation

## Conditions (pilot first, scale the winners)

Base = condition A's prompt (schema-free floor, the best aligner measured).
G1/G2/G3 add one slot each; G4 = all three. Pilot: 60 cases (seed-42 draw
from the balanced 280), three models, tags g0..g4. Everything else fixed.
Scale-up of any winning condition is a separate registration.

## Hypotheses

- **G-H1** · the quote slot raises the anchored share above the post-hoc
  recovery rate on the same cases, and anchored-alignment comparable pairs
  rise with it. *Disconfirmed if* models refuse or hallucinate quotes at a
  rate that erases the gain — quotes must be verified verbatim (exact
  substring match), and a fabricated quote counts as a violation, reported.
- **G-H2** · the scale slot cuts the rescaled-anchor share (26.7% baseline)
  by at least half. *Disconfirmed if* the slot is filled but wrong — scale
  is decided where the table header is, and if the model cannot read it into
  a dedicated slot, it cannot read it into the value either.
- **G-H3** · the id grammar raises name-key comparable rate above condition
  A's 0.29 floor. *Disconfirmed if* ids fragment anyway (grammar tokens
  still chosen freely) — which would mean naming cannot be contracted, only
  anchored.
- **G-H4** · guardrails are independent: G4 ≈ G1+G2+G3 effects without
  interference. *Disconfirmed if* slots compete for the model's attention
  and G4 underperforms its parts.

## Verification is mechanical

Quote slots verify by exact substring match against the case's references;
scale slots by comparison with the anchored token; ids by collision
counting. No judge anywhere.

## What will not be claimed

A pilot at 60 cases bounds direction, not effect size. The guardrails are
prompt-level; whether they survive different base prompts or non-financial
corpora is out of scope. This does not rehabilitate the ontology: the
contract slots are schema-agnostic and sit on the schema-free floor.
