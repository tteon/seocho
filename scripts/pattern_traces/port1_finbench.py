"""PORT-1 scale-up: FinBench planted-gold A/B cases across model families.

The pilot (``ab_reasoning.py``, seocho-vdw.9) established sign consistency on
one hand-written case; its recorded limit was "search saturates at this size —
needs harder cases". This harness generates the harder cases from the FinBench
generator's planted gold (``outputs/finbench/sf1/gold.json`` + the 40 planted
transfer edges), so every answer is deterministic by construction and the case
bank scales with the generator, not with hand-writing.

A/B contract (unchanged from the pilot): both arms receive the SAME facts —
all planted transfers, timestamps and channel codes verbatim. The ``baseline``
arm sees them as a chronological transaction log in prose; the ``seocho`` arm
sees the ontology schema plus ontology-aligned graph triples. The arms differ
in form, not in what is knowable, so turns-to-correct measures representation.

Indexing cases use small per-typology packs (extraction gold must be bounded);
search cases use the full 40-edge pack, which is what makes them non-saturated:
the model must isolate one typology's edges from four interleaved ones.

Usage:
  MARA_API_KEY=... python scripts/pattern_traces/port1_finbench.py \
      --model MiniMax-M2.7 --repeats 3 \
      --gold outputs/finbench/sf1/gold.json \
      --edges outputs/finbench/sf1/edges/transfer.parquet \
      --out outputs/pattern_traces/port1_MiniMax-M2.7.jsonl
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

_spec = importlib.util.spec_from_file_location(
    "pattern_trace_schema", Path(__file__).resolve().parent / "schema.py")
schema = importlib.util.module_from_spec(_spec)
sys.modules["pattern_trace_schema"] = schema
_spec.loader.exec_module(schema)

_ONTOLOGY_SCHEMA = (
    "Labels: Account(acct_no INTEGER UNIQUE)\n"
    "Relationships: TRANSFER(Account->Account, {amount, channel, ts})\n"
    "Channels: WIRE_CROSSBORDER, VIRTUAL_ASSET, MVTS_HAWALA, ATM_CD, "
    "BRANCH_CASH, PREPAID_GIFT"
)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def load_edges(parquet_path: Path) -> List[Tuple[int, int, int, int, str]]:
    import duckdb

    rows = duckdb.connect().execute(
        f"select src, dst, amount, ts, channel from '{parquet_path}' "
        "where src >= 9000000 or dst >= 9000000 order by ts, src").fetchall()
    return [(int(s), int(d), int(a), int(t), str(c)) for s, d, a, t, c in rows]


def render_baseline(edges) -> str:
    lines = [
        f"On {_iso(ts)}, account {src} sent {amount} USD to account {dst} "
        f"via channel {channel}."
        for src, dst, amount, ts, channel in edges]
    return "Transaction log:\n" + "\n".join(lines)


def render_seocho(edges) -> str:
    lines = [
        f"Account({src}) -TRANSFER {{amount: {amount}, channel: {channel}, "
        f"ts: {_iso(ts)}}}-> Account({dst})"
        for src, dst, amount, ts, channel in edges]
    return _ONTOLOGY_SCHEMA + "\n\nGraph context (ontology-aligned):\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Search cases derived from gold.json + the planted edges (all deterministic)
# ---------------------------------------------------------------------------

def build_search_cases(gold: Dict[str, Any], edges) -> List[Dict[str, Any]]:
    typ = gold["typologies"]
    ring3, ring5 = typ["layering_cycle"]["rings"]
    funnel = typ["funnel_account"]["funnel"]
    onward = typ["funnel_account"]["onward_beneficiary"]
    conduit = typ["rapid_passthrough"]["conduit"]
    chain = typ["high_risk_channel_chain"]

    into_funnel = [e for e in edges if e[1] == funnel]
    funnel_total = sum(e[2] for e in into_funnel)
    max_single = max(e[2] for e in into_funnel)
    ring3_close = next(e for e in edges if e[0] == ring3[-1] and e[1] == ring3[0])
    conduit_in = next(e for e in edges if e[1] == conduit)
    conduit_out = next(e for e in edges if e[0] == conduit)
    retained = conduit_in[2] - conduit_out[2]
    hold_hours = (conduit_out[3] - conduit_in[3]) // 3600
    hop2_edge = next(e for e in edges
                     if e[0] == chain["hop_days"]["1"][0] and e[1] == chain["hop_days"]["2"][0])
    ring_wire_total = sum(e[2] for e in edges
                          if e[4] == "WIRE_CROSSBORDER"
                          and e[0] in set(ring3 + ring5) and e[1] in set(ring3 + ring5))

    def num(n: int) -> List[str]:
        return [f"{n:,}", str(n)]

    return [
        {"id": "ring3_members",
         "question": "One group of accounts moves money in a closed 3-account "
                     "cycle (A->B->C->A). List the three account numbers.",
         "gold_all": [str(a) for a in ring3]},
        {"id": "ring3_close_channel",
         "question": f"Which channel is used on the transfer that returns funds "
                     f"to account {ring3[0]}, closing that 3-account cycle?",
         "gold_all": [ring3_close[4]]},
        {"id": "ring5_members",
         "question": "A second closed cycle involves five accounts. List all "
                     "five account numbers.",
         "gold_all": [str(a) for a in ring5]},
        {"id": "funnel_deposit_count",
         "question": f"How many separate incoming transfers does account "
                     f"{funnel} receive? Answer with the number.",
         "gold_any": True, "gold_all": num(len(into_funnel))},
        {"id": "funnel_total_in",
         "question": f"Summing every incoming transfer to account {funnel}, "
                     f"what total amount (USD) flows in? Answer with the number.",
         "gold_any": True, "gold_all": num(funnel_total)},
        {"id": "funnel_max_single",
         "question": f"What is the largest single incoming transfer to account "
                     f"{funnel}? Answer with the number.",
         "gold_any": True, "gold_all": num(max_single)},
        {"id": "funnel_onward",
         "question": f"After collecting the incoming transfers, account {funnel} "
                     f"moves the funds onward. To which account, and via which "
                     f"channel?",
         "gold_all": [str(onward), "WIRE_CROSSBORDER"]},
        {"id": "passthrough_retained",
         "question": f"Account {conduit} receives funds and passes almost all of "
                     f"them on. How much (USD) does it retain? Answer with the "
                     f"number.",
         "gold_any": True, "gold_all": num(retained)},
        {"id": "passthrough_hold",
         "question": f"How many hours does account {conduit} hold the funds "
                     f"between receiving and sending them on? Answer in the "
                     f"form 'N hours'.",
         "gold_any": True, "gold_all": [f"{hold_hours} hours", f"{hold_hours}.0 hours"]},
        {"id": "chain_hop3",
         "question": f"Following outgoing transfers starting from account "
                     f"{chain['origin']}, which account is exactly three hops "
                     f"away?",
         "gold_all": [str(chain["hop_days"]["3"][0])]},
        {"id": "chain_hop2_channel",
         "question": f"On the path out of account {chain['origin']}, which "
                     f"channel carries the second hop (from "
                     f"{chain['hop_days']['1'][0]} onward)?",
         "gold_all": [hop2_edge[4]]},
        {"id": "rings_wire_total",
         "question": "Considering only transfers between accounts belonging to "
                     "the two closed cycles, what total amount moves via "
                     "channel WIRE_CROSSBORDER? Answer with the number.",
         "gold_any": True, "gold_all": num(ring_wire_total)},
    ]


# ---------------------------------------------------------------------------
# Indexing cases: small per-typology packs, gold = exact entity/rel sets
# ---------------------------------------------------------------------------

def build_indexing_cases(gold: Dict[str, Any], edges) -> List[Dict[str, Any]]:
    typ = gold["typologies"]
    _, ring5 = typ["layering_cycle"]["rings"]
    conduit = typ["rapid_passthrough"]["conduit"]
    chain = typ["high_risk_channel_chain"]
    chain_accounts = {chain["origin"]}
    for hop in chain["hop_days"].values():
        chain_accounts.update(hop)

    def pack(accounts) -> List:
        acc = set(accounts)
        return [e for e in edges if e[0] in acc and e[1] in acc]

    cases = []
    for cid, accounts in (
            ("idx_ring5", ring5),
            ("idx_passthrough", [typ["rapid_passthrough"]["origin"], conduit,
                                 typ["rapid_passthrough"]["destination"]]),
            ("idx_chain", sorted(chain_accounts))):
        sub = pack(accounts)
        cases.append({
            "id": cid,
            "edges": sub,
            "gold_entities": {str(a) for a in accounts},
            "gold_rels": {(str(s), "TRANSFER", str(d)) for s, d, *_ in sub},
        })
    return cases


def _norm(text: str) -> str:
    return text.lower().replace(",", "")


def _hit(token: str, hay: str) -> bool:
    """Digit-boundary match for numeric golds: '50000' must not match inside
    '49950000'. Non-numeric golds use plain substring."""
    tok = _norm(token)
    if tok.replace(".", "").isdigit():
        import re
        return re.search(rf"(?<![\d.]){re.escape(tok)}(?![\d.])", hay) is not None
    return tok in hay


def check_search(case: Dict[str, Any], answer: str) -> Tuple[bool, str]:
    """Return (ok, feedback). Feedback names the SLOT TYPE that is missing,
    never the gold value — leaking the gold tokens (the previous behavior)
    turned every turn >= 2 into a copy test and contaminated turns-to-correct
    (ML review 2026-08-15). ``slot`` defaults to a generic noun."""
    hay = _norm(answer)
    tokens = case["gold_all"]
    slot = case.get("slot", "a required value")
    if case.get("gold_any"):
        ok = any(_hit(t, hay) for t in tokens)
        return ok, ("" if ok else f"Your answer is missing {slot}.")
    missing_n = sum(1 for t in tokens if not _hit(t, hay))
    return missing_n == 0, ("" if missing_n == 0
                            else f"Your answer is missing {slot} "
                                 f"({missing_n} of {len(tokens)} required item(s)).")


def check_indexing(case: Dict[str, Any], answer: str) -> Tuple[bool, str]:
    try:
        payload = json.loads(answer[answer.index("{"): answer.rindex("}") + 1])
    except Exception:
        return False, "reply was not parseable JSON with nodes/relationships"
    names = set()
    for node in payload.get("nodes", []):
        for value in (node.get("properties") or {}).values():
            names.add(str(value))
    rels = {(str(r.get("source")), str(r.get("type")), str(r.get("target")))
            for r in payload.get("relationships", [])}
    missing_entities = case["gold_entities"] - names
    missing_rels = case["gold_rels"] - rels
    if not missing_entities and not missing_rels:
        return True, ""
    # Count, not contents — naming the missing triples would leak the gold.
    return False, (
        f"Your graph is missing {len(missing_entities)} required entity(ies) "
        f"and {len(missing_rels)} required relationship(s); check that node "
        f"identifiers and relationship types follow the target schema.")


def build_prompt(pattern: str, arm: str, case: Dict[str, Any],
                 edges) -> Tuple[str, str]:
    if pattern == "search":
        system = ("Answer the question from the provided material only. "
                  "Be precise; name identifiers and channel codes exactly.")
        material = render_seocho(edges) if arm == "seocho" else render_baseline(edges)
        return system, f"{material}\n\nQuestion: {case['question']}"
    # indexing: material is the small pack rendered as prose for BOTH arms;
    # the arms differ in whether the target schema (ontology) is given.
    material = render_baseline(case["edges"])
    if arm == "seocho":
        system = ("Extract a graph strictly following this ontology. Return one "
                  "JSON object {\"nodes\": [{label, properties}], \"relationships\": "
                  "[{type, source, target, properties}]} where source/target are "
                  "the identifying property values.\n" + _ONTOLOGY_SCHEMA)
    else:
        system = ("Extract entities and relationships from the text. Return one "
                  "JSON object {\"nodes\": [{label, properties}], \"relationships\": "
                  "[{type, source, target, properties}]}. Choose labels, types "
                  "and identifiers yourself.")
    return system, material


def run_case(backend, *, pattern: str, arm: str, case: Dict[str, Any], edges,
             model: str, max_turns: int, repeat: int) -> schema.Episode:
    system, user = build_prompt(pattern, arm, case, edges)
    episode = schema.Episode(
        pattern=pattern, case_id=f"{case['id']}:{arm}:r{repeat}",
        model=model, provider="mara")
    episode.ontology = "finbench_aml" if arm == "seocho" else ""
    feedback_log: List[str] = []
    turns_to_correct = None

    for turn in range(1, max_turns + 1):
        turn_user = user
        if feedback_log:
            turn_user += ("\n\nYour previous answer was not accepted. "
                          + feedback_log[-1] + "\nAnswer again, corrected.")
        started = time.perf_counter()
        error = None
        try:
            response = backend.complete(system=system, user=turn_user,
                                        temperature=0.0, task_hint=pattern,
                                        mode="pipeline")
            text = response.text or ""
        except Exception as exc:
            response, text, error = None, "", f"{type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - started) * 1000.0

        if pattern == "search":
            ok, feedback = check_search(case, text)
            parse = "not_applicable"
        else:
            ok, feedback = check_indexing(case, text)
            parse = "ok" if ok or "parseable" not in feedback else "failed"

        episode.steps.append(schema.LLMStep(
            role=f"turn_{turn}", model=model, latency_ms=round(latency_ms, 1),
            usage=dict(getattr(response, "usage", {}) or {}),
            text_chars=len(text), parse=parse,
            prompt_sections={"system": len(system), "user": len(turn_user)},
            error=error))
        if ok:
            turns_to_correct = turn
            break
        feedback_log.append(feedback)

    episode.outcome = {
        "arm": arm,
        # first_turn_correct is the leak-free metric: it never depends on
        # feedback, so it is valid even against the pre-fix checker. Report it
        # separately from turns_to_correct (ML review 2026-08-15).
        "first_turn_correct": turns_to_correct == 1,
        "turns_to_correct": turns_to_correct,
        "turns_used": len(episode.steps),
        "total_completion_tokens": sum(
            int(step.usage.get("completion_tokens", 0) or 0)
            for step in episode.steps),
    }
    return episode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="mara")
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--patterns", default="search,indexing")
    parser.add_argument("--gold", default="outputs/finbench/sf1/gold.json")
    parser.add_argument("--edges", default="outputs/finbench/sf1/edges/transfer.parquet")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from seocho.store.llm import create_llm_backend

    gold = json.loads(Path(args.gold).read_text())
    edges = load_edges(Path(args.edges))
    backend = create_llm_backend(provider=args.provider, model=args.model)
    out = Path(args.out)

    summary: Dict[str, Dict[str, List[Any]]] = {}
    for pattern in [p.strip() for p in args.patterns.split(",")]:
        cases = (build_search_cases(gold, edges) if pattern == "search"
                 else build_indexing_cases(gold, edges))
        for case in cases:
            for arm in ("baseline", "seocho"):
                for repeat in range(args.repeats):
                    episode = run_case(backend, pattern=pattern, arm=arm,
                                       case=case, edges=edges, model=args.model,
                                       max_turns=args.max_turns, repeat=repeat)
                    schema.append_episode(out, episode)
                    o = episode.outcome
                    key = f"{pattern}/{arm}"
                    summary.setdefault(key, {"turns": [], "tokens": [], "first": []})
                    summary[key]["turns"].append(o["turns_to_correct"])
                    summary[key]["tokens"].append(o["total_completion_tokens"])
                    summary[key]["first"].append(o["first_turn_correct"])
                    print(f"{args.model:16s} {pattern:9s} {case['id']:22s} "
                          f"{arm:8s} r{repeat} turns={o['turns_to_correct']} "
                          f"tokens={o['total_completion_tokens']}", flush=True)

    print(f"\n=== {args.model} summary "
          f"(first_turn_correct = leak-free metric; turns_to_correct secondary) ===")
    for key, values in sorted(summary.items()):
        turns = values["turns"]
        solved = [t for t in turns if t is not None]
        avg = f"avg_turns={sum(solved) / len(solved):.2f}" if solved else "avg_turns=n/a"
        tokens = values["tokens"]
        first = values["first"]
        print(f"{key:18s} first_turn {sum(first)}/{len(first)} "
              f"({sum(first) / len(first):.0%}) | solved {len(solved)}/{len(turns)} "
              f"{avg} avg_tokens={sum(tokens) / len(tokens):.0f}")


if __name__ == "__main__":
    main()
