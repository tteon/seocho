# ADR-0102: Content-vs-Context on BC3 Decision Emails + KV-cache Cost (measured)

Date: 2026-06-03

Status: Accepted (evidence record; informs design, does not lock architecture)

## Context

The Context-Graph study (`examples/contextgraph/`) tests whether the FinDER
finding (financial QA: vector ≥ graph) generalizes — or FLIPS — on
**decision-making email threads**, where relational/temporal structure
(proposal→response→decision, who objected, position changes) was hypothesized to
favor a graph (context) lane. Pre-registered hypotheses: H1 (FLIP — graph/hybrid
beats vector on relational slices), H2 (graph's stable per-thread prefix amortizes
cheaper than vector via KV-cache), H3 (decision-graph quality is recall-gated).

**Scope (critical, §20 — no overclaiming):** this ADR records a result on **BC3
ONLY** — a single decision dataset (135 cases × 5 lanes = 675 partials), single
judge (gpt-5.5). Of the decision datasets considered (BC3, Enron, W3C/Avocado,
AMI), **only BC3 was reshaped and run.** Enron is downloaded but not run. This is
therefore one decision data point (plus FinDER financial as a separate domain),
NOT a cross-dataset generalization.

## Decision / Findings

**H1 (FLIP) — REJECTED on BC3.** judge_score: hybrid@decision 0.465 ≈
hybrid@non-ontology 0.463 ≈ vector 0.454 ≫ graph@non-ontology 0.324 >
graph@decision 0.270. Paired vs vector (same case): graph loses significantly
(Δ −0.13 / −0.18; vector wins 79–92 of 135; p=0.0); hybrid only TIES vector
(Δ +0.01; p>0.46) — including on relational slices E2_DECISION_SUMMARY /
E3_PROPOSALS / E4_POSITIONS. Same shape as FinDER: **content(vector) ≥
context(graph); hybrid ties content; graph alone loses.**

**Ontology ablation — decision ontology did not help.** Graph got *worse* with the
decision ontology (0.324→0.270); hybrid unchanged. Disconfirms "ontology lifts the
graph lane" on this data.

**H2 (KV-cache cost) — mechanism CONFIRMED, recommendation unchanged.** OpenAI
gpt-4o (the truly-necessary OpenAI exception — `cached_tokens` telemetry exists
nowhere else available; ADR-cost-policy), n=28/lane: amortized_billable graph 812
< vector 918 < hybrid 2350; hit_ratio graph 59% / vector 0% / hybrid 17%. The
graph's raw context is 2.2× vector yet its **stable prefix caches (59%) → cheaper
amortized billable than vector**. The cost mechanism holds. BUT the cost win sits
on a quality-poor lane (graph 0.27), and the quality-competitive lane (hybrid)
is the most expensive (2350) — so **vector remains the best quality-per-token
operating point on BC3.** Latency NOT measured (ttft 0.0 — not captured). n=28
underpowered (directional).

**H3 (recall gate) — consistent.** The mechanism for graph's loss is
extraction-recall: the serialized subgraph drops content the raw messages hold;
hybrid recovers vector by including messages but the graph adds no significant
signal. Matches the FinDER generator/recall finding.

## Consequences

- **Design signal, not a lock:** graph-as-context is not competitive on BC3
  decision QA as currently extracted; the lever is extraction recall, not
  retrieval mode. SEOCHO must not overfit to FinDER *or* BC3.
- **KV-cache prefix stability is a real cost lever** (graph 59% vs vector 0%
  cache) and directly motivates middleware feature F1 (ontology+graph as a
  byte-stable cached prefix) — but only pays off where the graph/hybrid lane is
  quality-competitive, which requires recall improvement first.
