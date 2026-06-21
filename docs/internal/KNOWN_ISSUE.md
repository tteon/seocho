# Known Issues and Risk Handling Log

This file is the single source of truth for risk handling in this repository.

## Process
1. When a new risk is identified, add one row in the table with `Status = OPEN`.
2. When implementation starts, change `Status = IN_PROGRESS` and record owner/date.
3. After code fix and verification, change `Status = RESOLVED` and add evidence links.
4. If a fix is partial or blocked, keep `Status = OPEN` and add next action.
5. Every risk-handling PR/session must update this file.

## Status Legend
- `OPEN`: identified but not fixed
- `IN_PROGRESS`: fix implementation is ongoing
- `RESOLVED`: fix merged and verified

## Issue Log

| ID | Severity | Issue | Status | Opened (UTC) | Resolved (UTC) | Verification | Evidence |
|---|---|---|---|---|---|---|---|
| KI-2026-02-13-001 | P1 | Makefile referenced non-existent `engine` service and `src/` paths, breaking quality gates. | RESOLVED | 2026-02-13 | 2026-02-13 | Manual review | `Makefile` |
| KI-2026-02-13-002 | P1 | Neo4j procedure privileges were over-broad (`NEO4J_dbms_security_procedures_unrestricted=*`). | RESOLVED | 2026-02-13 | 2026-02-13 | Manual review | `docker-compose.yml` |
| KI-2026-02-13-003 | P1 | `semantic` service used `print` logging and raw exception details in HTTP 500 responses. | RESOLVED | 2026-02-13 | 2026-02-13 | Manual review | `semantic/main.py`, `semantic/agent.py`, `semantic/neo4j_client.py` |
| KI-2026-02-13-004 | P1 | Roadmap and implementation drift reduced reliability of operational docs. | RESOLVED | 2026-02-13 | 2026-02-13 | Manual review | `docs/ROADMAP.md`, `docs/KNOWN_ISSUE.md` |
| KI-2026-02-20-005 | P1 | Extraction/Semantic HTTP tests hung under `TestClient` and API tests failed from local module shadowing (`neo4j`, `agents`). | RESOLVED | 2026-02-20 | 2026-02-20 | Focused pytest | `extraction/tests/test_middleware.py`, `extraction/tests/test_error_responses.py`, `extraction/tests/test_api_endpoints.py`, `extraction/tests/test_api_integration.py`, `semantic/tests/test_api.py` |
| KI-2026-02-20-006 | P2 | `scripts/pm/lint-items.sh` stalled when `bd` daemon startup lagged in local environments. | RESOLVED | 2026-02-20 | 2026-02-20 | Script execution | `scripts/pm/lint-items.sh` |
| KI-2026-02-20-007 | P2 | Docs sync automation contract exists, but remote rollout requires owner-level `workflow` scope permission. | OPEN | 2026-02-20 |  | Push validation | Pending owner token/permission update |
| KI-2026-02-20-008 | P2 | Teams lacked a concrete signal for whether SHACL-like constraints were production-usable on real payloads. | RESOLVED | 2026-02-20 | 2026-02-20 | Focused pytest + API contract review | `extraction/rule_api.py`, `extraction/agent_server.py`, `docs/SHACL_PRACTICAL_GUIDE.md`, `scripts/rules/shacl_practical_demo.py` |

## Verification Notes
- Focused local suites executed:
  - `python3 -m pytest -q extraction/tests/test_middleware.py extraction/tests/test_error_responses.py extraction/tests/test_api_endpoints.py extraction/tests/test_api_integration.py semantic/tests/test_api.py`
  - `python3 -m pytest -q extraction/tests/test_rule_constraints.py extraction/tests/test_retry.py extraction/tests/test_exceptions.py`
- PM/documentation checks executed:
  - `bash scripts/pm/lint-items.sh --sprint 2026-S03`
  - `bash scripts/pm/lint-agent-docs.sh`
- Full Docker-based end-to-end checks were not executed in this environment due Docker daemon permission constraints.
