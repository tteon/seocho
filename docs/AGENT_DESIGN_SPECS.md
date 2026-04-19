# Agent Design Specs

SEOCHO can load a YAML agent design spec and compile it into the existing
`AgentConfig` + `ontology_profile` surface.

This keeps the runtime contract small:

- the YAML declares the agent pattern
- the ontology binding is explicit and required
- SEOCHO turns that into normal SDK construction

## Why

Use agent design specs when you want:

- reviewable agent behavior checked into git
- a required ontology slot for every design
- reusable pattern templates for product teams

If the YAML omits the top-level `ontology:` section, or the `ontology:` section
does not declare a binding such as `profile`, `ontology_id`, `package_id`, or
`path`, SEOCHO raises a `ValueError`.

## Supported Patterns

- `planning_multi_agent`
- `reflection_chain`
- `memory_tool_use`

These patterns map to bounded defaults on top of `AgentConfig`. Users can still
override individual fields in the YAML.

## Example

```yaml
name: planning-multi-agent-finance
pattern: planning_multi_agent
ontology:
  required: true
  profile: finance-core
agent:
  execution_mode: supervisor
  handoff: true
  routing_policy: thorough
query:
  query_strategy: template
  answer_style: evidence
indexing:
  extraction_strategy: domain
  validation_on_fail: retry
tools:
  - graph_query
  - filing_search
```

Load the spec and let SEOCHO build the client:

```python
from seocho import Ontology, Seocho

onto = Ontology.from_jsonld("schema.jsonld")

client = Seocho.from_agent_design(
    "examples/agent_designs/planning_multi_agent_finance.yaml",
    ontology=onto,
    llm="openai/gpt-4o-mini",
    workspace_id="finance-prod",
)
```

Or inspect the compiled config without building the client:

```python
from seocho import load_agent_design_spec

spec = load_agent_design_spec("examples/agent_designs/reflection_chain_finance.yaml")
config = spec.to_agent_config()
print(config.execution_mode)
print(spec.client_kwargs()["ontology_profile"])
```

## Included Examples

- [planning_multi_agent_finance.yaml](/tmp/seocho-land-finder-e2e/examples/agent_designs/planning_multi_agent_finance.yaml)
- [reflection_chain_finance.yaml](/tmp/seocho-land-finder-e2e/examples/agent_designs/reflection_chain_finance.yaml)
- [memory_tool_use_finance.yaml](/tmp/seocho-land-finder-e2e/examples/agent_designs/memory_tool_use_finance.yaml)