- **Design consequence — F3 runtime gate (route_policy@v1, opt-in, OFF by
  default).** Because graph-as-context is ≤ vector, the runtime can skip the
  (large) graph-context fallback for non-relational queries. A/B (reusing the
  judged vector-vs-hybrid lanes as the gate's ON=vector vs OFF=vector+graph):
  BC3 quality Δ −0.010 (paired p=0.48, n.s.), AMI Δ −0.044 (p=0.35, n.s.) — no
  statistically significant quality loss; token savings ~1.6k (BC3) / ~2.6k
  (AMI) prompt tokens per gated query (~66–80% context reduction). Verdict: a
  sound cost lever, but kept OFF by default (AMI's small negative point estimate
  + benchmark-proxy caveat — gate fires only on empty-structured-record queries;
  live runtime A/B + larger N needed before any default flip). Landed behind
  SEOCHO_LANE_POLICY (commit 415b5f8).
- **Generalization (AMI, 2026-06-04):** ran AMI Meeting Corpus as a 2nd decision
  dataset (15 meetings, 59/lane, human abssumm gold; MARA MiniMax gen + local BGE
  + MARA DeepSeek judge — cheap budget, no OpenAI). Result: **PARTIAL
  generalization.** Both BC3+AMI agree on the core — graph never significantly
  beats vector and hybrid ties vector (**H1 FLIP rejected in both**). But BC3 had
  graph significantly WORSE (p=0.0) whereas on AMI graph merely TIES vector (all
  paired p>0.25). Mechanism: AMI graphs are far richer (400–500 nodes vs BC3
  50–89; long dense transcripts → higher extraction recall) → graph-as-context
  closes the gap. This refines the conclusion from "graph is uncompetitive" to
  **"graph competitiveness tracks extraction recall"** (consistent with the
  FinDER generator/recall finding). Confounds disclosed: judges differ (gpt-5.5
  vs MARA-DeepSeek), embedders differ (OpenAI vs BGE), modality differs; AMI
  underpowered (n=59) → within-dataset relationship only.
- **Ontology+prompt cycle (approach1 SHACL+SKOS), 2026-06-05 — PROXY INFLATION.**
  An ontology-engineer↔domain-expert swarm (anti-pattern-guarded) produced a
  SHACL+SKOS extraction prompt (typed sent_date, stance edges, grounded Decision-
  RESOLVES, naming) to fix the measured gaps. Round-1 STRUCTURE rose hugely on
  BC3 (15-thread): CQ coverage 30→60%, stance CQ 0→73%, grounded 0→0.91, no
  anti-pattern explosion. BUT round-2 QUALITY (gpt-oss judge, same threads,
  vector lane identical +0.000 = clean comparison): graph@decision only +0.018
  (below the pre-registered 5pp threshold), hybrid −0.022, and E4_POSITIONS (the
  slice whose stance CQ went 0→73%) got WORSE (0.038→0.000). **Filling structure
  inflated the existence-check proxies but did not improve answers.** Mechanism:
  the missing structure was not the binding constraint on answer quality — the
  serialization/use of the graph in the answer context is. Decision: the
  extraction-structure lever is exhausted (judge Δ<5pp); next levers are
  serialization (F4 bounded anchor-neighborhood) or reasoning-over-graph
  (approach2 ReAct), NOT more extraction. Honest asymmetry (graphs M2.5-built,
  answers M2.7, judge gpt-oss — MARA had M2.5/DeepSeek down) is fair across arms.
  Validates §20: a structural proxy can move opposite to LLM-judged quality —
  always confirm with the judge, never ship a CQ gain as a quality claim.
- **approach2 (ReAct reasoning) + improvement-cycle VERDICT, 2026-06-06.**
  approach2 = a ReAct loop (MiniMax-M2.7) querying the approach1 SHACL+SKOS graphs
  via typed graph-read tools, vs approach1's one-shot serialization. 4-way judge
  (gpt-oss, same 15 threads, graph lanes): **vector 0.229 ≫ approach1 0.164 >
  baseline 0.145 > approach2 ReAct 0.138.** ReAct did not beat one-shot (−0.025)
  and stayed far below vector. Per-slice: ReAct WON E1_FACT (0.333, targeted
  who/when query) but HURT synthesis slices E2/E3 (iterative querying loses the
  holistic narrative); E4_POSITIONS ~0 in all variants. **VERDICT: no lever —
  SHACL+SKOS extraction (approach1) nor ReAct reasoning (approach2) — made the
  graph competitive with vector on BC3 decision QA. content ≥ context HOLDS
  through ontology+prompt+reasoning optimization.** The decision-synthesis signal
  in the extracted graph is simply less than the raw text; the one durable lesson
  is to ROUTE single-fact/lookup queries to targeted graph queries (E1 win) and
  narrative/synthesis to content. The cycle stopped at the agreed gates (judge
  Δ<5pp, no improvement across rounds). Asymmetry (graphs M2.5, answers M2.7,
  judge gpt-oss — MARA M2.5/DeepSeek down) fair across arms; N=55, directional.
- **Graph-centric reframe + builds (2026-06-06, continuous expert panel).** The
  user re-centered: graph is the CORE; a standing panel (Harvard SWE + Meta-scale
  architect) reframed the verdict — "graph ≤ vector" was an artifact of scoring
  graph on prose-QA (vector's turf) through a lossy serializer. Built & measured
  ($0 unless noted): (#1) graph-strength eval — on the RIGHT metric (LLM-free
  deterministic serving) approach1 SHACL+SKOS vs baseline: multi-hop JOIN 0→73%,
  provenance 0→91%. (#2) Tier-1 deterministic LLM-free answerer (grounded, cites
  source_quote). (B) canonical entity merge: fragmentation 14→0% (Person 128→95),
  answerability unchanged (B's gain is accuracy, not answerability — needs C). (C)
  accuracy via same gpt-oss judge: on graph's served classes E3_PROPOSALS+
  E4_POSITIONS, **DET-graph(LLM-free) 0.080 = vector 0.080 (tie); E4 DET 0.077 >
  approach1 LLM-over-graph 0.000**; E1_FACT vector 0.367 > DET 0.233.
- **Program conclusion:** graph's demonstrated win is the **scale/serving axis**
  — LLM-free admission-control + verifiability/auditability + cacheable stable
  prefix — matching vector quality on served classes at $0 LLM, NOT a prose-answer
  quality advantage (parity, not better; absolute quality low on these hard
  slices). prose-QA was always the wrong metric for graph. A (LLM-verbalize)
  assessed LOW-ROI: it would spend scarce M2.7 chasing the vector-favoring
  prose-judge while the deterministic answer already equals vector.
- **Scale-axis eval (panel pivot, $0 pre-registered, commit bd19c38).** Measured
  the asserted scale win directly with coverage as a JOINT metric (LLM-free AND
  correct). MOSTLY DISCONFIRMING: graph admits LLM-free (E3 100%, E4 77%) but
  correct|admitted ≈0 → LLM-free-CORRECT 0-8% (pre-reg bar 0.40 → FAIL); admission
  multiplier 1.04x; degradation 4% vs vector 0%. ONE robust win: prefix-stability
  graph 98% vs vector 16% (cacheable stable prefix — the cost mechanism). The
  marginal "88% coverage" was proxy inflation; the joint metric exposed it.
- **DEFINITIVE CONCLUSION (BC3 decision QA):** the binding constraint is
  **extraction quality** — the graph's answers are mostly wrong/incomplete, so
  neither quality nor rigorously-measured scale (except cache prefix-stability)
  favors graph. LLM-free serving is only valuable when correct; it isn't here.
  Graph's one structural advantage independent of answer quality is the cacheable
  stable prefix. Next real lever, if pursued, is extraction recall/quality
  (generator-dominated), NOT retrieval mode or serving.
- **Experiment 0 — failure-mode decomposition + $0 grounding fix (2026-06-06,
  Harvard-professor lit panel + SWE/architect cross-review; commits 429bc34 →
  c16b73f).** To localize "extraction quality", a $0 no-LLM probe
  (`examples/contextgraph/failure_modes.py`) split each E1–E4 case (panel-
  corrected: deterministic modes only — recall-vs-comprehension is undecidable
  without a frozen gold-tuple set, §20.1/§20.8). **Finding:** on the join classes
  E3/E4 (graph's actual job) FORMAT-LOSS = 56–58% (gold tokens present in the
  graph's raw Chunk text but dropped by the `_graph_context` serializer) with
  NOT-RECOVERABLE only 4–6% → the **serializer, not extraction recall, is the
  binding constraint** there; SILENT-WRONG (admitted & wrong, served LLM-free) =
  100%/69%. E1_FACT is the exception (NOT-RECOVERABLE 75% = genuine upstream,
  sent_date null). Root cause beyond the serializer: Proposal-node fragmentation
  (`informal_sig_at_chi` + title variants = anti-pattern #4; arm B merged Person
  only). A **$0 fix** to the deterministic answerer (ground on full source_quote +
  dedup proposals by token-prefix + per-claim abstain → ok=True only if ≥1
  grounded claim) plus extending the $0 entity-merge to Proposal nodes
  (136→127). **gpt-oss re-judge (v2 vs before), the confirmation:** E3_PROPOSALS
  judge_score 0.083→0.167 (2×; c/p/i 0/2/10 → 0/4/8, incorrect→partial) but
  **corr|admit stays 0.00**; E4_POSITIONS unchanged (0.077, corr|admit 0.10);
  E1_FACT unchanged (0.233). **VERDICT: the $0 serializer/grounding/dedup levers
  are EXHAUSTED — they recover SUBSTANCE (partial credit, gold-token recall E3
  62%) but NOT correctness (corr|admit ~0 on join classes; the proxy moved, the
  terminal metric did not — the §20 gap, caught by the JOINT metric).** The
  remaining binding constraint is genuine **upstream extraction quality** (E4
  stance edges don't carry the gold position substance; E1 sent_date not
  extracted), which requires the paid arms (two-pass / gold-tuple set / stronger
  extractor) the panel gated behind the now-spent free arms.
- **Still open:** the paid upstream-extraction arms (two-pass routed to E4/E1-hard
  chunks; κ-checked gold-tuple set for E1/E4 to make recall-vs-comprehension
  decidable; difficulty-routed stronger extractor); Enron (raw, no gold — needs
  annotated subset).
- Provider cost: future judges default to MARA; OpenAI used here only because
  `cached_tokens` telemetry is OpenAI/DeepSeek-only and no direct-DeepSeek key
  exists.

## Reproducibility

Run `e1-bc3-full` (675 partials), judge `e1-bc3-full_judged.json` (gpt-5.5,
decision rubric), cost `h2_cost.json` (gpt-4o, threads=8, arm=decision). Judge is
crash-hardened (incremental sidecar + resume + guarded post-processing).
