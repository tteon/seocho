---
draws_on:
  - log2026.arm_results.v2
  - log2026.validity.v1
  - log2026.correctness.v1
---

# 1.3  Giving the extractor an ontology lowers agreement between views

## What was predicted

The prediction was registered in this repository on 2026-05-31, months before
the run: a middle-sized ontology beats both no ontology and an over-large one.
An inverted U, not a rising line. It follows from the domain — if financial
figures fail to align because their meaning is not local, declaring the meaning
in advance should make them align — and it is the belief the industry acted on
when it built FIBO.

## What happened

Four conditions differing only in the schema handed to the extractor. Sixteen
cases, three models, every extraction scored. Documents, prompt, chunking, seed
and the property slots each class declares are held fixed.

| Condition | Facts a second model also describes | 95% interval |
|---|---:|---|
| **no ontology** | **0.3389** | [0.2785, 0.3838] |
| real FIBO | 0.2030 | [0.1218, 0.2877] |
| FIBO + synonyms | 0.2100 | [0.1332, 0.2818] |
| FIBO + hierarchy | 0.2425 | [0.1690, 0.3148] |

Resampling cases:

| Comparison | Difference | Interval | |
|---|---:|---|---|
| none − FIBO | +0.1359 | [+0.0566, +0.2084] | separated |
| none − FIBO+synonyms | +0.1289 | [+0.0686, +0.1928] | separated |
| none − FIBO+hierarchy | +0.0964 | [+0.0328, +0.1565] | separated |
| FIBO − FIBO+synonyms | −0.0070 | [−0.0442, +0.0443] | overlaps zero |
| FIBO − FIBO+hierarchy | −0.0395 | [−0.1410, +0.0595] | overlaps zero |

Giving the extractor no schema at all produces the highest agreement, reliably.
The three ontology conditions are not distinguishable from one another — the
synonym layer and the entailed hierarchy both fail to separate, and the second
of those was the outcome the pre-registration named as most likely.

The registered shape is rejected as well, and differently from how a rising line
would have been. Goldilocks predicts the middle peaks. The middle does not peak;
the baseline does.

## The treatment was delivered

An experiment that varies a schema has to show the schema reached the extractor.
Of the 70 classes the FIBO condition declares, 64 were used, and 0.9155 of its
nodes carry one. The hierarchy condition used 61 of 70 at 0.9050. The models
read the class list and followed it. The conditions are real.

## Agreement on names is not agreement on facts

The table above counts whether two models give a fact the same name. On the same
graphs, measured on content instead:

| Condition | Gold figures present | Figures two models both extracted |
|---|---:|---:|
| no ontology | 0.2525 | **0.1471** |
| hand-written | 0.2554 | 0.4348 |
| real FIBO | **0.2918** | **0.5200** |
| FIBO + synonyms | 0.2767 | 0.4691 |

The ordering inverts. The baseline wins on naming and loses on content: FIBO
captures more of the answer's figures and produces far higher agreement on the
values themselves.

This is reported with a caveat that has not been cleared. These content figures
come from the first sweep, where the baseline declared no `value` slot, so part
of its low value-agreement may be that it had fewer values to compare rather
than that its models disagreed. The property floor was equalised in the second
sweep and the naming result survived it unchanged; the content comparison has
not been re-run at the same footing.

## Two columns that must not be read

The declared-type share is 0.9906 for the baseline. Its one declared class is
`Entity`, so every node trivially carries a declared type. That is a definition,
not a finding.

The period fill rate was confounded in the first sweep and is not in the second.
With every condition declaring the slot, the baseline fills `period` on 0.3163
of nodes against 0.7158 for FIBO. That difference is now attributable to the
ontology rather than to which condition was handed the slot.

## What this does not establish

Sixteen cases, three models, one run. Correctness means a figure appears in the
gold answer, which counts a right number attached to the wrong entity as
correct, and scores facts rather than answers.
