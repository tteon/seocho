# Hypothesis ledger — what was registered, what was found, what was explained

This file is an index, not a registration. The registrations are the dated
files under `experiments/preregistration/`, each committed before the run it
governs, and nothing here adds to them. The ledger exists because three
documents now carry hypotheses numbered H1–H4 that are different hypotheses,
and because an earlier version of this file blurred the line this project
exists to keep sharp: it described an exploratory finding as pre-registered.
That version is replaced by this one.

Every hypothesis below carries one of three labels, and the label is the point:

- **registered** — direction and disconfirming outcome committed before the
  evidence. The file and its commit are the proof.
- **exploratory** — found in the data, not predicted. Real, but it has not
  survived a test it could have failed. Its registered replication is named.
- **post-hoc** — an explanation written after a result, offered as
  interpretation and never as a prediction.

Prefixes replace the colliding H-numbers: **SW-** (second sweep), **SC-**
(scale-up), **S2-** (condition-C scale), **AN-** (answering).

---

## Registrations on file

| File | Governs | Committed |
|---|---|---|
| `experiments/preregistration/2026-08-02-second-sweep.md` | v2 sweep, conditions A/C/D/E, 16 cases | before results, at 93/192 extractions |
| `experiments/preregistration/2026-08-02-scale-up.md` | s1 sweep, condition A, 280 cases | before the run; addendum at 115/840 |
| `experiments/preregistration/2026-08-02-condition-c-scale.md` | s2 sweep, condition C, 280 cases | before the run |
| `experiments/preregistration/2026-08-02-answering.md` | Part 2, answering under five evidence conditions | before the run |

A fifth registration predates all of these and governs a **different study**:
CLAUDE.md §19 pre-registers the Goldilocks direction for the FIBO
*module-size* sweep (non-ontology / small / medium / large). It is not a
registration of the A/C/D/E condition study, and this ledger's earlier
version was wrong to cite it as one.

---

## Second sweep (v2) — scored

Artifacts: `log2026.arm_results.v2`, `log2026.validity.v2`.

- **SW-H1** · equalising the property floor removes the period effect, not the
  agreement effect — **registered**, held.
- **SW-H2** · the subsumption hierarchy (E) raises agreement above plain FIBO
  (C) — **registered**, not separated; the registration itself named this the
  likelier outcome.
- **SW-H3** · FIBO's content advantage survives equal property slots —
  **registered**, held.
- **SW-H4** · no ontology condition escapes name fragmentation — **registered**,
  held. Every ontology condition agrees less on names than the one-class floor.

The floor's win is structural: one declared class gives two extractors nothing
to disagree about. Seventy classes give them seventy choices.

## The alignment key — exploratory, replication registered

Keying facts by their anchor in the source instead of by name multiplied
comparable pairs and revealed disagreements name-matching could not see
(`log2026.provenance_keying.v1`, 16 cases, condition C).

This was **not predicted**. It was found after the sweep and it is the study's
centre precisely because it could not have been retrofitted into the sweep's
registration. Its tests that can still fail are registered as **SC-H4** (does
the advantage survive 280 cases on condition A?) and **S2-H1** (does it
survive 280 cases on condition C, where it was found?).

## Scale-up (s1) — in flight, unread

Condition A, 280 cases, three models. Hypotheses **SC-H1** (the verifiable-fact
shortage is a sample-size problem), **SC-H2** (agreement-gated serving beats
serve-always on precision), **SC-H3** (the single-view ceiling does not move),
**SC-H4** (the shortage was a keying problem) — all **registered**, none
scored. SC-H1, SC-H3 and SC-H4 pull in different directions on purpose; the
registration says which outcome kills which paper.

## Condition-C scale (s2) — registered before launch

See its file. **S2-H1** through **S2-H4** cover the alignment key's
replication on the condition it was found in, and the first A-versus-C
comparison at a sample size with intervals worth reporting.

## Answering (Part 2) — registered before launch

See its file, which supersedes the condition table sketched in
`narrative/07-part2-search.md` and keeps its gating ladder. **AN-H1** through
**AN-H4**. The registered direction is *against* the graph: the graph is a
lossy compression of the same passages, and this repository has measured
vector ≈ hybrid ≫ graph three times on other corpora. A graph win would be the
surprising result.

## Post-hoc explanations — never predictions

- **SHACL's role** (`log2026.shacl_check.v1`) — the ontology's measurable
  contribution is making a class of error *detectable*, not making names
  converge; a one-class condition has almost nothing it can violate. Written
  after the counts existed.
- **Verification's value** (`log2026.verification_value.v1`) — cross-view
  agreement converts precision into a serving decision. Written after.
- **Routing's ceiling** (`log2026.routing_ceiling.v1`) — an oracle router
  barely beats the best fixed view, so retrieval's problem is not routing.
  Written after.
- **Why synonyms did nothing** (`log2026.question_axes.v1`) — almost no
  question needs a FIBO synonym to be answerable, and the ones that do are two
  abbreviations in one category. An explanation of SW-era nulls, found later.

---

## Sample-size boundary, stated once

Everything scored under the second sweep rests on sixteen cases per condition.
Differences there separated by bootstrap interval are claims; everything else
is "not separated at this size", never "no effect". The 280-case sweeps exist
to move the claims that matter off that footing, and the ledger will record
which ones survive.
