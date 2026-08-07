# Three ontology defects the interaction experiment found, and what each cost

Every one of these was found by running agents against the graph and reading what they wrote,
not by inspecting the schema. That is the point worth keeping: a schema looks complete right up
until something has to generate a query from it.

Each entry states the defect, the evidence, the fix, and the measured difference. The runs are
`outputs/finbench/agent_interaction.json`; the superseded runs that produced the "before"
numbers are kept beside it.

---

## 1. Properties named in prose are not declared

**Defect.** `TRANSFER`'s description said "Properties: amount, ts, channel (channel code),
channel_risk (1-5), cross_border (boolean)". There was no `properties:` block.
`policy_from_ontology` builds `allowed_properties` from the block alone, so as far as the
guardrail was concerned `amount` did not exist.

**Evidence.** The guardrail rejected `sum(t.amount)` as `unknown_properties:amount`. The model
did not treat this as a schema question — it treated it as an instruction, and repaired by
writing:

```cypher
RETURN count(*) AS transfer_count, null AS total_amount
```

and answered `{"transfer_count": 800, "total_amount": null}`. A guardrail blocking the correct
query, and the answer coming back with a hole in it rather than an error. Nothing in the
episode looked like a failure.

**Fix.** Declare them. `TRANSFER`, `USES_CHANNEL`, `DEPOSIT` and `REPAY` now carry
`properties:` blocks.

**Cost of the defect.** Every question involving an amount was unanswerable on the two arms
that ran the guardrail. `ext_easy_1` and `ext_easy_2` scored 0.5 on both.

**Generalisation.** Prose the validator cannot read is not a declaration. If a field is only in
a description, assume it is absent.

---

## 2. `Any` is read as "anything is permitted", not "not yet specified"

**Defect.** `GUARANTEE` was declared `source: Any, target: Any`. `OWN`, `APPLY` and `INVEST`
were declared `source: Any`.

**Evidence.** Measured on the SF100 graph: `GUARANTEE` runs Person/Company → Person/Company
40,001 times and touches an `Account` **zero** times.

```
GUARANTEE endpoints: Person→Person 33,068 · Person→Company 3,332 ·
                     Company→Person 3,270 · Company→Company 331
Account -[:GUARANTEE]- Account: 0
```

With `Any` in the schema, every one of the four agent designs wrote
`(a:Account)-[:GUARANTEE]-(b:Account)` on the three-layer question — putting a party-level
relationship between two accounts. The query is valid Cypher, runs, and returns nothing. The
ontology arm burned its whole eight-turn budget rewriting variations of the same wrong shape.

**Fix.** Declare the real endpoints: `Person|Company` on both ends of `GUARANTEE`, and on the
source of `OWN`, `APPLY` and `INVEST`.

**Cost of the defect,** on `int_hard_1b` (the three-layer conjunction, unambiguous wording):

| | before (`Any`) | after (declared) |
|---|---|---|
| ontology arm, SF100 | 8 round trips, 53,898,981 db hits, wrong | 1 round trip, 145,485 db hits, right |
| ontology arm, pooled over three scales | 0/9 | **9/9** |

**Generalisation.** An under-specified endpoint costs as much as a missing one and costs it
silently, because the resulting query is syntactically fine and returns an empty result that
reads as "no such pattern exists".

---

## 3. Declaring roles makes a model commit to direction — including where the question never said which

This one is not a defect in the ontology. It is the ontology working, and it is worth writing
down because it cuts both ways with the same mechanism.

**What roles do.** `TRANSFER` declares `sourceRole: sender` / `targetRole: beneficiary`;
`GUARANTEE` declares `guarantor` / `guaranteed`. Endpoint types alone cannot distinguish "sent
to" from "received from" when both ends carry the same label.

**Where it helps.** `ext_med_1` — "which accounts sent money to account N on a transfer whose
channel_risk is 5 or more":

| arm | db hits (SF100) | correct (pooled over 3 scales) |
|---|---:|---:|
| labels only | 1,158,923 | **1/9** |
| + ontology | 53,293 | **9/9** |

21.7× cheaper and right instead of wrong. The labels arm reached the channel through
`Account-[:USES_CHANNEL]->Channel.risk_weight`, a different question with a different answer.

**Where it hurts.** `int_hard_1` asks for accounts "whose owners **guarantee one another**".
With `guarantor`/`guaranteed` declared, all three ontology-informed arms read "one another" as
mutual and wrote `(o1)-[:GUARANTEE]->(o2)` **and** `(o2)-[:GUARANTEE]->(o1)`. The graph holds
40,001 GUARANTEE edges and **not one reciprocal pair**, so the answer is empty by construction.
The labels arm, having no direction to commit to, wrote the undirected form and matched.

| question | wording | labels | + ontology |
|---|---|---:|---:|
| `int_hard_1` | "guarantee one another" | 6/9 | **0/9** |
| `int_hard_1b` | "one of them guarantees the other in either direction" | 6/9 | **9/9** |

Same graph, same schema, same model, same three scales. The only difference is that the second
wording says which direction it means.

**Generalisation.** A schema that makes direction legible does not remove ambiguity from a
question — it exposes it. The vaguer arm scored better here by being unable to be precise, which
is not a property anyone should want. Both questions are kept in the set for exactly that
reason: the pair is the evidence, and one without the other would support the wrong conclusion.

---

## 4. The planner's row estimate is not a cost signal

Not an ontology defect, but found the same way and worth recording next to them, because the
first version of the plan-feedback arm was built on it.

**What was tried.** Run `EXPLAIN` before executing and refuse any query whose estimated row
count exceeds a budget. `EXPLAIN` is free, so the refusal costs nothing.

**What happened.** The gate fired **zero times in 108 episodes**, including on a query that did
37,905,378 db hits.

**Why.** Measured across the 48 queries the arms settled on at SF100, actual db hits ran from
**2.9× to 4,617,254×** the summed `EstimatedRows`. `EstimatedRows` is per-operator output
cardinality: an anchored aggregate estimates one row while doing 23 million db hits, because
the work is in the expansion and the estimate describes what comes out of it. Any budget drawn
on that number either passes everything or blocks everything.

**Fix.** Gate on measured elapsed time. The tool runs the query under a 2-second probe first; a
query that does not finish comes back to the model with its plan operators and an instruction
to start from an indexed lookup, plus an `/* accept-cost */` override for when the cost is
genuinely necessary. The override is not a convenience — without it the gate becomes a policy
that investigators may not run expensive queries, which is wrong for the internal audience by
construction.

**Result.** The gate fires 15 times at SF100 and cuts median db hits per question from
5,381,224 to **1,789,272**, at 106/117 correct against the unguarded 108/117 — it costs two
answers to save two thirds of the work.
