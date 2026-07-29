#!/usr/bin/env python3
"""Materialize the golden-record master database (``mdmmaster``) — $0.

Consolidation-hub-with-registry-lineage, per the survivorship ruleset:

- one ``(:GoldenEntity)`` per WCC cluster (canonical name: most tokens →
  longest → lexicographic), plus singletons (found-by-one-model entities)
- ``(:SourceRef)`` per contributing department node, linked by
  ``[:DERIVED_FROM {match_method, match_score, source_business_key, …}]`` —
  the XREF; pairwise ``[:SAME_AS {method, score}]`` audit edges record WHY
  records merged
- ``(:GoldenFact)`` per (entity, metric, period, basis) where the majority
  vote produced a survivor; **quarantines become ``(:StewardTask)`` nodes
  with NO golden value** — disagreement escalates, never guesses (§20.2)
- every golden node is stamped with ``rule_set_version`` + ``rule_set_sha256``
  and a deterministic ``golden_id``: same sources + same rules ⇒ byte-identical
  master (§20.7 — rebuild, don't patch)

Inputs: staging_artifact.json + resolve_artifact.json (steps 03/04).
Output:  mdmmaster graph + master_artifact.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
sys.path.insert(0, str(MDM_ROOT))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

from dotenv import dotenv_values  # noqa: E402

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ.setdefault(k, v)

from lib.normalize import norm_key, parse_value, values_agree  # noqa: E402
from lib.survivorship import (  # noqa: E402
    SourceFact, golden_id, load_ruleset, pick_canonical_name, survive_numeric,
)

MASTER_DB = "mdmmaster"
MASTER_WS = "mdm-master-v1"

_PERIOD_RE = re.compile(r"(?:fy|fiscal\s*(?:year)?)?\s*((?:19|20)\d{2})", re.IGNORECASE)


def norm_period(period: object) -> str:
    text = str(period or "").strip().lower()
    if not text:
        return ""
    m = _PERIOD_RE.search(text)
    return f"fy{m.group(1)}" if m else text


def split_metric_name(metric: object, period: object) -> tuple[str, str]:
    """Some models embed the period in the metric NAME ("CostOfSales_FY2023").

    Returns (metric_base_norm, period_norm) — the embedded period is used only
    when the `period` property itself is empty.
    """
    raw = str(metric or "")
    p = norm_period(period)
    m = _PERIOD_RE.search(raw)
    base = raw
    if m:
        base = (raw[: m.start()] + raw[m.end():])
        if not p:
            p = f"fy{m.group(1)}"
    return norm_key(base), p


def collapse_intra_source(facts: list[dict], rel_tol: float) -> tuple[list[SourceFact], list[dict]]:
    """One vote per source. A source whose own values conflict abstains
    (recorded as intra_source_conflict), it does not get two votes."""
    by_source: dict[str, list[dict]] = defaultdict(list)
    for f in facts:
        by_source[f["source"]].append(f)
    votes: list[SourceFact] = []
    conflicts: list[dict] = []
    for source, fs in sorted(by_source.items()):
        parsed = [(f, parse_value(f["value"])) for f in fs]
        values = [p for _, p in parsed if p is not None]
        if len(values) > 1 and not all(
                values_agree(values[0], v, rel_tol=rel_tol) for v in values[1:]):
            conflicts.append({"source": source, "note": "intra_source_conflict",
                              "values": [str(f["value"]) for f in fs]})
            continue
        # Agreeing duplicates: keep the least-rounded raw as the source's vote.
        best = max(fs, key=lambda f: ((parse_value(f["value"]) or
                                       type("z", (), {"sig_digits": 0})).sig_digits,
                                      str(f["value"])))
        votes.append(SourceFact(source=source, raw=str(best["value"])))
    return votes, conflicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-prefix", default="seocho-capital-v1")
    args = ap.parse_args()

    ruleset = load_ruleset()
    out_dir = ROOT / "outputs" / "evaluation" / "mdm_demo" / args.run_prefix
    with (out_dir / "staging_artifact.json").open("r", encoding="utf-8") as f:
        staging = json.load(f)
    with (out_dir / "resolve_artifact.json").open("r", encoding="utf-8") as f:
        resolve = json.load(f)
    panel_size = len(staging["dept_node_counts"])

    # --- 1. golden entities from WCC clusters -------------------------------
    clusters = list(resolve["clusters"].values())
    pair_method = {}
    proxies = staging["proxies"]
    for pr in staging["candidate_pairs"]:
        a, b = proxies[pr["i"]], proxies[pr["j"]]
        key = tuple(sorted([f"{a['src_db']}:{a['src_eid']}", f"{b['src_db']}:{b['src_eid']}"]))
        pair_method[key] = {"method": pr["method"], "score": pr["score"]}

    goldens: list[dict] = []
    member_to_golden: dict[str, str] = {}   # "src_db:src_eid" -> golden_id
    for members in clusters:
        source_keys = sorted(f"{m['src_db']}:{m['business_key']}" for m in members)
        gid = golden_id(ruleset.version, source_keys)
        name = pick_canonical_name([m["name"] for m in members])
        models = sorted({m["model"] for m in members})
        same_as = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                key = tuple(sorted([
                    f"{members[i]['src_db']}:{members[i]['src_eid']}",
                    f"{members[j]['src_db']}:{members[j]['src_eid']}"]))
                ev = pair_method.get(key)
                if ev:
                    same_as.append({"a": key[0], "b": key[1], **ev})
        goldens.append({
            "golden_id": gid, "name": name, "models": models,
            "model_count": len(models), "members": members, "same_as": same_as,
            "aliases": sorted({m["name"] for m in members}),
        })
        for m in members:
            member_to_golden[f"{m['src_db']}:{m['src_eid']}"] = gid

    # --- 2. attribute survivorship over metric facts -------------------------
    # Company attribution rides on the CASE (one workspace = one filing): the
    # case "anchor" is the golden entity seen by the most models within that
    # case's workspaces (the 10-K filer, in practice). Extractors frequently
    # leave metric nodes unconnected to the filer node, so joining on graph
    # edges alone would drop most facts from two of the three models.
    case_anchor: dict[str, str] = {}
    case_golden_models: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for g in goldens:
        for m in g["members"]:
            case_golden_models[m["case_id"]][g["golden_id"]].add(m["model"])
    golden_by_id = {g["golden_id"]: g for g in goldens}

    def _has_legal_entity(gid: str) -> bool:
        # FIBO's `be` module makes LegalEntity the anchor class (§19): a
        # cluster any model typed as LegalEntity is the filer, not a stray
        # header token that happened to be extracted by more models.
        return any("LegalEntity" in m["labels"] for m in golden_by_id[gid]["members"])

    for case_id, by_gid in case_golden_models.items():
        case_anchor[case_id] = max(
            by_gid,
            key=lambda gid: (_has_legal_entity(gid),
                             len(by_gid[gid]),
                             len(golden_by_id[gid]["members"]),
                             golden_by_id[gid]["name"]))

    fact_groups: dict[tuple, list[dict]] = defaultdict(list)
    metrics_total = 0
    metrics_out_of_scope = 0
    for met in staging["metrics"]:
        metrics_total += 1
        gid = case_anchor.get(met["case_id"])
        if gid is None:
            metrics_out_of_scope += 1   # case produced no in-scope entity
            continue
        metric_base, period = split_metric_name(met["metric"], met["period"])
        key = (gid, metric_base, period,
               str(met["basis"] or "").strip().lower())
        fact_groups[key].append({
            "source": f"{met['dept']}/{met['model']}",
            "value": met["value"], "metric_raw": met["metric"],
            "period_raw": met["period"], "src_db": met["src_db"],
            "metric_eid": met["metric_eid"], "case_id": met["case_id"],
        })

    golden_facts: list[dict] = []
    steward_tasks: list[dict] = []
    for (gid, metric, period, basis), facts in sorted(fact_groups.items()):
        votes, intra = collapse_intra_source(facts, ruleset.rel_tol)
        out = survive_numeric(votes, panel_size=panel_size, ruleset=ruleset)
        rec = {
            "golden_id": gid, "metric": metric, "period": period, "basis": basis,
            "metric_raw": facts[0]["metric_raw"],
            "status": out.status, "rule": out.rule,
            "value": out.value, "value_raw": out.value_raw, "source": out.source,
            "agreement_count": out.agreement_count,
            "sources_reporting": out.sources_reporting,
            "confidence": out.confidence,
            "dissents": out.dissents + intra,
            "contributing": [{"source": f["source"], "value": str(f["value"]),
                              "src_db": f["src_db"], "metric_eid": f["metric_eid"]}
                             for f in facts],
        }
        if out.status == "golden":
            golden_facts.append(rec)
        elif out.status == "quarantine":
            steward_tasks.append({**rec, "reason": out.rule,
                                  "task_id": golden_id(ruleset.version,
                                                       [gid, metric, period, basis])})

    print(f"== {len(goldens)} golden entities "
          f"({sum(1 for g in goldens if g['model_count'] >= 2)} multi-model) ==")
    print(f"== facts: {len(golden_facts)} golden, {len(steward_tasks)} quarantined "
          f"(of {len(fact_groups)} groups; {metrics_out_of_scope}/{metrics_total} "
          f"metric rows outside resolution scope) ==")

    # --- 3. write mdmmaster (idempotent rebuild) ------------------------------
    from seocho.store.graph import Neo4jGraphStore
    from extraction.config import db_registry
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"],
                         os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    try:
        db_registry.register(MASTER_DB)
        gs.ensure_database(MASTER_DB, wait_online=True)
        gs.query("MATCH (n {_workspace_id:$ws}) DETACH DELETE n",
                 params={"ws": MASTER_WS}, database=MASTER_DB)

        gs.query(
            "UNWIND $rows AS r "
            "CREATE (g:GoldenEntity {_workspace_id:$ws}) "
            "SET g.golden_id = r.golden_id, g.name = r.name, "
            "    g.models = r.models, g.model_count = r.model_count, "
            "    g.aliases = r.aliases, g.rule_set_version = $rv, "
            "    g.rule_set_sha256 = $rsha "
            "WITH g, r UNWIND r.members AS m "
            "MERGE (s:SourceRef {src_db: m.src_db, src_eid: m.src_eid, "
            "                    _workspace_id:$ws}) "
            "SET s.name = m.name, s.model = m.model, s.dept = m.dept, "
            "    s.business_key = m.business_key, "
            "    s.src_instance = coalesce(m.src_instance, '') "
            "CREATE (g)-[:DERIVED_FROM {source_business_key: m.business_key}]->(s)",
            params={"rows": goldens, "ws": MASTER_WS,
                    "rv": ruleset.version, "rsha": ruleset.sha256},
            database=MASTER_DB)

        same_as_rows = [sa for g in goldens for sa in g["same_as"]]
        gs.query(
            "UNWIND $rows AS r "
            "MATCH (a:SourceRef {_workspace_id:$ws}) "
            "  WHERE a.src_db + ':' + a.src_eid = r.a "
            "MATCH (b:SourceRef {_workspace_id:$ws}) "
            "  WHERE b.src_db + ':' + b.src_eid = r.b "
            "CREATE (a)-[:SAME_AS {method: r.method, score: r.score, "
            "                      rule_set_version: $rv}]->(b)",
            params={"rows": same_as_rows, "ws": MASTER_WS, "rv": ruleset.version},
            database=MASTER_DB)

        gs.query(
            "UNWIND $rows AS r "
            "MATCH (g:GoldenEntity {golden_id: r.golden_id, _workspace_id:$ws}) "
            "CREATE (f:GoldenFact {_workspace_id:$ws}) "
            "SET f.metric = r.metric, f.metric_raw = r.metric_raw, "
            "    f.period = r.period, f.basis = r.basis, "
            "    f.value = r.value, f.value_raw = r.value_raw, "
            "    f.survivor_source = r.source, f.rule = r.rule, "
            "    f.agreement_count = r.agreement_count, "
            "    f.sources_reporting = r.sources_reporting, "
            "    f.confidence = r.confidence, "
            "    f.dissents = [d IN r.dissents | d.source + ': ' + coalesce(d.raw, d.note, '')], "
            "    f.rule_set_version = $rv "
            "CREATE (g)-[:HAS_FACT]->(f)",
            params={"rows": golden_facts, "ws": MASTER_WS, "rv": ruleset.version},
            database=MASTER_DB)

        gs.query(
            "UNWIND $rows AS r "
            "MATCH (g:GoldenEntity {golden_id: r.golden_id, _workspace_id:$ws}) "
            "SET g.needs_review = true "
            "CREATE (t:StewardTask {_workspace_id:$ws}) "
            "SET t.task_id = r.task_id, t.status = 'open', t.reason = r.reason, "
            "    t.metric = r.metric, t.period = r.period, t.basis = r.basis, "
            "    t.candidate_values = [c IN r.contributing | c.source + ': ' + c.value], "
            "    t.rule_set_version = $rv "
            "CREATE (t)-[:REVIEWS]->(g)",
            params={"rows": steward_tasks, "ws": MASTER_WS, "rv": ruleset.version},
            database=MASTER_DB)

        check = gs.query(
            "MATCH (g:GoldenEntity {_workspace_id:$ws}) "
            "OPTIONAL MATCH (g)-[:HAS_FACT]->(f) "
            "OPTIONAL MATCH (t:StewardTask {_workspace_id:$ws}) "
            "RETURN count(DISTINCT g) AS g, count(DISTINCT f) AS f, "
            "       count(DISTINCT t) AS t",
            params={"ws": MASTER_WS}, database=MASTER_DB)[0]
        print(f"== {MASTER_DB}: {check['g']} GoldenEntity, {check['f']} GoldenFact, "
              f"{check['t']} StewardTask ==")
    finally:
        gs.close()

    artifact = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ruleset_version": ruleset.version, "ruleset_sha256": ruleset.sha256,
        "panel_size": panel_size,
        "case_anchor": case_anchor,
        "golden_entities": goldens,
        "golden_facts": golden_facts,
        "steward_tasks": steward_tasks,
        "metrics_total": metrics_total,
        "metrics_out_of_scope": metrics_out_of_scope,
    }
    path = out_dir / "master_artifact.json"
    path.write_text(json.dumps(artifact, indent=1, default=str), encoding="utf-8")
    print(f"== wrote {path.relative_to(ROOT)} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
