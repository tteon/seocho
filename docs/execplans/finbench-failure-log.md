# FinBench experiment — failure log

Every wrong turn taken while building the graph-agent scalability experiment, with what
it cost and what exposed it. Kept because the pattern across them is more useful than
any single result:

> **Almost every mistake here produced a plausible number or a green test.** Not one was
> caught by the thing we were nominally measuring. Accuracy never revealed a wiring bug;
> latency never revealed a plan regression; a passing suite never revealed a
> non-sargable anchor.

Grouped by kind. "Cost" is time lost or, worse, a conclusion that would have been drawn.

---

## 1 · Measurements that were wrong (and looked fine)

### 1.1 Generation arm reported 0% (9/9 rejected)

**Read as:** "validated generation cannot handle these questions."
**Actually:** two configuration errors in my harness.

- `Text2CypherFallbackPolicy` defaults `workspace_property="workspace_id"`, but SEOCHO
  writes `_workspace_id`. Every generated query failed the tenant-scope check.
- The prompt never stated the scoping convention, so the model invented a `Workspace`
  node with a `HAS_ACCOUNT` edge to satisfy it — and the guardrail correctly refused
  that. The refusal was real; it measured the prompt, not the model.

**Exposed by:** the rejection strings (`missing_workspace_scope_expression`,
`unknown_labels:Workspace`). Had I logged only pass/fail, the arm would have been
written off.
**After fixing:** 78% (7/9).

### 1.2 `generated_first` reported 67%

**Read as:** "routing to generation barely helps."
**Actually:** the flag had no effect at all. `ask()` constructed
`DeterministicQueryPlanner` directly and passed it down, so `_build_planner` — and the
precedence flag with it — was never consulted. 67% was the template arm under a
different name.

**Exposed by:** `s1_intent` — every recorded value was a template intent
(`count`, `neighbors`, `path`) and none was `generated`. Corroborating tells:
`s2_slot_fill=94%` and `guardrail_repairs=0%`, neither of which the generation path can
produce.
**After fixing:** 89% (8/9).

### 1.3 `by_route` reported 78%

**Read as:** "folding the policy book in was pointless."
**Actually:** `_build_planner` tested `!= "generated_first"`, so the new `by_route` mode
fell through to the deterministic planner. Same class of bug as 1.2, one layer down,
**two hours after fixing 1.2**.

**Exposed by:** the recorded route — `engine route='template'` with `rejection=None`,
which distinguishes "generation was refused" from "the hybrid planner was never built".
**After fixing:** 100% (9/9).

### 1.4 "Sub-linear scaling" from SF1→SF10

**Read as:** graph latency grows 1.7x per 10x of data — bounded queries hold up.
**Actually:** small-scale fixed cost masking a linear query. SF100→SF1000 grew 9.2x per
10x — essentially linear. The probe was unlabeled (`MATCH (a)-[...]`), so no index could
serve it.

**Cost:** a claim recorded in the ExecPlan that had to be retracted.
**Exposed by:** running the same probe at SF1000 instead of extrapolating.

### 1.5 100% accuracy on the original scenario set

**Read as:** the middleware handles AML questions.
**Actually:** the nine questions were single-fact lookups. Rewriting them the way an
analyst asks — window, threshold, several conditions, multiple return values — gave
**25% (2/8)** on the same graph and model.

**Exposed by:** writing scenarios against published typologies (FinCEN structuring,
FATF funnel accounts) rather than against what the system could already do.

---

## 2 · Fixes that made things worse

### 2.1 Repairing the templates suppressed the repair loop

Making `_count` / `_list_all` / `_neighbors` return rows switched off the loop that had
been rescuing those questions. The trigger was `if not records:` — "no rows" read as
"nothing to fix" — and a template returning one plausible row looked like success.

```
direct_transfer @ SF1000     before template fixes    after
reasoning_attempts                              2         0
support                                 supported   partial
rows                                            2         1
correct                                      True     False
```

SF1000 accuracy fell **89% → 56%** *while plan quality improved* (sargable 75% → 100%,
db hits 33,000,855 → 456). A component-local improvement regressed the system because
another component's trigger depended on the first one failing.

**Fix:** `_should_repair` also treats a single row on a set-valued intent as
inconclusive.
**Lesson:** an isolated component metric can move the right way while the system moves
the wrong way.

