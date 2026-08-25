"""The grammar admits what the validator would pass, and cannot emit what it would reject.

Every safety property here was a measured failure before it was a rule (AIsummit26 harness,
gpt-oss-120b and Qwen2.5-1.5B, 2026-08-22):

* an unscoped node, an inlined literal, an anonymous relationship — the rejection classes
  that cost 2.8-3.9 repair attempts per generation when enforced after the fact;
* the 9-AND runaway — under constrained decoding an unbounded repetition is an attractor
  (19,390 characters of the same clause, measured), so every repetition carries a bound;
* the expressibility set — union labels, comma patterns, reference nodes, `a <> b` node
  inequality — each added because a correct human-written query in the measured corpus
  needed it, and its absence pushed the constrained model into wrong-but-admissible output.

Membership tests compile the EBNF with xgrammar — the same engine vLLM constrains with — so
"the grammar admits X" is decided by the enforcement engine itself, not a reimplementation.
They skip cleanly where xgrammar is not installed; the structural tests always run.
"""

from __future__ import annotations

import pytest

from seocho.query.grammar import covers, grammar_from_policy
from seocho.query.workload_compiler import Text2CypherFallbackPolicy


@pytest.fixture
def policy() -> Text2CypherFallbackPolicy:
    return Text2CypherFallbackPolicy(
        allowed_labels=("Account", "Person", "Company"),
        allowed_relationships=("TRANSFER", "OWN"),
        allowed_properties=("acct_no", "amount", "id", "_workspace_id"),
        workspace_property="_workspace_id",
        required_parameters=("workspace_id",),
        max_graph_hops=4,
        max_result_rows=50,
        max_repair_attempts=1,
        require_explain_before_execute=True,
    )


PARAMS = ("workspace_id", "limit", "acct_no")


def test_grammar_derives_from_the_policy(policy) -> None:
    g = grammar_from_policy(policy, params=PARAMS)
    # The ontology's vocabulary, verbatim: labels, relationship types, properties.
    for term in ("Account", "Person", "TRANSFER", "OWN", "acct_no"):
        assert f'"{term}"' in g
    # The two contract terminals are hard-coded, not choices.
    assert "$workspace_id" in g
    assert 'limit_clause ::= "LIMIT $limit"' in g
    # No unbounded repetition anywhere: `*` may only appear inside quoted terminals
    # (the variable-length hop syntax), never as an EBNF repetition operator.
    for line in g.splitlines():
        outside_quotes = "".join(
            part for i, part in enumerate(line.split('"')) if i % 2 == 0
        )
        assert "*" not in outside_quotes, f"unbounded repetition in: {line}"


def test_covers_reports_the_subset_boundary(policy) -> None:
    inside = (
        "MATCH (a:Account { _workspace_id: $workspace_id, acct_no: $acct_no })"
        "-[t:TRANSFER]->(b:Account { _workspace_id: $workspace_id }) "
        "RETURN count(t) AS n LIMIT $limit"
    )
    ok, reasons = covers(inside, policy)
    assert ok, reasons

    with_pipeline = (
        "MATCH (o:Person { _workspace_id: $workspace_id })-[r:OWN]->(a:Account "
        "{ _workspace_id: $workspace_id }) WITH o, count(a) AS held "
        "RETURN o.id AS owner, held LIMIT $limit"
    )
    ok, reasons = covers(with_pipeline, policy)
    assert not ok
    assert any("WITH" in r for r in reasons)


# --- membership: decided by the enforcement engine itself -------------------------------

xgr = pytest.importorskip("xgrammar")


@pytest.fixture
def matcher(policy):
    grammar = grammar_from_policy(policy, params=PARAMS)
    info = xgr.TokenizerInfo(
        [chr(c) for c in range(32, 127)], vocab_type=xgr.VocabType.RAW
    )
    compiled = xgr.GrammarCompiler(info).compile_grammar(xgr.Grammar.from_ebnf(grammar))

    def member(cypher: str) -> bool:
        m = xgr.GrammarMatcher(compiled)
        text = '{"cypher": "' + " ".join(cypher.split()) + '"}'
        return bool(m.accept_string(text) and m.is_completed())

    return member


