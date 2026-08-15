# ADR-0165: ablation A4 (resources) + A5 (execution honesty), OFF vs ON

Date: 2026-08-15 · Status: accepted (measurement record) · seocho-4rb, seocho-2ay

Two more Level-2 rows of the OS ablation study (wiki/os-ablation-study-design.md),
each an OFF/ON on the real shipped mechanism.

## A4 — resources: token-budget containment

The resource subsystem's axis: does a runaway agent chain stop, and how far past
the cap? Same multi-turn chain through the real `TokenBudgetTracker` (wired via
RunHooks, ADR-0153) with the budget OFF (0 = unlimited, a bare agent) vs ON (a
per-session cap). `scripts/agentos/ablation_a4_budget.py`.

Chain: 40 turns × 800 tok (would spend 32,000 unchecked); cap = 10,000.

| arm | halted | turns run | tokens spent | overshoot |
|---|---|---|---|---|
| OFF (no budget) | no | 40 | **32,000** | — (unbounded) |
| ON (cap 10,000) | yes | 13 | **10,400** | **400** (< one turn = 800) |

Budget OFF lets the chain spend everything (3.2× the cap here; unbounded in a
real loop). ON halts at a turn boundary and is bounded by cap + at most one
turn's spend — a structured stop, not a clipped answer.

## A5 — execution honesty: truncation disclosure

The execution subsystem's honesty axis: when a result is capped, does the tool
SIGNAL it, so a partial result cannot be presented as complete? Bare raw tool
(OFF — rows, no truncation metadata) vs the OS's governed `execute_query` (ON —
caps at `row_cap`, always ships `truncated: true/false`, the #478 lesson).
Metric = disclosure rate over over-cap results. `scripts/agentos/ablation_a5_honesty.py`,
using the real `SeochoOS.execute_query`; cap = 50.

| result size | over cap | ON rows | ON discloses | OFF discloses |
|---|---|---|---|---|
| 10 / 40 | no | 10 / 40 | yes (truncated:false) | no |
| 80 / 200 / 500 | yes | 50 | **yes (truncated:true)** | **no** |

Over-cap disclosure rate: **ON = 1.0, OFF = 0.0**. The governed path always
carries the signal; the bare tool never does, so a capped result looks complete
to the caller. (The stronger "does the agent actually say so" is Level-1's judge.)

## Consequences

- A4 and A5 join A2 (ADR-0164) as measured Level-2 rows; A1 (ADR-0160/0161/0162)
  and A3 (ADR-0158/0159) are the reframed earlier work. Ablation coverage is now
  A1–A5 measured, A6 (server_share) planned (seocho-xju), Level-1 integrated next
  (seocho-41a). Feeds the F2 ablation-grid figure.
- Both are OFF/ON on shipped mechanisms with the axis each subsystem owns — the
  "each part, before/after" the study requires.
