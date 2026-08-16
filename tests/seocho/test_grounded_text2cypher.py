"""Ontology-grounded text2cypher generator (the guardrail-conformant retrieve step).

Validated live against DozerDB+MARA (governed arm answered correctly once the
generator emitted declared-only, all-node-workspace-scoped, parameterized Cypher);
here we unit-test the prompt contract + JSON parsing + param hygiene with a fake LLM.
"""

from __future__ import annotations

from seocho.query.grounded_text2cypher import (
    build_text2cypher_system_prompt,
    generate_grounded_cypher,
)


class _Resp:
    def __init__(self, obj):
        self._obj = obj
        self.text = ""

    def json(self):
        return self._obj


class _FakeLLM:
    def __init__(self, obj):
        self._obj = obj
        self.last_system = None

    def complete(self, *, system=None, user=None, **kw):
        self.last_system = system
        return _Resp(self._obj)


def test_system_prompt_states_the_hard_rules_and_schema():
    p = build_text2cypher_system_prompt("Node labels: Company, Incident")
    assert "Node labels: Company, Incident" in p
    assert "$workspace_id" in p and "EVERY node" in p        # all-node tenant scope
    assert "LIMIT $limit" in p
    assert "never inline" in p.lower()                       # parameterize values


def test_returns_cypher_and_params_and_strips_runtime_params():
    llm = _FakeLLM({
        "cypher": "MATCH (c:Company {_workspace_id: $workspace_id, name: $company}) "
                  "RETURN c LIMIT $limit",
        # a model that wrongly tries to bind runtime params must be sanitized
        "params": {"company": "Acme Corp", "workspace_id": "attacker", "limit": 999},
    })
    cypher, params = generate_grounded_cypher(llm, "who is Acme?", "schema",
                                              workspace_id="acme", limit=50)
    assert cypher.startswith("MATCH (c:Company")
    assert params == {"company": "Acme Corp"}, "runtime-bound params stripped"


def test_malformed_llm_output_yields_empty_cypher():
    class _Bad(_FakeLLM):
        def complete(self, **kw):
            r = _Resp({}); r.text = "not json at all"; return r

    cypher, params = generate_grounded_cypher(_Bad({}), "q", "schema")
    assert cypher == "" and params == {}