### 2.2 The sargable fix was applied to one template

`seocho-1dp` fixed the anchor in `_count` and left the sibling templates alone. At
SF1000 the repaired count did 105 db hits while `list_all` did **86,469,790** — right
answer, ruinous plan. Correctness testing cannot see that difference at any scale.

**Fix:** all anchored templates route through one `_anchor_predicate` decision.

### 2.3 Forcing path direction would have produced wrong answers

Adding schema-reachability pruning, I first pruned on *directed* reachability. That
would have returned "no path" for legitimate undirected questions ("how is account X
connected to company Y", where `OWN` is declared Company→Account).

**Caught before shipping** by testing a case I expected to pass. Pruning is now
conservative — it walks edges both ways and refuses only when no chain could satisfy the
pattern in either direction.

---

## 3 · Bugs I introduced

### 3.1 Parameter collision across two endpoints

`_sargable_anchor` used a fixed `anchor_key_N` prefix. A path template anchors two
nodes, so the second endpoint was compared against the first one's value:

```cypher
WHERE (a.acct_no = $anchor_key_0 OR a.id = $anchor_key_1)
  AND (b.id = $anchor_key_0)          -- Company.id vs an account number
```

Silently returned zero rows. **Exposed by** rows=0 on a query whose plan looked fine.
Fixed by namespacing parameters per alias.

### 3.2 My own ground-truth prober used the bad plan shape

`verify_scenarios.py` — the script establishing ground truth — was written with the
unlabeled `MATCH (a)-[...] WHERE a.id = $id` shape. It was the AllNodesScan the
experiment went on to be about.

**Consequence:** the first scale curve measured the naive plan and called it the graph
layer's cost.

### 3.3 My own `seocho-k2v` fix was non-sargable

The relationship-aware count I added anchored with `CONTAINS coalesce(name, uri, id)`,
following the existing convention. It answered 25 correctly at SF1 and **0** at SF1000
while doing 16.5M db hits — the scan both explodes in cost and stops resolving the
anchor.

This is the experiment's thesis applied to its own code: **it passed every small-scale
test and was unshippable.**

### 3.4 The funnel typology ran backwards in time

I hardcoded the onward transfer at `T0 + 6*DAY + 4*HOUR` while the smurf schedule spans
6.2 days, so the "onward wire after the last deposit" landed **2 hours before it**.

**Exposed by** asserting the gap was positive. Now derived from the deposit timeline.

### 3.5 Breaking a shape-only test

Propagating the sargable anchor broke `test_supersession_query_contract`, which asserts
template shape on `CypherBuilder.__new__` — an instance with no ontology attached.
`_sargable_anchor` raised `AttributeError` instead of declining. Both identity helpers
now read the ontology through `getattr` and fall back to text matching.

### 3.6 Overclaiming "observability promoted"

I reported the observability work as done when only the **span** layer existed. No
Prometheus metrics, no Grafana, and `docker-compose.observability.yml` did not exist.
Spans answer "why was this request slow"; they cannot answer "is the sargable rate
drifting". Corrected by adding the metric layer and the stack.

---

## 4 · Methodology errors

### 4.1 No warm-up, and it *understated* the result

Measuring without `apoc.warmup.run` and with single-shot timings put the tuned/naive
latency gap at 67x. With warm-up and ten repetitions it is **411x**.

```
SF1000        cold      warm p50    ratio
tuned      32.8 ms       1.8 ms     18.6x
naive     841.8 ms     814.3 ms      1.0x
```

**Warm-up helps the good plan and does nothing for the bad one** — an index seek is
I/O-bound until its pages are cached; a full scan does the same work either way. So
skipping warm-up systematically understates the advantage of the correct plan.

### 4.2 Page cache two orders of magnitude undersized

Left at the 512M default against a 2.45 GiB store. Every absolute latency taken before
`docker-compose.finbench.yml` is unreliable. Db hits were unaffected — which is the
argument for reporting them.

### 4.3 Single-shot measurements, no p99

Vendor and LDBC guidance both say repeat and report the tail. The first numbers did
neither.

### 4.4 Ranking models by latency on an unstable provider

DeepSeek-V3.1's p50 moved **33,763 ms → 2,172 ms** between runs with no code change.
An early ExecPlan entry presented a "6–10x latency spread" as a Track 7 result; that had
to be retracted. Accuracy and slot-fill are comparable across models on this harness;
latency is not.

### 4.5 Reading `rate()` as zero

Panels showed 0 after a single run while the raw counter at `:8889/metrics` was clearly
non-zero — a counter needs two scrapes to have a slope. Nearly filed as a broken
dashboard.

### 4.6 A poll timeout reported as an infrastructure result

The memory sweep restarts the container per profile and polls `SHOW DATABASES` for up to
180 s. The `unlimited/8G/6G` baseline came back `database_not_online` — recorded in the
report next to a genuine startup failure at `mem=2g`, in the same column, looking like the
same kind of finding. It was WAL replay after a fresh recreate exceeding the poll budget;
re-measured against the identical config once online, it produced normal numbers.

This is the most dangerous shape in the whole log because it is *directionally plausible*:
"large config also failed to start" invites an infrastructure story, and the harness
provides no way to tell a timeout from a refusal. Two lessons. A sweep must distinguish
"never came online" from "not online yet" — different states, different conclusions. And a
row that contradicts the trend deserves a re-measurement before it becomes a finding; here
the contradiction was the tell, since 8G/6G failing while 2G/1G worked makes no mechanical
sense.

---

## 5 · Data and ontology modelling errors

### 5.0 Generating a financial benchmark with no hubs, and calling it FinBench-style

The generator drew both transfer endpoints as `floor(random()*n_accounts)`. That is
uniform attachment, so degree is binomial: at SF1000 it measured mean 10.00, p99 18,
**max 31** — max/mean of 3.1.

LDBC FinBench's stated difference from the social-network benchmark is hub vertices whose
"degree ... may scale up to millions in large data scales", which "poses new challenges to
the performance of systems". The dataset was named after a benchmark and then built
without the property that benchmark exists to stress.

The cost was not academic. Two headline conclusions — "an indexed plan holds constant db
hits at every scale" and "page cache and container memory produce no cliff" — both rest on
a mechanism ("the working set is 25 pages and stays hot") that a hub directly breaks. The
findings are not wrong, but they were stated at a generality the data could not support,
and one axis was missing from the ranked table entirely.

What made it invisible: every scale-factor knob was exercised (SF1 → SF1000, 1000x data)
while the *shape* of the data stayed fixed. Scaling volume feels like scaling the problem.
It is not, when the distribution is the thing that breaks.

The lesson that generalises: when synthesising data to test a system, the distribution is a
parameter and has to be swept like any other. A generator that only takes a size argument
can only ever answer "what happens with more of the same".

### 5.0.0 The same omission, three times: shape was never a parameter

§5.0 records generating a financial benchmark with no hubs. The same mistake was present
twice more and went unnoticed until asked about directly:

| property | measured at SF1000 | what it should be |
|---|---|---|
| max degree | 31 | hubs to millions (FinBench) |
| max edge multiplicity | **2** (14 duplicate pairs in 10M edges) | routine repetition |
| avg local clustering | **0.000** (0 of 2,997 sampled nodes in a triangle) | 0.1–0.5 |

One root cause: the generator took `--sf` and nothing else. Scale was a parameter, shape
was not. So SF1 → SF1000 exercised a thousand-fold change in volume while the structure the
queries traversed stayed identical, which is exactly why volume looked like the axis under
test when it was the only axis that had been wired up.

Each absence disables a specific measurement, which is worse than reducing realism:

- **No multiplicity → semantic errors are unscoreable.** At redundancy 1.0000x,
  `count(dst)` equals `count(DISTINCT dst)`, so an agent that answers "how many transfers"
  to "how many counterparties" scores correct. Still false on the power-law graph too
  (1.0031x).
- **No triadic closure → motif detection is untested.** The `laundering_cycle` scenario is
  anchored on a known account, so it measures recall and gets precision free; there are 47
  incidental 3-cycles in the whole graph to compete with. The unanchored question an
  analyst actually asks was never posed and could not be.
- **No hubs → the memory conclusions were mechanism-bound** (§5.0).

The generalisable lesson, stronger than "sweep the distribution": *ask what a measurement
would be unable to detect on this data*. All three gaps are invisible when reading
accuracy, latency, or db hits — they show up only by asking what the numbers could not
have told you.

Fixed for degree (`--hub-skew`) and multiplicity (`--dup-share`); clustering needs a
generation model that closes triangles, so it is measured and published but not yet
generated, and `graph_properties.py` now merges a `structural_profile` into every manifest
so the next omission is at least visible.

### 5.0.1 And then the prediction about hubs was also wrong

Having found the omission, I predicted the indexed anchor would stop helping on a hub,
since expansion costs O(degree). Measured: the guardrail's shape is flat from degree 6 to
degree 158,315 (163-267 db hits, 2.7-3.4 ms, index seek intact). Cypher is lazy, so
`DISTINCT ... LIMIT 50` stops at 50 rows without walking the hub.

The real failure sits one step away — questions that *cannot* early-terminate (counts,
sums, ranked top-K) time out on the same anchor. So the diagnosis "hubs are expensive" was
too coarse to be useful; the operative variable is whether the question admits a `LIMIT`.
Worth recording because the wrong prediction and the right one differ only in which part
of the query you look at, and the wrong one would have sent the fix to the wrong layer.

### 5.0.2 Curating anchors by degree, when degree does not predict cost

The generator publishes one anchor per degree band so measurements can state which band
they ran in. The measurement then showed the scheme does not work: median degree 6 cost
158,487 db hits while p99 degree 73 cost 3,876 — non-monotonic, because preferential
attachment makes a low-degree node's neighbours disproportionately likely to be hubs. Cost
follows the neighbourhood, not the anchor.

LDBC solved this before I ran into it: parameter curation selects bindings by intermediate
result size at each level of the plan (Gubichev & Boncz, TPCTC 2014). Building a curation
scheme keyed on a local property, without checking it correlates with cost, is the same
class of error as trusting db hits without checking they track latency.


### 5.1 The ontology under-described the data, and pruning trusted it

`OWN` was declared `source: Person` while the loader emits **both** Person→Account
(1801) and Company→Account (199). Schema-reachability pruning therefore reported a
satisfiable path as impossible.

**Lesson worth generalising:** schema-based pruning is only sound if the schema covers
the data. The SIGMOD 2025 formalisation assumes the schema is authoritative; in practice
that conformance has to be verified, not assumed. Mitigated two ways: `OWN` now declares
`source: Any`, and the reachability test is deliberately conservative.

### 5.2 Planted patterns had no time

Every planted edge carried `ts = 1700000000`. AML detection is temporal — FinCEN defines
structuring as transactions "on one or more days for the purpose of evading" the
threshold — so no realistic typology was expressible. The whole first scenario set was
shaped by what the data could support rather than by what the domain asks.

### 5.3 `CAST(x AS BIGINT)` rounds

DuckDB rounds rather than truncates, so `random() * N` occasionally yielded `N` and
produced endpoint ids outside the node range (7 dangling edges). Ids need `floor()`.

### 5.4 `DISTINCT` + window function is not order-stable

`uses_channel` used `SELECT DISTINCT ... count(*) OVER (...)`, whose row order varied
between identical runs and changed the Parquet checksum — a real determinism break in a
generator whose value depends on being reproducible. `GROUP BY` + `ORDER BY` fixed it.

---

### 5.4 Three wrong golds in one session, each read as a model failure

Measuring hub-anchored cases produced, in order: 0/12, then 1/12, then 5/12 — and two of
those three jumps were fixes to *my gold*, not to the system.

| what was wrong | how it scored | what it actually was |
|---|---|---|
| case file emitted `expected_answer`; harnesses read `gold` | 0/12, both arms | gold was empty, every answer compared against nothing |
| list gold ordered by numeric id; engine orders by a string display expression (`"1027815" < "46"`) | 3 list cases wrong | ordering mismatch, answers were valid |
| gold computed *exactly* 2 hops and included the anchor; question asked "within 2 hops" | 4 two-hop cases wrong | the agent answered the question as asked — 3 of 4 matched the correct reading exactly, the 4th differed by one (the anchor) |

The pattern is the dangerous part: **a wrong gold and a wrong answer are indistinguishable
in the score, and the score is presented as the model's.** Each of these produced a
believable accuracy figure and an available explanation ("hubs break retrieval"), and only
recomputing the expected value a second way exposed them. The two-hop case is the sharpest:
the answers were *nearly* right (94,857 against 94,791), which reads as an almost-correct
model rather than an off-by-definition gold.

What actually caught them: comparing the returned value against an independently computed
alternative interpretation, rather than against the gold. The habit worth keeping is that
before attributing a near-miss to the model, compute what the answer *would* be under the
other plausible reading of the question.

A fourth, related: the direction fix itself first scored 12/12 by substring-matching a
phrase list its author had written against the questions being scored. Held-out paraphrases
scored 0 of 6. Overfitting a measurement to its own instrument looks like success and reads
like a result.

---

### 5.5 Transitive closure is not a cycle, and the clustering number hid that

Adding triadic closure to the generator, I completed `a->b->c` with `a->c` and measured
average local clustering rising from 0.000 to 0.175 with 77% of sampled nodes in a
triangle. That looked like the fix.

Directed 3-cycles went 47 to 110 — i.e. nowhere. A transitive triangle has no cycle in it;
a cycle needs `c->a`. The whole reason for adding closure was that the laundering-ring
scenario had no distractors, and after the "fix" it still had none, behind a clustering
figure that read as healthy. Adding `--cycle-share` took incidental 3-rings to 471,151.

**The trap is a metric that moves in the right direction while the thing it was standing in
for does not.** Clustering was a proxy for "does this graph contain the motifs my scenario
searches for", and the proxy improved while the motif count did not. Same class as
§2.1, where sargability improved while system accuracy fell — a proxy is only worth
tracking alongside the thing it proxies for.

### 5.6 A tool that adds one fact deleted the documentation around it

`annotate_ontology_degrees.py` wrote the measured degree hint into the ontology with a
`yaml.safe_dump` round-trip. That is the obvious implementation and it silently discarded
every comment in the file — including the paragraph explaining *why* `sourceRole` exists,
written one commit earlier precisely so the next reader would not have to rediscover it.

Rewritten as a targeted splice of the one block, with a re-parse check afterwards.
Round-tripping a hand-maintained config through a serializer is lossy in a way that leaves
no error and no diff worth noticing.

---

### 5.7 Realistic phrasing broke four things the synthetic phrasing had been hiding

Rewriting the grid questions from probe wording ("how many distinct accounts are reachable
within 2 transfer hops from the account with account number 18503") into what an analyst
actually asks ("we are considering freezing account 18503 — within two transfer hops
downstream, how many other accounts would be touched") broke the pipeline four separate
ways. All four were live before the rewrite; the synthetic phrasing had simply been avoiding
every trigger.

| # | trigger in the realistic wording | what broke | measured |
|---|---|---|---|
| 1 | "payment mule", "sent funds to" | intent classified as `financial_metric_lookup`, which hardcodes `Company`/`FinancialMetric` — labels absent from this ontology — so the plan fell to an unlabeled scan | db hits/answer 38,584 → **6,600,197** |
| 2 | sentence opening with "Account 42" | the anchor arrived as `'Account 42'`; `_sargable_anchor` treats a multi-word mention as prose and declined, dropping to a text scan | 130 → **4,000,138** |
| 3 | "two transfer hops" | `_MULTI_HOP_RE` matched only `\d+\s*hops?`, so no multi-hop route fired and every two-hop question was answered with the anchor's out-degree | gold 69,303 → answered **68** |
| 4 | the anchor never reaching generation as a parameter | generated Cypher dropped the anchor entirely and expanded two hops from *every* account | **38,867,373** db hits for a question about one account |

**Defect 3 is the most dangerous in this entire log, because the wrong answer was cheaper.**
Db hits per answer fell from 38,584 to 154 — a 250x "improvement" — and every cost metric
read it as a win. Only accuracy caught it. Everywhere else in this project the failure ran the
other way: db hits told the truth while accuracy stayed green. Neither metric can be trusted
alone, and I nearly reported the 250x as a result.

**Defect 4 had three layers, each revealed only by fixing the one above it.** Passing
`$anchor_value` stopped the anchor being dropped, but the extractor then bound a *reporting
threshold* ("below 10000000") as the anchor — worse than no anchor, since a wrong anchor
answers a different question confidently. Restricting extraction to numbers introduced by the
node's own vocabulary fixed that, and then the model bound the parameter to `id` — a string
like `"Account:44957"` — so an integer compared against it matched nothing: one index seek,
zero rows, and an answer of "0" that looks like a finding and costs `db_hits=1`. The prompt
had to state which property the parameter keys, the same way it already states tenant scope
and arrow direction.

Two lessons worth separating.

**A benchmark phrased in nobody's language cannot measure what it claims.** Three of these
four defects are latent in production wording and invisible to synthetic wording, and all four
are invisible at SF1 — a scan still resolves the anchor there and costs 12,283 db hits, so the
answer is right and cheap. They surface between SF10 and SF100. No amount of small-scale
testing finds them.

**Fix the system where the system is wrong, and the spec where the spec is wrong.** The
off-by-one on the two-hop count (gold 69,303, answered 69,304 — the anchor itself) was *my*
ambiguity: "how many accounts would be touched" never says whether the account counts itself,
and neither reading is wrong. That one was fixed in the question ("how many *other* accounts…
exclude account N itself"), not in the code. Fixing it in the code would have been tuning the
instrument to the system, which is the same error as the phrase-list overfit in §5.5.

---

## 6 · Environment traps

| trap | symptom | fix |
|---|---|---|
| `apoc-extended-init` sidecar exits 23 | `docker compose up neo4j` fails on a dependency | `--no-deps`; core APOC still auto-installs |
| `docker exec` bulk import | store owned by root; server (uid 7474) cannot read it. Surfaces only as "Unable to start" plus `AccessDeniedException` in debug.log | `chown -R neo4j:neo4j` after import |
| `opentelemetry-sdk` absent | OTLP backend disables itself and tracing continues with **no backend**; `current_backend_names()` returns `[]` | install `opentelemetry-sdk` + exporters |
| Grafana port 3000 taken | bind failure; a Compose override *appends* the port so the conflict persists | `ports: !override` |
| Tempo 503 for ~10 s | search fails right after start | wait for `/ready` |
| `pkill -f "ablation_ontology"` | killed my own shell — the pattern matched the invoking command line | bracket trick: `ablation_ontolog[y]` |
| `session.run(q, timeout=N)` — **made this mistake twice**, the second time three sessions after documenting it | see below; the second occurrence ran an unbounded ring count for minutes | `begin_transaction(timeout=...)`; documenting a trap does not stop you walking into it |
| `pkill -f <db-name>` | killed my own command, because the database name appears in the new command line too — the bracket trick cannot help when the pattern is legitimately present in the replacement | put the query in a script file and match on the script name |
| `.env` credential vs running container | `.env` password (10 chars) no longer authenticates; the container's own `NEO4J_AUTH` (13 chars) does. `auth.ini` mtime was misleading — Neo4j 5 keeps users in the *system database*, so the file is only the bootstrap. A daily scheduled job had been failing the same way for three days | compare against the container's env before assuming the store is corrupt |
| `session.run(q, timeout=30)` | looks like a transaction timeout, is not — `Session.run` forwards every keyword into *query parameters*, so the query ran unbounded and the ">60s" reported came from the server's `db.transaction.timeout`, not the flag | `session.begin_transaction(timeout=...)` |
| `ORDER BY ... LIMIT k` as a traversal bound | 70,000x *slower* than no bound on a hub — the sort materialises the whole edge list, destroying the lazy early-exit that was the actual protection | needs engine-side ordered top-K (relationship property index), not a query rewrite |
| Page cache constrained, host cache not | shrinking `pagecache` to 4.5% of the store changed nothing — a Neo4j-level miss was still served from the *host's* page cache | cgroup `mem_limit` to cap the OS cache too (and shrink `heap` with it, or the JVM will not start) |
| `--warm` on `verify_scenarios.py` | `unrecognized arguments` — warm-up is the default; the flag is `--no-warmup` | read the parser, not the memory of it |

---

## 7 · What the record adds up to

Thirty-one distinct mistakes. The three that mattered most (§1.1–1.3) each produced a
*believable* figure supporting a *false* conclusion: "generation cannot do this",
"routing barely helps", "the policy integration is pointless". Each was caught by an
instrumentation field — a rejection string, `s1_intent`, the recorded plan route — and
none by the accuracy number they were expressed in.

Three more (§2.1, §3.2, §3.3) are the experiment's own thesis landing on the
experiment: a change that improved a component metric while degrading the system; a
ground-truth prober written with the pathological plan; and a fix of mine that passed
every small-scale test and was wrong at scale.

That is the argument for the instrumentation, made at our own expense rather than
asserted.