def test_admits_the_canonical_query_shapes(matcher) -> None:
    for cypher in (
        # anchored aggregate, anchor in the node map
        "MATCH (a:Account { _workspace_id: $workspace_id, acct_no: $acct_no })"
        "-[t:TRANSFER]->(b:Account { _workspace_id: $workspace_id }) "
        "RETURN count(t) AS n, sum(t.amount) AS total LIMIT $limit",
        # union label the ontology itself declares
        "MATCH (o:Person|Company { _workspace_id: $workspace_id })-[r:OWN]->"
        "(a:Account { _workspace_id: $workspace_id, acct_no: $acct_no }) "
        "RETURN o.id AS owner LIMIT $limit",
        # second MATCH re-referencing a bound variable, variable-length hops,
        # node-inequality self-loop exclusion
        "MATCH (s:Account { _workspace_id: $workspace_id, acct_no: $acct_no }) "
        "MATCH (s)-[t:TRANSFER*1..2]->(r:Account { _workspace_id: $workspace_id }) "
        "WHERE r <> s RETURN count(DISTINCT r) AS reach LIMIT $limit",
    ):
        assert matcher(cypher), cypher


def test_rejects_every_validator_class_at_decode_time(matcher) -> None:
    rejected = {
        "unscoped node": "MATCH (a:Account) RETURN count(a) AS n LIMIT $limit",
        "inlined literal": (
            "MATCH (a:Account { _workspace_id: $workspace_id }) "
            "WHERE a.amount > 3 RETURN count(a) AS n LIMIT $limit"
        ),
        "anonymous relationship": (
            "MATCH (a:Account { _workspace_id: $workspace_id })-[:TRANSFER]->"
            "(b:Account { _workspace_id: $workspace_id }) "
            "RETURN count(b) AS n LIMIT $limit"
        ),
        "node compared to a parameter": (
            "MATCH (a:Account { _workspace_id: $workspace_id })-[t:TRANSFER]->"
            "(b:Account { _workspace_id: $workspace_id }) "
            "WHERE b <> $acct_no RETURN count(t) AS n LIMIT $limit"
        ),
        "missing LIMIT": (
            "MATCH (a:Account { _workspace_id: $workspace_id, acct_no: $acct_no })"
            "-[t:TRANSFER]->(b:Account { _workspace_id: $workspace_id }) "
            "RETURN count(t) AS n"
        ),
        "repetition runaway": (
            "MATCH (a:Account { _workspace_id: $workspace_id }) "
            "WHERE a.acct_no = $acct_no"
            + " AND a.acct_no = $acct_no" * 8
            + " RETURN count(a) AS n LIMIT $limit"
        ),
    }
    for name, cypher in rejected.items():
        assert not matcher(cypher), name


# --- the option threads through generate_validated_cypher --------------------------------


class _StubResponse:
    def __init__(self, cypher: str) -> None:
        self._cypher = cypher
        self.text = '{"cypher": "' + cypher + '"}'
        self.usage = {"prompt_tokens": 100, "completion_tokens": 40, "cached_tokens": 80}

    def json(self):
        return {"cypher": self._cypher}


class _StubBackend:
    """Captures the request kwargs; returns a fixed, policy-conforming query."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def acomplete(self, **kwargs):
        self.calls.append(kwargs)
        return _StubResponse(
            "MATCH (a:Account { _workspace_id: $workspace_id, acct_no: $acct_no })"
            "-[t:TRANSFER]->(b:Account { _workspace_id: $workspace_id }) "
            "RETURN count(t) AS n LIMIT $limit"
        )



async def test_grammar_option_reaches_the_request_and_displaces_response_format(policy) -> None:
    from seocho.query.text2cypher import generate_validated_cypher

    backend = _StubBackend()
    grammar = grammar_from_policy(policy, params=PARAMS)

    async def explain(cypher, params) -> None:
        return None

    result = await generate_validated_cypher(
        question="how many transfers?",
        schema={"Account": ("acct_no",)},
        params={"workspace_id": "t1", "limit": 50, "acct_no": 1},
        policy=policy,
        backend=backend,
        model="m",
        explain=explain,
        grammar=grammar,
    )
    call = backend.calls[0]
    # The grammar rides provider_options, and response_format is NOT sent alongside it:
    # vLLM refuses both constraint mechanisms on one request.
    assert call["provider_options"] == {"structured_outputs": {"grammar": grammar}}
    assert call["response_format"] is None
    # Timing and usage are on the result — the failure path carries the same numbers on
    # the raised exception, so episode accounting never books LLM time as database time.
    assert result.generate_ms > 0
    assert result.usage == {"prompt_tokens": 100, "completion_tokens": 40, "cached_tokens": 80}



async def test_without_grammar_the_request_is_unchanged(policy) -> None:
    from seocho.query.text2cypher import generate_validated_cypher

    backend = _StubBackend()

    async def explain(cypher, params) -> None:
        return None

    await generate_validated_cypher(
        question="how many transfers?",
        schema={"Account": ("acct_no",)},
        params={"workspace_id": "t1", "limit": 50, "acct_no": 1},
        policy=policy,
        backend=backend,
        model="m",
        explain=explain,
    )
    call = backend.calls[0]
    assert call["provider_options"] is None
    assert call["response_format"] == {"type": "json_object"}
