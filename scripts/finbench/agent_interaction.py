"""Measure the agent<->database exchange, by audience, difficulty, scale and agent arm.

Everything before this script measured the database side: how a plan shape, a degree
distribution or a load path changes cost. That left the agent as an assumed constant. This
script makes the *exchange* the unit of measurement — one question in, an unknown number of
Cypher round trips out — and asks how the shape of that exchange changes as the graph grows
and as the agent is given more to work with.

Four axes.

**Audience.** Questions split into what an AML investigator inside an institution asks and what
a public-facing service asks on behalf of one customer. This is not a presentational split:
the two have opposite cost profiles. The external class is anchored on one account and lives
under a request SLO, so its cost is decided by whether the anchor reaches an index and by the
anchor's degree. The internal class is unanchored pattern search, so its cost is decided by
whether the plan can avoid scanning, and its *precision* — not its recall — is what scale
destroys.

**Difficulty**, defined structurally so it is not a matter of opinion:
  easy    one hop, one relationship type, an aggregate or a short ordered list
  medium  two hops, or one hop joined to a second layer (owner, channel)
  hard    three or more layers in conjunction, or an unanchored pattern search

**Scale.** SF1 / SF10 / SF100 of the same generator, same seed, all ten edge types present.

**Arm** — what the agent is given, which is the thing an operator can actually change:
  labels     label and relationship names only. Plain text2cypher.
  ontology   the full schema: relationship direction, endpoint roles, and the measured degree
             hint. Nothing is validated; the agent is only better informed.
  guardrail  ontology, plus SEOCHO's ontology check runs on the tool arguments before the
             query reaches the database. A violation is returned to the model as text, so a
             rejection becomes a repair rather than a failure.
  plan       guardrail, plus the tool runs EXPLAIN first and refuses to execute a plan that
             contains an all-nodes scan or whose estimated row count exceeds a budget. The
             planner's own estimate is handed back to the model. This is the arm that tests
             whether an agent can optimise when it is allowed to see the execution plan.

Each arm is a superset of the one above it, so the difference between two adjacent arms is
attributable to the one thing that changed.

What is recorded per episode: round trips, total db hits, rows and bytes returned into the
model's context, the operator histogram of every plan executed, database time against model
time, tokens, every guardrail and plan rejection with its reason, and whether the final answer
matched gold. db hits is the primary cost unit because it is the only one unaffected by
concurrent load on the box.

Usage:
  python scripts/finbench/agent_interaction.py --password "$PW" \
      --databases finbenchl1:1 finbenchl10:10 finbenchl100:100 \
      --out outputs/finbench/agent_interaction.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from agents import Agent, ModelSettings, Runner, function_tool
from agents.exceptions import MaxTurnsExceeded

WS = "default"
ROW_CAP = 50
TX_TIMEOUT_S = 60.0
MAX_TURNS = 8

# The plan arm runs a query under a short budget first and only commits to the full one if it
# survives. The budget is elapsed time, not the planner's row estimate, because the estimate
# does not work: measured across the 48 queries this run settled on at SF100, actual db hits
# ran from 2.9x to 4,617,254x the summed EstimatedRows. `EstimatedRows` is output cardinality
# per operator, so an anchored aggregate estimates one row while doing 23 million db hits, and
# any budget drawn on it either passes everything or blocks everything. Elapsed time is exact,
# and it is also the thing the external audience's SLO is actually written against.
PROBE_TIMEOUT_S = 2.0
# The override the agent may use when it judges the cost necessary. Without it the gate would
# be simply wrong for the internal audience, whose questions are unanchored by nature and
# cannot be made cheap — a gate tuned for a public request path must not silently become a
# policy that investigators may not run expensive queries.
ACCEPT_COST_MARK = "accept-cost"


# --------------------------------------------------------------------------------------
# Question set
# --------------------------------------------------------------------------------------
# Written in English so the measurement isolates retrieval rather than the model's Korean
# handling; the Korean gloss each question carries is what the audience split actually sounds
# like in the setting it comes from, and is reported alongside the numbers.
#
# Every question returns a small, shape-stable answer — a scalar pair or a top-5 under a total
# order. That is deliberate. If the gold grew with the graph, "correct" would mean a different
# thing at each scale and the accuracy column would be uninterpretable.

QUESTIONS: List[Dict[str, Any]] = [
    # ---------------- external, public-facing: anchored, subject-scoped, latency-bound -----
    {
        "id": "ext_easy_1", "audience": "external", "difficulty": "easy",
        "ko": "내 계좌로 지금까지 들어온 이체는 몇 건이고 총액은 얼마인가요?",
        "question": ("For account number {a}: how many transfers has it received in total, "
                     "and what is the total amount received?"),
        "shape": "scalar", "keys": ["n", "total"],
        "ref": ("MATCH (:Account {acct_no:$a,_workspace_id:$ws})<-[t:TRANSFER]-"
                "(:Account {_workspace_id:$ws}) RETURN count(t) AS n, sum(t.amount) AS total"),
    },
    {
        "id": "ext_easy_2", "audience": "external", "difficulty": "easy",
        "ko": "내 계좌에서 나간 이체 건수와 그 중 가장 큰 금액은?",
        "question": ("For account number {a}: how many outgoing transfers are there, and what "
                     "is the single largest amount sent?"),
        "shape": "scalar", "keys": ["n", "biggest"],
        "ref": ("MATCH (:Account {acct_no:$a,_workspace_id:$ws})-[t:TRANSFER]->"
                "(:Account {_workspace_id:$ws}) RETURN count(t) AS n, max(t.amount) AS biggest"),
    },
    {
        "id": "ext_med_1", "audience": "external", "difficulty": "medium",
        "ko": "내 계좌로 돈을 보낸 계좌 중 고위험 채널을 쓴 곳은 어디인가요?",
        "question": ("Which accounts sent money to account number {a} on a transfer whose own "
                     "channel_risk property is 5 or more? Give the five lowest such account "
                     "numbers in ascending order."),
        "shape": "list", "column": "acct",
        "ref": ("MATCH (s:Account {_workspace_id:$ws})-[t:TRANSFER]->"
                "(:Account {acct_no:$a,_workspace_id:$ws}) WHERE t.channel_risk>=5 "
                "RETURN DISTINCT s.acct_no AS acct ORDER BY acct LIMIT 5"),
    },
    {
        "id": "ext_med_2", "audience": "external", "difficulty": "medium",
        "ko": "내가 송금한 계좌들의 실제 소유자는 누구인가요?",
        "question": ("Who owns the accounts that account number {a} has sent money to? Give the "
                     "five lowest owner ids in ascending order."),
        "shape": "list", "column": "owner",
        "ref": ("MATCH (:Account {acct_no:$a,_workspace_id:$ws})-[:TRANSFER]->"
                "(b:Account {_workspace_id:$ws})<-[:OWN]-(o) "
                "RETURN DISTINCT o.id AS owner ORDER BY owner LIMIT 5"),
    },
    {
        "id": "ext_hard_1", "audience": "external", "difficulty": "hard",
        "ko": "내 돈이 두 단계 안에 닿는 계좌는 몇 개이고, 그 중 가장 위험한 등급은?",
        "question": ("Starting from account number {a} and following transfers downstream, how "
                     "many distinct accounts are reachable within two hops, and what is the "
                     "highest risk_tier among them?"),
        "shape": "scalar", "keys": ["n", "worst_risk_tier"],
        "ref": ("MATCH (:Account {acct_no:$a,_workspace_id:$ws})-[:TRANSFER*1..2]->"
                "(b:Account {_workspace_id:$ws}) "
                "RETURN count(DISTINCT b) AS n, max(b.risk_tier) AS worst_risk_tier"),
    },
    {
        "id": "ext_hard_2", "audience": "external", "difficulty": "hard",
        "ko": "내 계좌로 두 단계 안에 돈이 흘러들어온 계좌는 몇 개인가요?",
        "question": ("How many distinct accounts sit within two transfer hops upstream of "
                     "account number {a} — that is, accounts from which money reaches {a} in one "
                     "or two transfers?"),
        "shape": "scalar", "keys": ["n"],
        "ref": ("MATCH (b:Account {_workspace_id:$ws})-[:TRANSFER*1..2]->"
                "(:Account {acct_no:$a,_workspace_id:$ws}) RETURN count(DISTINCT b) AS n"),
    },

    # ---------------- internal, AML investigator: unanchored, completeness-bound -----------
    {
        "id": "int_easy_1", "audience": "internal", "difficulty": "easy",
        "ko": "전체 계좌 수와 최고위험(등급 5) 계좌 수는?",
        "question": ("How many accounts are there in total, and how many of them are at "
                     "risk_tier 5?"),
        "shape": "scalar", "keys": ["accounts", "tier5"],
        "ref": ("MATCH (a:Account {_workspace_id:$ws}) RETURN count(a) AS accounts, "
                "sum(CASE WHEN a.risk_tier=5 THEN 1 ELSE 0 END) AS tier5"),
    },
    {
        "id": "int_easy_2", "audience": "internal", "difficulty": "easy",
        "ko": "거래가 가장 많이 오간 채널 상위 5개는?",
        "question": ("Which five channels carry the most transactions? Give the channel codes "
                     "in descending order of total transaction count."),
        "shape": "list", "column": "code",
        "ref": ("MATCH (a:Account {_workspace_id:$ws})-[u:USES_CHANNEL]->"
                "(c:Channel {_workspace_id:$ws}) RETURN c.code AS code, sum(u.tx_count) AS n "
                "ORDER BY n DESC, code LIMIT 5"),
    },
    {
        "id": "int_med_1", "audience": "internal", "difficulty": "medium",
        "ko": "같은 사람이 소유한 계좌끼리 직접 송금이 오간 사례는 몇 건인가요?",
        "question": ("How many distinct ordered pairs of two *different* accounts owned by the "
                     "same party have a direct transfer running from the first to the second? "
                     "Count each pair once however many transfers run between them."),
        "shape": "scalar", "keys": ["n"],
        "ref": ("MATCH (o {_workspace_id:$ws})-[:OWN]->(a:Account {_workspace_id:$ws})"
                "-[:TRANSFER]->(b:Account {_workspace_id:$ws})<-[:OWN]-(o) WHERE a<>b "
                "RETURN count(DISTINCT [a.acct_no,b.acct_no]) AS n"),
    },
    {
        "id": "int_med_2", "audience": "internal", "difficulty": "medium",
        "ko": "100곳이 넘는 상대로부터 입금을 받은 계좌는 어디인가요?",
        "question": ("Which accounts received transfers from more than 100 distinct sending "
                     "accounts? Give the five lowest such account numbers in ascending order."),
        "shape": "list", "column": "acct",
        "ref": ("MATCH (s:Account {_workspace_id:$ws})-[:TRANSFER]->"
                "(t:Account {_workspace_id:$ws}) WITH t, count(DISTINCT s) AS fan "
                "WHERE fan>100 RETURN t.acct_no AS acct ORDER BY acct LIMIT 5"),
    },
    {
        "id": "int_hard_1", "audience": "internal", "difficulty": "hard",
        "ko": "서로 돈이 오가고, 소유자끼리 보증을 서줬고, 같은 기기로 로그인한 계좌 쌍을 찾아주세요.",
        "question": ("Find pairs of accounts that satisfy all three of these at once: money has "
                     "moved between them by transfer, their owners are different parties who "
                     "guarantee one another, and the same login device has signed in to both. "
                     "Give the account number pairs, smaller number first."),
        "shape": "list", "column": "a1",
        "ref": ("MATCH (a:Account {_workspace_id:$ws})-[:TRANSFER]-(b:Account {_workspace_id:$ws}) "
                "WHERE a.acct_no < b.acct_no "
                "MATCH (pa {_workspace_id:$ws})-[:OWN]->(a), (pb {_workspace_id:$ws})-[:OWN]->(b) "
                "WHERE pa<>pb AND (pa)-[:GUARANTEE]-(pb) "
                "MATCH (m:Medium {_workspace_id:$ws})-[:SIGN_IN]->(a), (m)-[:SIGN_IN]->(b) "
                "RETURN DISTINCT a.acct_no AS a1, b.acct_no AS a2 ORDER BY a1,a2 LIMIT 5"),
    },
    {
        # The same question with the one ambiguity removed. `int_hard_1` says "guarantee one
        # another", which reads as either reciprocal or mutual, and the two arms that were told
        # GUARANTEE runs guarantor->guaranteed committed to the mutual reading and returned
        # nothing — correct for the query they wrote, wrong for what was meant. The graph holds
        # 40,001 GUARANTEE edges and not one reciprocal pair, so the mutual reading is empty by
        # construction. This variant says "in either direction". Both are kept: a schema that
        # makes direction legible does not remove ambiguity from a question, it exposes it, and
        # the pair is the evidence for that.
        "id": "int_hard_1b", "audience": "internal", "difficulty": "hard",
        "ko": "서로 돈이 오가고, 소유자 중 한 쪽이 다른 쪽에 보증을 섰고, 같은 기기로 로그인한 계좌 쌍은?",
        "question": ("Find pairs of accounts that satisfy all three of these at once: money has "
                     "moved between them by transfer in either direction, their owners are two "
                     "different parties and one of them guarantees the other in either "
                     "direction, and the same login device has signed in to both. Give the "
                     "account number pairs, smaller number first."),
        "shape": "list", "column": "a1",
        "ref": ("MATCH (a:Account {_workspace_id:$ws})-[:TRANSFER]-(b:Account {_workspace_id:$ws}) "
                "WHERE a.acct_no < b.acct_no "
                "MATCH (pa {_workspace_id:$ws})-[:OWN]->(a), (pb {_workspace_id:$ws})-[:OWN]->(b) "
                "WHERE pa<>pb AND (pa)-[:GUARANTEE]-(pb) "
                "MATCH (m:Medium {_workspace_id:$ws})-[:SIGN_IN]->(a), (m)-[:SIGN_IN]->(b) "
                "RETURN DISTINCT a.acct_no AS a1, b.acct_no AS a2 ORDER BY a1,a2 LIMIT 5"),
    },
    {
        "id": "int_hard_2", "audience": "internal", "difficulty": "hard",
        "ko": "여러 차명계좌에서 한 계좌로 신고기준 아래 금액만 잘게 모으고 있는 사람은 누구인가요?",
        "question": ("Which party owns the largest number of distinct accounts that all send "
                     "money into one single common account, where every one of those transfers "
                     "is below the 10,000,000 reporting threshold? Give the owner id and how "
                     "many of their accounts are involved."),
        "shape": "list", "column": "owner",
        "ref": ("MATCH (o {_workspace_id:$ws})-[:OWN]->(a:Account {_workspace_id:$ws})"
                "-[t:TRANSFER]->(c:Account {_workspace_id:$ws}) WHERE t.amount < 10000000 "
                "WITH o,c,count(DISTINCT a) AS accts WHERE accts>=5 "
                "RETURN o.id AS owner, accts ORDER BY accts DESC, owner LIMIT 3"),
    },
]

ARMS = ["labels", "ontology", "guardrail", "plan"]


# --------------------------------------------------------------------------------------
# Schema text per arm
# --------------------------------------------------------------------------------------
def labels_only_schema(ontology: Any) -> Dict[str, Any]:
    """The schema a plain text2cypher prompt carries: names and endpoint types.

    Everything the ontology adds beyond this — which end of a same-label relationship the
    anchor sits on, how heavy the degree tail is — is exactly what the `ontology` arm restores,
    so this function's omissions are the independent variable.
    """
    nodes = {name: sorted((node.properties or {}).keys())
             for name, node in ontology.nodes.items()}
    rels = {name: f"({rel.source})-[:{name}]->({rel.target})"
            for name, rel in ontology.relationships.items()}
    return {"nodes": nodes, "relationships": rels}


def build_instructions(schema: Dict[str, Any], *, arm: str) -> str:
    parts = [
        "You are a financial-crime analyst answering questions about a financial graph by "
        "writing Cypher and running it with the run_cypher tool.",
        "",
        "Schema:",
        json.dumps(schema, indent=2, default=str),
        "",
        "Rules:",
        "- Every node pattern must carry the workspace scope, written exactly as "
        "{_workspace_id: $workspace_id}. The harness supplies $workspace_id, $limit and "
        "(where the question names an account) $a; you do not need to define them, and you "
        "must not inline their values.",
        "- Refer to an account the question names by binding acct_no to the $a parameter, "
        "not by inlining the number.",
        "- End every query with LIMIT $limit.",
        "- Call the tool as many times as you need, then answer.",
    ]
    if arm == "plan":
        parts.append(
            "- The tool runs EXPLAIN before executing. If the plan scans all nodes or the "
            "planner estimates too many rows, the query is refused and you are given the plan. "
            "Rewrite it to start from an indexed lookup and to filter earlier.")
    parts += [
        "",
        "Finish your reply with a single line of the form:",
        "ANSWER: <json>",
        "where <json> is a JSON object for a question asking for named values, or a JSON array "
        "for a question asking for a list. Put nothing after that line.",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------------------
# Instrumented tool
# --------------------------------------------------------------------------------------
def _operators(plan: Any, acc: Counter) -> None:
    if plan is None:
        return
    name = plan.get("operatorType") if isinstance(plan, dict) else getattr(plan, "operator_type", None)
    if name:
        acc[str(name).split("@")[0]] += 1
    children = plan.get("children") if isinstance(plan, dict) else getattr(plan, "children", None)
    for child in children or []:
        _operators(child, acc)


def _db_hits(plan: Any) -> int:
    if plan is None:
        return 0
    args = plan.get("args", {}) if isinstance(plan, dict) else getattr(plan, "arguments", {}) or {}
    hits = int(args.get("DbHits", 0) or 0)
    children = plan.get("children") if isinstance(plan, dict) else getattr(plan, "children", None)
    for child in children or []:
        hits += _db_hits(child)
    return hits


def _estimated_rows(plan: Any) -> float:
    if plan is None:
        return 0.0
    args = plan.get("args", {}) if isinstance(plan, dict) else getattr(plan, "arguments", {}) or {}
    est = float(args.get("EstimatedRows", 0) or 0)
    children = plan.get("children") if isinstance(plan, dict) else getattr(plan, "children", None)
    for child in children or []:
        est = max(est, _estimated_rows(child))
    return est


def make_instrumented_tool(driver, database: str, *, arm: str, anchor: Optional[int],
                           calls: List[Dict[str, Any]], guardrail_fn=None):
    """One tool, four behaviours, one record per call.

    The parameters the model must not be trusted with — the workspace scope, the row cap, and
    the anchor the question names — are injected here rather than accepted from the model. That
    is not a convenience: an anchor the model inlines as a literal is the defect that turned one
    question into 38 million db hits, and a harness that owns the scope is what a real
    subject-scoped service does.
    """

    @function_tool(
        name_override="run_cypher",
        description_override=(
            "Run one read-only Cypher query against the financial graph and return the rows as "
            "JSON. Use only labels and relationship types from the schema. $workspace_id, "
            "$limit and $a are supplied for you."),
    )
    def run_cypher(cypher: str) -> str:
        record: Dict[str, Any] = {"cypher": cypher, "outcome": "ok", "db_hits": 0,
                                  "rows": 0, "ms": 0.0, "operators": {}, "chars": 0}
        calls.append(record)
        # Both spellings are bound. Neo4j ignores a parameter the query does not mention, and
        # an episode that failed because the harness bound `ws` while the model wrote
        # `workspace_id` would be measuring the harness rather than the agent.
        params = {"workspace_id": WS, "ws": WS, "limit": ROW_CAP}
        if anchor is not None:
            params["a"] = anchor
            params["acct_no"] = anchor

        if guardrail_fn is not None:
            violations = guardrail_fn(cypher, params)
            if violations:
                record["outcome"] = "guardrail_rejected"
                record["violations"] = violations
                msg = ("REJECTED — the query violates the graph schema and was not executed: "
                       + ", ".join(violations)
                       + ". Rewrite it using only the declared labels, relationship types and "
                         "the supplied parameters.")
                record["chars"] = len(msg)
                return msg

        t0 = time.perf_counter()
        with driver.session(database=database) as session:
            budget = TX_TIMEOUT_S
            if arm == "plan":
                accepted = ACCEPT_COST_MARK in cypher
                try:
                    explain = session.run("EXPLAIN " + cypher, **params).consume()
                    ops: Counter = Counter()
                    _operators(explain.plan, ops)
                    record["operators_planned"] = dict(ops)
                    # Kept on the record even though it is not used to decide anything, so the
                    # reason this arm does not gate on it stays checkable rather than asserted.
                    record["estimated_rows"] = _estimated_rows(explain.plan)
                except Neo4jError as exc:
                    record["outcome"] = "syntax_error"
                    record["error"] = exc.code
                    msg = f"ERROR — the query did not compile: {exc.code}: {str(exc)[:220]}"
                    record["chars"] = len(msg)
                    return msg
                if not accepted:
                    probe = session.begin_transaction(timeout=PROBE_TIMEOUT_S)
                    try:
                        probe.run(cypher, **params).consume()
                        probe.commit()
                    except Exception:
                        probe.close()
                        record["outcome"] = "plan_rejected"
                        record["ms"] = (time.perf_counter() - t0) * 1000
                        msg = (f"NOT EXECUTED — the query did not finish within "
                               f"{PROBE_TIMEOUT_S:.0f}s. Its plan is {dict(ops)} and the "
                               f"planner estimated {record['estimated_rows']:,.0f} rows, so the "
                               f"cost is in the expansion rather than the result. Rewrite it to "
                               f"start from an indexed lookup (Account.acct_no, Account.id, "
                               f"Channel.code, and the id of every other label are indexed) and "
                               f"to filter before expanding. If you judge the cost unavoidable "
                               f"for this question, resend the same query with the comment "
                               f"/* {ACCEPT_COST_MARK} */ in it and it will be run in full.")
                        record["chars"] = len(msg)
                        return msg
                    # It finished inside the probe, so the full run below is a cache-warm repeat
                    # and its timing is not comparable to the other arms'. Stage two replays
                    # every arm's settled query identically, which is where latency is measured.
                    record["probe_passed"] = True

            tx = session.begin_transaction(timeout=budget)
            try:
                result = tx.run("PROFILE " + cypher, **params)
                rows = [dict(r) for _, r in zip(range(ROW_CAP), result)]
                summary = result.consume()
                tx.commit()
            except Neo4jError as exc:
                tx.close()
                record["outcome"] = ("timeout" if "Transaction" in (exc.code or "")
                                     and "terminat" in str(exc).lower() else "db_error")
                record["error"] = exc.code
                record["ms"] = (time.perf_counter() - t0) * 1000
                msg = f"ERROR — {exc.code}: {str(exc)[:220]}"
                record["chars"] = len(msg)
                return msg
            except Exception as exc:  # driver-level failure, e.g. a killed transaction
                tx.close()
                record["outcome"] = "timeout"
                record["error"] = type(exc).__name__
                record["ms"] = (time.perf_counter() - t0) * 1000
                msg = f"ERROR — the query was stopped after {TX_TIMEOUT_S:.0f}s: {type(exc).__name__}"
                record["chars"] = len(msg)
                return msg

        ops = Counter()
        _operators(summary.profile, ops)
        record["ms"] = (time.perf_counter() - t0) * 1000
        record["db_hits"] = _db_hits(summary.profile)
        record["rows"] = len(rows)
        record["operators"] = dict(ops)
        payload = json.dumps({"rows": rows, "row_count": len(rows), "row_cap": ROW_CAP},
                             default=str)
        record["chars"] = len(payload)
        return payload

    return run_cypher


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------
_ANSWER_RE = re.compile(r"ANSWER:\s*(.+)\s*$", re.S | re.I)


def parse_answer(text: str) -> Tuple[Optional[Any], str]:
    m = _ANSWER_RE.search(text or "")
    raw = m.group(1).strip() if m else (text or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    # A model that keeps talking after the JSON is common enough that giving up on it would
    # measure formatting compliance rather than retrieval.
    for opener, closer in (("{", "}"), ("[", "]")):
        i = raw.find(opener)
        if i < 0:
            continue
        depth = 0
        for j in range(i, len(raw)):
            if raw[j] == opener:
                depth += 1
            elif raw[j] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[i:j + 1]), "parsed"
                    except ValueError:
                        break
    # `ANSWER: 1412` is a complete answer to "how many", and refusing it would score the
    # model's formatting rather than what it retrieved.
    bare = re.fullmatch(r"[-+]?[\d,]+(?:\.\d+)?", raw)
    if bare:
        try:
            return json.loads(raw.replace(",", "")), "parsed_bare"
        except ValueError:
            pass
    return None, "unparseable"


def _numbers(obj: Any) -> List[float]:
    out: List[float] = []
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(float(obj))
    elif isinstance(obj, str):
        try:
            out.append(float(obj.replace(",", "").replace("₩", "").strip()))
        except ValueError:
            pass
    elif isinstance(obj, dict):
        for v in obj.values():
            out += _numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _numbers(v)
    return out


def _flatten_scalars(obj: Any) -> List[Any]:
    if isinstance(obj, dict):
        out: List[Any] = []
        for v in obj.values():
            out += _flatten_scalars(v)
        return out
    if isinstance(obj, list):
        out = []
        for v in obj:
            out += _flatten_scalars(v)
        return out
    return [obj]


def score(question: Dict[str, Any], gold_rows: List[Dict[str, Any]],
          answer: Any) -> Dict[str, Any]:
    """Correct means the gold values are present, not that the prose is well phrased.

    Scalars match on value with a 0.1% tolerance, which covers a model that rounds a sum but
    not one that computed a different sum. Lists are scored by F1 against the gold set and are
    only correct at F1 = 1.0 — a partial list is a partial answer, and reporting it as correct
    is the exact failure this project measured at zero truncation disclosures out of twenty.
    """
    if answer is None:
        return {"correct": False, "f1": 0.0, "note": "unparseable"}

    if question["shape"] == "scalar":
        gold = gold_rows[0] if gold_rows else {}
        found = _numbers(answer)
        hits = 0
        for key in question["keys"]:
            want = gold.get(key)
            if want is None:
                hits += 1
                continue
            want_f = float(want)
            tol = max(abs(want_f) * 0.001, 0.5)
            if any(abs(f - want_f) <= tol for f in found):
                hits += 1
        n = len(question["keys"])
        return {"correct": hits == n, "f1": hits / n if n else 0.0,
                "gold": gold, "matched_keys": hits}

    col = question["column"]
    gold_set = {str(r[col]) for r in gold_rows if r.get(col) is not None}
    got = {str(v) for v in _flatten_scalars(answer) if v is not None}
    if not gold_set:
        return {"correct": not got, "f1": 1.0 if not got else 0.0, "gold": []}
    tp = len(gold_set & got)
    prec = tp / len(got) if got else 0.0
    rec = tp / len(gold_set)
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    # A model that lists the five gold ids plus a sentence of context should not be marked
    # wrong for the context, so recall carries the verdict and precision is reported beside it.
    return {"correct": rec == 1.0, "f1": round(f1, 4), "recall": round(rec, 4),
            "precision": round(prec, 4), "gold": sorted(gold_set)}


# --------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------
def mara_model(model: str):
    from agents import OpenAIChatCompletionsModel
    from openai import AsyncOpenAI

    key = os.environ["MARA_API_KEY"]
    client = AsyncOpenAI(api_key=key,
                         base_url=os.getenv("MARA_BASE_URL", "https://api.cloud.mara.com/v1"))
    return OpenAIChatCompletionsModel(model=model, openai_client=client)


async def run_episode(*, driver, database: str, sf: int, arm: str, question: Dict[str, Any],
                      anchor: Optional[int], gold_rows: List[Dict[str, Any]],
                      schema: Dict[str, Any], guardrail_fn, model_name: str,
                      repeat: int = 0) -> Dict[str, Any]:
    calls: List[Dict[str, Any]] = []
    tool = make_instrumented_tool(driver, database, arm=arm, anchor=anchor, calls=calls,
                                  guardrail_fn=guardrail_fn if arm in ("guardrail", "plan") else None)
    agent = Agent(
        name=f"analyst_{arm}",
        instructions=build_instructions(schema, arm=arm),
        model=mara_model(model_name),
        model_settings=ModelSettings(temperature=0.0),
        tools=[tool],
    )
    prompt = question["question"].format(a=anchor)
    t0 = time.perf_counter()
    final_text, err = "", None
    usage_in = usage_out = 0
    try:
        result = await Runner.run(agent, prompt, max_turns=MAX_TURNS)
        final_text = str(result.final_output or "")
        usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
        if usage is not None:
            usage_in, usage_out = usage.input_tokens, usage.output_tokens
    except MaxTurnsExceeded:
        err = "max_turns_exceeded"
    except Exception as exc:  # a model-side failure is a real outcome, not a crash
        err = f"{type(exc).__name__}: {str(exc)[:200]}"
    wall_ms = (time.perf_counter() - t0) * 1000

    answer, parse_note = parse_answer(final_text)
    verdict = score(question, gold_rows, answer) if err is None else {
        "correct": False, "f1": 0.0, "note": err}

    ops: Counter = Counter()
    for c in calls:
        ops.update(c.get("operators") or {})
    db_ms = sum(c["ms"] for c in calls)
    # The query the episode actually settled on — the last one that reached the database and
    # returned rows. This is what stage two replays without a model in the loop, because the
    # thing an operator ships is the query an agent design produces, and its p99 is a database
    # property that a handful of LLM episodes cannot estimate.
    executed = [c for c in calls if c["outcome"] == "ok"]
    settled = executed[-1]["cypher"] if executed else None
    return {
        "sf": sf, "database": database, "arm": arm, "question_id": question["id"],
        "repeat": repeat, "settled_cypher": settled,
        "audience": question["audience"], "difficulty": question["difficulty"],
        "anchor": anchor,
        "round_trips": len(calls),
        "db_hits": sum(c["db_hits"] for c in calls),
        "rows_into_context": sum(c["rows"] for c in calls),
        "chars_into_context": sum(c["chars"] for c in calls),
        "db_ms": round(db_ms, 1),
        "model_ms": round(max(wall_ms - db_ms, 0.0), 1),
        "wall_ms": round(wall_ms, 1),
        "input_tokens": usage_in, "output_tokens": usage_out,
        "operators": dict(ops),
        "guardrail_rejections": sum(1 for c in calls if c["outcome"] == "guardrail_rejected"),
        "plan_rejections": sum(1 for c in calls if c["outcome"] == "plan_rejected"),
        "db_errors": sum(1 for c in calls if c["outcome"] in ("db_error", "syntax_error")),
        "timeouts": sum(1 for c in calls if c["outcome"] == "timeout"),
        "violations": [v for c in calls for v in (c.get("violations") or [])],
        "parse": parse_note, "error": err,
        **{f"score_{k}": v for k, v in verdict.items()},
        "calls": [{k: v for k, v in c.items() if k != "cypher"} | {"cypher": c["cypher"][:600]}
                  for c in calls],
        "final_output": final_text[-1200:],
    }


async def main_async(args) -> None:
    questions = ([q for q in QUESTIONS if q["id"] in set(args.only)] if args.only
                 else QUESTIONS)
    if args.only and not questions:
        raise SystemExit(f"no question matches {args.only}")
    ontology_doc = yaml.safe_load(Path(args.ontology).read_text())
    from seocho.ontology import Ontology
    from seocho.query.hybrid_planner import policy_from_ontology, schema_for_prompt
    from seocho.query.workload_compiler import validate_text2cypher_fallback

    ontology = Ontology.from_dict(ontology_doc)
    policy = policy_from_ontology(ontology)
    full_schema = schema_for_prompt(ontology, policy)
    thin_schema = labels_only_schema(ontology)

    def guardrail_fn(cypher: str, params: Dict[str, Any]) -> List[str]:
        # The same rulebook the deterministic path runs, called directly rather than through
        # the SDK's guardrail hook so the four arms differ only in what is enforced, not in
        # which framework enforces it.
        return list(validate_text2cypher_fallback(cypher, params=params, policy=policy))

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    targets = []
    for spec in args.databases:
        db, _, sf = spec.partition(":")
        targets.append((db, int(sf or 1)))

    context: Dict[str, Dict[str, Any]] = {}
    for db, sf in targets:
        with driver.session(database=db) as s:
            p99 = s.run("MATCH (a:Account) RETURN percentileDisc(a._out_degree,0.99) AS p").single()["p"]
            anchor = s.run("MATCH (a:Account) WHERE a._out_degree>=$p RETURN min(a.acct_no) AS a",
                           p=p99).single()["a"]
            gold: Dict[str, List[Dict[str, Any]]] = {}
            for q in questions:
                tx = s.begin_transaction(timeout=args.gold_timeout)
                try:
                    gold[q["id"]] = [dict(r) for r in tx.run(q["ref"], a=anchor, ws=WS)]
                    tx.commit()
                except Exception:
                    tx.close()
                    gold[q["id"]] = []
        context[db] = {"sf": sf, "anchor": anchor, "p99_out_degree": p99, "gold": gold}
        print(f"[gold] {db} sf={sf} anchor={anchor} p99_out={p99} "
              f"empty={[k for k,v in gold.items() if not v]}", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    results: List[Dict[str, Any]] = []

    async def one(db: str, arm: str, q: Dict[str, Any], repeat: int) -> None:
        ctx = context[db]
        async with sem:
            r = await run_episode(
                driver=driver, database=db, sf=ctx["sf"], arm=arm, question=q,
                anchor=ctx["anchor"] if q["audience"] == "external" else None,
                gold_rows=ctx["gold"][q["id"]],
                schema=thin_schema if arm == "labels" else full_schema,
                guardrail_fn=guardrail_fn, model_name=args.model, repeat=repeat)
        results.append(r)
        print(f"  {db:14s} {arm:9s} {q['id']:11s} r{repeat} trips={r['round_trips']} "
              f"hits={r['db_hits']:>10,} ok={r['score_correct']} "
              f"gr={r['guardrail_rejections']} pl={r['plan_rejections']} "
              f"to={r['timeouts']} {r['wall_ms']:.0f}ms", flush=True)

    jobs = [one(db, arm, q, rep) for db, _ in targets for arm in args.arms
            for q in questions for rep in range(args.repeats)]
    print(f"\n[run] {len(jobs)} episodes, concurrency {args.concurrency}\n", flush=True)
    await asyncio.gather(*jobs)
    driver.close()

    out = {
        "schema_version": "seocho.finbench.agent-interaction.v1",
        "model": args.model, "arms": list(args.arms), "row_cap": ROW_CAP,
        "tx_timeout_s": TX_TIMEOUT_S, "probe_timeout_s": PROBE_TIMEOUT_S,
        "max_turns": MAX_TURNS,
        "context": {db: {k: v for k, v in c.items() if k != "gold"} for db, c in context.items()},
        "questions": [{k: q[k] for k in ("id", "audience", "difficulty", "ko", "question", "shape")}
                      for q in questions],
        "repeats": args.repeats,
        "episodes": sorted(results, key=lambda r: (r["sf"], r["arm"], r["question_id"],
                                                   r["repeat"])),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str))
    print(f"\nwrote {args.out}  ({len(results)} episodes)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uri", default="bolt://localhost:7687")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", required=True)
    p.add_argument("--databases", nargs="+", default=["finbenchl1:1", "finbenchl10:10",
                                                      "finbenchl100:100"])
    p.add_argument("--arms", nargs="+", default=ARMS, choices=ARMS)
    p.add_argument("--model", default="gpt-oss-120b")
    p.add_argument("--ontology", default="examples/finbench/finbench.ontology.yaml")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--repeats", type=int, default=3,
                   help="episodes per cell. Temperature is 0, so repeats are here to expose "
                        "how often an agent design lands on a *different* query, not to "
                        "estimate latency — stage two replays the settled query for that.")
    p.add_argument("--gold-timeout", type=float, default=120.0)
    p.add_argument("--only", nargs="*", default=None,
                   help="restrict to these question ids, for re-running one cell without "
                        "disturbing the conditions the rest of the run shared")
    p.add_argument("--out", default="outputs/finbench/agent_interaction.json")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
