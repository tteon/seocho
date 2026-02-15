# AGENTS.md

This file provides guidance to coding agents collaborating on this repository.

## Project Overview

SEOCHO (서초) is an Enterprise GraphRAG & Multi-Agent Orchestration Framework that bridges unstructured data and structured knowledge graphs. It provides:

- Multi-agent orchestration with OpenAI Agents SDK
- Knowledge graph construction and Text2Cypher querying
- Real-time agent trace visualization
- Data Mesh integration with DataHub metadata governance
- FIBO (Financial Industry Business Ontology) support

## Project Vision

The de facto enterprise framework for building observable, multi-agent systems that reason over knowledge graphs.

## Project Requirements

- Always use English in code, examples, and comments.
- Features should be implemented concisely, maintainably, and efficiently.
- Code is not just for execution, but also for readability.
- Use type hints for all function signatures (PEP 8 compliance).
- Agent tools must be stateless and return serializable results.
- Never hardcode credentials; use environment variables.

## Architecture

> For detailed module descriptions, database architecture, agent definitions, and service ports, see **CLAUDE.md**.

The project is a Docker-based microservices architecture. Key directories: `extraction/`, `evaluation/`, `semantic/`, `demos/`.

## Common Development Commands

> For full command reference, see **CLAUDE.md**. Quick reference below:

```bash
make up / make down / make restart    # Docker lifecycle
make test / make lint / make format   # Quality gates
make shell                            # Shell into extraction-service
```

Issue/task operations:

```bash
scripts/pm/new-issue.sh ...      # standardized issue capture
scripts/pm/new-task.sh ...       # standardized task capture
scripts/pm/sprint-board.sh --sprint 2026-S03
scripts/pm/lint-items.sh         # enforce required collaboration labels
```

## Key Technical Details

1. **Multi-Agent Architecture**: Hierarchical agent system using OpenAI Agents SDK with Router → Specialists → Supervisor pattern
2. **Tool Decoration**: Use `@function_tool` decorator for agent tools; tools receive `RunContextWrapper` for context access
3. **Async-first**: Agent server uses asyncio; IO-bound operations should be async-compatible
4. **Database Allowlist**: GraphDBA validates database names (`kgnormal`, `kgfibo`, `neo4j`, `agent_traces`) before Cypher execution
5. **Schema Discovery**: SchemaManager dynamically reads and applies Neo4j schemas from YAML definitions
6. **Trace Observability**: All agent executions are traced via `trace()` context manager and visualized in Streamlit
7. **Configuration**: Hydra + OmegaConf for hierarchical YAML configuration with environment variable interpolation

## Development Notes

- All agent tools should have comprehensive docstrings (used by LLM for tool selection)
- Agent handoffs are explicit; define `handoffs` list when creating agents
- Use `st.session_state` for Streamlit state management
- Neo4j queries should use parameterized values to prevent Cypher injection
- Always rebuild Docker images after changing `requirements.txt`
- Integration tests require all Docker services running

## Development Tips

Code standards:
- Use `@dataclass` for configuration and context objects
- Prefer composition over inheritance for agent specialization
- Return JSON-serializable results from tools for trace logging
- Use type hints consistently; the codebase uses Python 3.11+ features

Agents:
- Inherit from `BaseAgent` for custom agents; implement `validate_input()` method
- Register tools globally via `@register_tool("name")` decorator or locally via agent's `tools` list
- Keep agent instructions focused and specific; avoid generic prompts
- GraphDBA should always check schema before generating Cypher

Tests:
- Place tests in `extraction/tests/` or `semantic/tests/`
- Use `pytest` fixtures for Neo4j and API client setup
- Mock external services (OpenAI, DataHub) in unit tests
- Include regression tests for bug fixes

Configuration:
- Add new prompts to `extraction/conf/prompts/` as Jinja2 YAML files
- Define schemas in `extraction/conf/schemas/` with node labels, relationships, and properties
- Use environment variables for secrets: `${oc.env:OPENAI_API_KEY}`

## Review Guidelines

Please note that the attention of contributors and maintainers is the MOST valuable resource.
Less is more: focus on the most important aspects.

- Your review output SHOULD be concise and clear.
- You SHOULD only highlight P0 and P1 level issues, such as severe bugs, performance degradation, or security concerns.
- You MUST not reiterate detailed changes in your review.
- You MUST not repeat aspects of the PR that are already well done.

Please consider the following when reviewing code contributions.

### Agent Design
- Ensure agent instructions are clear and unambiguous
- Verify handoff chains are properly defined and don't create cycles
- Check that tools return appropriate error messages for debugging
- Validate that database queries use the allowlist for database names

### API Design
- Use Pydantic models for request/response validation
- Return structured `AgentResponse` with both `response` and `trace_steps`
- Include proper HTTP error codes and error messages

### Testing
- Ensure all new agent tools have corresponding tests
- Ensure that all bugfixes and features have corresponding tests
- Test agent handoff scenarios end-to-end

### Documentation
- New agents must include docstrings explaining their role and capabilities
- Update `extraction/conf/prompts/` with any new prompt templates
- Link to relevant modules and classes in documentation

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
   - Push target branch policy: **always push to `main`**.
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
