# Python maintainability refactor

This living plan follows `docs/maintainers/EXECPLAN_SPEC.md`.

## Purpose / Big Picture


Make execution results serializable, preserve namespaced DataHub approval tags,
and make runtime tools report the registered database schema. Consolidate
repeated helpers and separate SDK, CLI and indexing responsibilities while
preserving public entrypoints and experiment evidence.

## Progress


- [x] Audit main b6dde9f6 and reproduce two data-contract failures.
- [x] Create isolated checkout and claim beads seocho-87yj.1.
- [x] Repair result, connector and runtime contracts with focused tests.
- [x] Consolidate semantic helpers and remove verified unused code.
- [x] Separate CLI commands, SDK plan/async ownership and indexing phases.
- [x] Clarify file tracking and measure its local I/O behavior.
- [x] Update architecture contracts, run basic CI and review the final diff;
      PR #679 records delivery to main.

## Surprises & Discoveries


The slots dataclass captures a same-named property as a default value. Two
DataHub tag functions silently replace one another. Runtime tools import
canonical helpers but redefine them with different file/DB behavior. The environment checker inventories Git-tracked
files, so moved vocabulary settings become visible only after staging the new
module. Broader Ruff found missing type imports and a missing metric accessor;
these are corrected without adding new telemetry backends.

## Decision Log


2026-09-13 / Codex: Use a stored agent_pattern mapping, populated from the envelope on construction
when the explicit mapping is empty; a non-empty explicit mapping wins. Keep
dataset tag display separate from glossary approval normalization. Runtime
server_runtime owns registered DB discovery and live schema retrieval; the
server exposes these through existing tool names. File tracking v2 explicitly
uses mtime/size, retaining legacy hash fields without recomputing them. Preserve public compatibility
imports, decorator registrations and optional provider implementations.

## Outcomes & Retrospective


Implementation complete; basic CI passed with 1,214 tests and 5 skips.
Public CLI argument trees/defaults and Seocho/AsyncSeocho method signatures
match the baseline snapshot. Delivery status is recorded in
[PR #679](https://github.com/tteon/seocho/pull/679). No live backend compatibility,
answer quality or production performance claim follows from offline tests.

## Context and Orientation


The Python package lives in src/seocho. runtime is the deployment shell;
extraction contains legacy services and import compatibility. client.py is the
SDK facade, cli is the command entrypoint, and index/pipeline.py orchestrates
document extraction and graph writes. Beads seocho-87yj.1 through .6 track the
six audited work streams. Run reports already expose typed experiment outcomes
for local visualization; internal refactoring must preserve those artifacts.

## SEOCHO Evidence Contract


Preserve ontology signals (whether the selected ontology helped or failed),
required answer slots, graph relation paths, provenance (source support), and
insufficiency (missing evidence). Existing evidence bundles, workspace_id,
policy checks, Cypher validation and conservative answer behavior stay intact.
This refactor does not change prompts, ranking or quality policy.

## SEOCHO Review Panel


The semantic-validity lens favors identical shared helper behavior and explicit
approval normalization. The software-engineering lens favors one implementation
owner, narrow protocols and stable facades. The systems lens favors bounded file
reads and no new orchestration or caching. Reject a refactor if contract tests
show changed evidence, tool scope or call ordering; do not mask it with mocks.

## Cost, Latency, and Provider Policy


No paid model calls or new remote resources are needed for this structural
work. Keep DozerDB and OpenAI Agents SDK baselines and neutral tracing. File I/O
measurements use local temporary files and identify their conditions. These are
not graph or LLM throughput measurements.

## Plan of Work


First fix models.py, connectors/datahub.py and runtime tool ownership. Next move
shared semantic implementation into SDK owners with extraction aliases. Remove
private helpers only after reference and compatibility checks. Separate command
parser/dispatch owners, SDK namespaces and indexing stages without changing
public signatures. Finally make file tracking use a documented bounded-read
contract, record ADR and update owner docs.

## Concrete Steps


Run commands from the isolated checkout after uv sync --locked --extra dev.
Use focused pytest files for each seam, then the complete basic gate:

    uv run pytest tests/seocho/test_execution_result_contract.py tests/seocho/test_datahub_glossary_pull.py extraction/tests/test_tools.py -q
    bash scripts/ci/run_basic_ci.sh
    python3 scripts/ci/check-import-boundaries.py
    bash scripts/ci/check-module-ownership-contract.sh
    bash scripts/ci/check-runtime-shell-contract.sh
    git diff --check

## Validation and Acceptance


Default result JSON serialization and namespaced approval tests pass. Active
runtime tool tests verify registry rejection and connector schema retrieval.
Existing SDK import, CLI argument, semantic helper, indexing failure/provenance
and file tracker tests pass. Basic CI passes. Meaningful local file measurements
state bytes read and peak traced allocations; no unmeasured speedup claim.

## Idempotence and Recovery


Use the isolated codex/seocho-87yj-refactor checkout; do not touch research
branches, original manifests or local tracker storage. Commit coherent slices;
recover with a reviewed revert of an individual slice rather than resetting
other work. File tracker loading remains compatible with existing state.

## Artifacts and Notes


The original audit and two failing reproductions are under the clean main
checkout's ignored .seocho/audits/maintainability-20260913 directory. Public
contracts and this plan hold durable decisions; local logs remain ignored.

## Interfaces and Dependencies


Preserve seocho.Seocho/AsyncSeocho, ExecutionResult constructor fields, existing
CLI flags, extraction compatibility imports and runtime tool names. Reuse
CommandGroup and existing store interfaces. New internal phase or namespace
modules depend on SDK contracts, never runtime or extraction implementations.

## Revision Notes


2026-09-13: Implemented ADR-0234. Default ExecutionResult JSON and DataHub URN
fallback regressions pass. Runtime tests invoke registered tools against an
injected connector and verify unknown DB rejection; they are not a live gate.
Shared semantic lookup preserves global/workspace precedence and cache clearing.
Private _LocalEngine._link, QualificationStore._executemany and an unused client
ontology-contract wrapper were removed. Registered factories and explicit
compatibility shims remain. Provider dispatch retains specialized backends.

Local post-index bookkeeping measurement used CPython 3.11.14, one worker,
one warmup and three trials per size. For a 16 MiB file, the removed full-text
hash path read an extra 16 MiB and had median peak traced Python allocations of
239,039,581 bytes; stat-only bookkeeping read no content and peaked at 1,036
bytes. This isolates tracking and excludes extraction, graph/LLM work and RSS.
Original receipt/script are ignored under .seocho/refactor. No paid calls ran.

Validation includes basic CI (1,214 passed, 5 skipped), product-wide Ruff E9/F,
import/module/runtime/root/ADR/doc contracts, generated website documentation
quality, and a scaffold dry-run with a visibly placeholder credential (no model
or graph connections). The real credential-free smoke correctly refused the
run. Original research edits and experiment memory/receipts remain untouched.

Final review: the committed candidate passes the complete basic gate, including
commit identity checks. CLI smoke, public signature snapshots and targeted
provider tests pass. Remaining compatibility shims and decorator registrations
were deliberately retained; no required refactoring item was left as a proposal.
