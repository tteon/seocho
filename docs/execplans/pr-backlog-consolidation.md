# PR backlog consolidation

## Purpose and scope

Review the 62 PRs open on 2026-09-13 against main `09f72a45`, preserve useful
changes, and remove duplicate review work. The maintainer requested review and
main landing. Keep the existing local experiment branch and its uncommitted
work intact; construct the landing branch from current main.

## Progress

- [x] Inventory all PR heads, changed paths, patch IDs, and required checks.
- [x] Consolidate CLI copy (#664, #625), schema help (#531), local-ask JSON
  output (#619), JSONL iteration (#669), and empty judge guard (#192).
- [x] Port #170's non-object input warnings to the canonical file reader,
  preserving current streaming and nested metadata behavior.
- [x] Port only #174's provider change onto current main; preserve #629's
  structured-output support. Credit original contributions through PR links.
- [x] Check focused CLI, graph benchmark, file reader, and provider tests.
- [x] Run full basic CI: 1,126 passed, 3 skipped; focused suites: 101 passed.
- [x] Required GitHub checks passed before merge.
- [x] Merged #673 and closed superseded PRs with links.

## Decisions and review lenses

Semantic validity: do not equate a green mock test with correct graph answers.
#618 chooses a count anchor from the relationship role even when the supplied
entity label describes the other end; an ontology edge alone cannot identify
the entity meant by a question. Require coverage of source and target anchors
and inconsistent plan inputs before accepting the proposed heuristic.

API and maintenance: #146 advertises synthesis-only context but disables the
semantic fast path, changing retrieval execution. #624/#655 add scope enforcement
to queries that still use `$ws` and/or unscoped aliases; their broad exception
handlers can turn validator rejection into a false no-data result. Review these
separately with real validator tests and explicit public contract changes.

Systems evidence: #669 removes the full text/split-lines allocation but still
materializes parsed cases. No throughput or bounded total memory claim is made.
#623's `json.load` replacement still calls `fp.read()` in Python's JSON decoder;
it is not streaming JSON parsing. #192 deterministically rejects empty candidate
answers without invoking a judge, while terse nonempty answers still reach it.
There are no new paid calls or performance experiments in this consolidation.

## Existing work and exclusions

#621 is superseded by #626: 65 of its 70 changed paths have identical blobs on
main; the other five contain subsequent compose, DataHub, or judge improvements.
#477 is already represented by #630. #497's harness was deliberately landed
separately in #561; that PR records answer-key leakage and rejected SDK scorecard
work. Re-merging the old branch would undo the corrected experiment boundary.
#154 changes numeric IDs to strings while retaining legacy GDS Cypher projection;
it needs a coherent projection migration and live GDS validation.

## Validation and acceptance

Run the focused suites for CLI parser/ontology, GraphRAG-Bench adapter, FinDER
judge, file reader, and LLM backends, then `bash scripts/ci/run_basic_ci.sh`.
Verify that source imports resolve to the clean landing worktree, not the dirty
experiment checkout. Await all required Python 3.10/3.11/3.12 and documentation
checks before squash merge. Never bypass branch protection.

CLI contract: `seocho local-ask "question" --json` emits one object with an
`answer` string; plain output remains the answer text. Both paths close the
client. JSON/JSONL input warnings contain location/type, not record content.

## Outcomes

Merged #673 at 86a6e3f2 after validation; backlog disposition is recorded in the linked PR. Remaining experimental
and security PRs must retain an explicit finding and acceptance gate; closing a
duplicate is not evidence that every proposed behavior was accepted.
