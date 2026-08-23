"""Decode-time enforcement of the Text2Cypher policy: an EBNF the ontology derives.

`validate_text2cypher_fallback` rejects after the fact, and every rejection costs a full
regeneration — prompt prefill plus decode, repeated until the model guesses the rule it was
never shown. Measured on the AIsummit26 graph-agentic-RAG harness (gpt-oss-120b, 26-episode
arm): 2.8-3.9 attempts per generation and 101.7 s of failed-generation LLM time booked in one
run. The rules the validator checks are structural, and the policy is strict, which makes them
expressible as a grammar instead of a rejection: under constrained decoding an undeclared
label, a missing tenant scope, an inlined literal, or a missing `LIMIT $limit` stops being a
repair case because it cannot be emitted at all. vLLM (>= 0.27) takes the EBNF through
`provider_options={"structured_outputs": {"grammar": ...}}`.

The envelope matters: the text2cypher contract is a JSON object with a `cypher` key, so the
grammar produces *that*, with the query inside the string. That is only safe because the
policy already forbids inlined literals — a conforming query contains no double quote to
escape.

Positioning, measured rather than hoped (arXiv-style caveats up front):

* The guarantee is **validity, scope and auditability**, not answer quality. On small models
  the structural rescue is total (Qwen2.5-1.5B: 0/8 -> 8/8 valid generations); on a 120B the
  unconstrained decode already writes valid Cypher and the grammar can *steer it off* a
  high-prior idiom it needed (a constraint shifts probability mass at every branch point —
  admissibility of the right answer does not make the right answer likely).
* Some endpoints accept `structured_outputs` with HTTP 200 and silently ignore it. Never
  A/B a grammar without `grammar_is_honored` — the false null looks exactly like a finding.

EVERY repetition is bounded — `{0,N}` instead of `*` — because under constrained decoding an
unbounded repetition is an attractor. Measured twice on gpt-oss-120b before the rule became
absolute: unbounded whitespace produced a run of spaces until the token cap truncated the
envelope, and after that was bounded, the repair turn fell into the AND-chain instead —
19,390 characters of `AND a.acct_no = $acct_no` repeated until an 8,000-token cap. Once the
model has emitted the same clause twice, the highest-probability continuation is a third.

Relationship variables are mandatory (`[t:TRANSFER]`, never `[:TRANSFER]`): "a variable an
aggregate references must be bound in the pattern" is context-sensitive and no CFG can say
it, and the measured failure mode is the model aggregating an anonymous relationship and
repeating the mistake through the repair turn. Forcing a name does not *prove* the reference
is bound, but the model reuses the name it was made to write.

A bare `(var)` node re-references a variable bound earlier — the second-MATCH continuation
and comma patterns need it, and every gold query in the measured corpus uses it. A CFG cannot
check the variable *was* bound, so an all-bare pattern is admissible and would sweep; that is
accepted deliberately, because the sweep guard was never the grammar's to give (a fully
scoped query has been measured sweeping 6.4M db hits *through an index*) — cost belongs to a
plan-shape check (leaf EstimatedRows), admissibility to this grammar.

Predicates compare `var.prop` against a parameter, plus exactly two more forms: `a <> b`
node inequality (the self-loop-exclusion idiom reachability queries use) and
`a.prop <> b.prop`. The nonsense form (`node <> $param`) stays out. The `param` rule must
name what the *executor* accepts, not whatever the caller has bound — a grammar that admits
an alias the executor drops produces ParameterMissing at run time (28 of 43 tool failures in
one measured run).

A question this subset cannot express is a finding about the subset, not a licence to widen
it silently — `covers()` exists so callers can report which questions fall outside.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_AGGREGATES = ("count", "sum", "avg", "min", "max", "collect")


def _alt(values: Iterable[str]) -> str:
    """EBNF alternation of literal strings, longest first so prefixes cannot shadow."""
    uniq = sorted({v for v in values if v}, key=lambda s: (-len(s), s))
    return " | ".join(f'"{v}"' for v in uniq) or '"NONE"'


def grammar_from_policy(policy: Any, *, params: Sequence[str],
                        workspace_property: Optional[str] = None,
                        max_hops: Optional[int] = None) -> str:
    """An EBNF that only admits Cypher this policy would accept."""
    labels = tuple(getattr(policy, "allowed_labels", ()) or ())
    rels = tuple(getattr(policy, "allowed_relationships", ()) or ())
    props = tuple(getattr(policy, "allowed_properties", ()) or ())
    ws_prop = workspace_property or getattr(policy, "workspace_property", "_workspace_id")
    hops = int(max_hops or getattr(policy, "max_graph_hops", 4) or 4)
    # `$limit` and `$workspace_id` are mandatory by contract, so they are terminals rather
    # than choices; the rest of the parameter list is what the harness bound for this question.
    param_names = tuple(p for p in params if p not in ("workspace_id",))

    return f'''root ::= "{{" ws "\\"cypher\\"" ws ":" ws "\\"" query "\\"" ws "}}"

query ::= match_clause (ws match_clause){{0,3}} (ws where_clause)? ws return_clause (ws order_clause)? ws limit_clause

match_clause ::= "MATCH " pattern ("," ws pattern){{0,2}}
pattern ::= node (rel node){{0,4}}
node ::= refnode | fullnode
refnode ::= "(" var ")"
fullnode ::= "(" var? label_part? scope ")"
label_part ::= ":" label ("|" label){{0,2}}
scope ::= " {{" wsp "{ws_prop}" wsp ":" wsp "$workspace_id" wsp ("," ws prop wsp ":" wsp param wsp){{0,2}} "}}"
rel ::= arrow_l ws rel_detail ws arrow_r ws
arrow_l ::= "-" | "<-"
arrow_r ::= "->" | "-"
rel_detail ::= "[" var ":" reltype hop_bound? "]"
hop_bound ::= "*1.." digit
digit ::= {_alt(str(i) for i in range(1, hops + 1))}

where_clause ::= "WHERE " predicate (" AND " predicate){{0,5}}
predicate ::= qref ws comparator ws param | var " <> " var | qref " <> " qref
qref ::= var "." prop
comparator ::= "=" | ">=" | "<=" | ">" | "<" | "<>"
param ::= {_alt("$" + p for p in param_names) if param_names else '"$limit"'}

return_clause ::= "RETURN " ret_item (", " ret_item){{0,7}}
ret_item ::= (aggregate | ref | "DISTINCT " ref) (" AS " var)?
aggregate ::= agg_fn "(" ("DISTINCT " )? ref ")"
agg_fn ::= {_alt(_AGGREGATES)}

order_clause ::= "ORDER BY " ref (" DESC" | " ASC")?
limit_clause ::= "LIMIT $limit"

ref ::= var ("." prop)?
label ::= {_alt(labels)}
reltype ::= {_alt(rels)}
prop ::= {_alt(props)}
var ::= [a-z] [a-z0-9_]{{0,24}}
ws ::= " "?
wsp ::= " "?
'''


def covers(cypher: str, policy: Any) -> Tuple[bool, List[str]]:
    """A cheap structural check that a query is inside the subset the grammar admits.

    Not a parser — a way for the benchmark to say "this question needs constructs the grammar
    does not have" instead of quietly attributing the failure to the model.
    """
    reasons: List[str] = []
    text = " ".join(cypher.split())
    upper = text.upper()
    if "LIMIT $LIMIT" not in upper:
        reasons.append("missing LIMIT $limit")
    ws_prop = getattr(policy, "workspace_property", "_workspace_id")
    if ws_prop not in text:
        reasons.append(f"missing scope {ws_prop}")
    if '"' in text or "'" in text:
        reasons.append("string literal (policy requires parameters)")
    for kw in ("WITH ", "UNWIND ", "CALL ", "UNION", "OPTIONAL MATCH", "CASE "):
        if kw in upper:
            reasons.append(f"outside subset: {kw.strip()}")
    return (not reasons), reasons

async def grammar_is_honored(backend: Any, model: str) -> Dict[str, Any]:
    """Prove the endpoint actually applies a grammar before believing a grammar-mode result.

    Some endpoints accept `structured_outputs` (and the older `guided_grammar`) with HTTP 200
    and ignore it — measured 2026-08-22 against a hosted gpt-oss-120b: the output was
    byte-identical to the unconstrained baseline for all three request spellings. An A/B run
    without this check reports "the grammar did not help" when the truth is "the grammar was
    never applied". So the probe asks a question only one answer can satisfy.

    Two traps this probe already absorbed, kept so they stay absorbed: reasoning-channel
    models spend ~70 tokens before the constrained final channel opens, so a tight
    `max_tokens` truncates the sentinel and reports a WORKING grammar as ignored (the false
    null in the opposite direction); and harmony-format models append terminator artifacts
    like `<|end|>`, so the test is containment, not equality — an unconstrained model asked
    to say hello never emits the sentinel, so containment cannot false-positive.
    """
    sentinel = "GRAMMAR_WAS_HONORED"
    try:
        r = await backend.acomplete(
            system="",
            user="Say hello in one sentence.",
            temperature=0.0,
            max_tokens=2048,
            task_hint="probe",
            mode="pipeline",
            model=model,
            provider_options={"structured_outputs": {"grammar": f'root ::= "{sentinel}"'}},
        )
        text = (getattr(r, "text", None) or str(r)).strip()
    except Exception as exc:  # a refusal is an answer: the endpoint cannot do this
        return {"honored": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return {"honored": sentinel in text, "output": text[:120]}


__all__ = ["grammar_from_policy", "covers", "grammar_is_honored"]
