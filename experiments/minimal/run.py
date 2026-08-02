#!/usr/bin/env python3
"""One case, end to end, with every stage traced.

    python3 experiments/minimal/run.py
    python3 experiments/minimal/run.py case_limit=10
    python3 experiments/minimal/run.py decisive.agent.offline=false
    python3 experiments/minimal/run.py decisive.ontology.modules='[be,ind]'

Stages, in order, each one a span and a line in trace.jsonl:

    discover  which source cases exist in the first view
    retrieve  pull facts for one case from every view
    compare   cross-view key comparability and value agreement
    govern    which entities carry an ontology-declared type
    serve     what a supervisor is allowed to receive
    answer    the model call, skipped entirely when offline

Default is offline, so a full run touches no paid API.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from omegaconf import DictConfig, OmegaConf

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]

import agent as agent_mod  # noqa: E402
import kg as kg_mod  # noqa: E402
import ontology as onto_mod  # noqa: E402
import verify as verify_mod  # noqa: E402
from observe import Run  # noqa: E402

DISCOVER = """
MATCH (n) WHERE n.workspace_id IS NOT NULL
RETURN DISTINCT n.workspace_id AS workspace
ORDER BY workspace
LIMIT $limit
"""


def load_config(argv: list[str]) -> DictConfig:
    """Base config plus dotlist overrides.

    Hydra 1.3 cannot import under Python 3.14, and its composition is more
    machinery than this harness needs. OmegaConf gives the same override syntax
    with nothing hidden:  run.py case_limit=5 decisive.agent.offline=false
    """
    base = OmegaConf.load(Path(__file__).resolve().parent / "conf" / "config.yaml")
    for key in ("defaults", "hydra"):
        if key in base:
            del base[key]
    if argv:
        base = OmegaConf.merge(base, OmegaConf.from_dotlist(argv))
    return base


def main(argv: list[str] | None = None) -> int:
    cfg = load_config(list(argv if argv is not None else sys.argv[1:]))
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    resolved = OmegaConf.to_container(cfg, resolve=True)
    run = Run(root=ROOT / cfg.runtime.output_root, name="minimal",
              config=resolved, console_spans=bool(cfg.runtime.console_spans))

    graph = kg_mod.KnowledgeGraph(uri=cfg.runtime.uri,
                                  views=dict(cfg.decisive.views), run=run)
    try:
        with run.stage("ontology",
                       name=cfg.decisive.ontology.name,
                       modules=list(cfg.decisive.ontology.modules)) as out:
            onto = onto_mod.load(
                name=cfg.decisive.ontology.name,
                modules=list(cfg.decisive.ontology.modules),
                module_dir=ROOT / cfg.runtime.module_dir,
                cache_dir=run.dir)
            out["declared_classes"] = sorted(onto.classes)
            out["relations"] = len(onto.relations)
            out["jsonld"] = f"{onto.name}.jsonld"

        cases = list(cfg.cases)
        if not cases:
            with run.stage("discover", view=next(iter(graph.views))) as out:
                first = next(iter(graph.views))
                rows = graph._query(first, DISCOVER, limit=int(cfg.case_limit) * 40)
                seen, picked = set(), []
                for row in rows:
                    case = str(row["workspace"]).rsplit("-", 1)[-1]
                    if case not in seen:
                        seen.add(case)
                        picked.append(case)
                    if len(picked) >= int(cfg.case_limit):
                        break
                cases = picked
                out["cases"] = cases

        per_case = []
        for case in cases:
            with run.stage("retrieve", case=case, views=list(graph.views)) as out:
                facts = {v: graph.facts(v, case) for v in graph.views}
                out["facts_per_view"] = {v: len(f) for v, f in facts.items()}

            with run.stage("compare", case=case,
                           key_rule=str(cfg.decisive.key_rule)) as out:
                report = verify_mod.compare(
                    views=facts,
                    key_of=lambda f: f.key,
                    value_of=lambda f: f.raw)
                out.update({k: v for k, v in report.items() if k != "conflicts"})
                out["conflict_examples"] = report["conflicts"][:5]

            with run.stage("govern", case=case, ontology=onto.name) as out:
                entities = {v: graph.entities(v, case, int(cfg.runtime.entity_limit))
                            for v in graph.views}
                typing = {"declared": 0, "generic_fallback": 0, "undeclared_other": 0}
                for rows in entities.values():
                    for row in rows:
                        typing[onto.typing_of(row["labels"])] += 1
                out["entities_per_view"] = {v: len(r) for v, r in entities.items()}
                out["typing"] = typing

            with run.stage("serve", case=case,
                           protected=list(cfg.decisive.evidence.protected_slots)) as out:
                decision = verify_mod.serve_or_refuse(
                    report["conflicts"],
                    protected=list(cfg.decisive.evidence.protected_slots))
                out.update(decision)

            flat = [f for view_facts in facts.values() for f in view_facts]
            with run.stage("answer", case=case,
                           offline=bool(cfg.decisive.agent.offline),
                           model=str(cfg.decisive.agent.model)) as out:
                evidence, ids = agent_mod.serialize_evidence(
                    flat, int(cfg.decisive.evidence.budget_chars))
                result = agent_mod.answer(
                    question=f"Report the verified facts for case {case}.",
                    evidence=evidence, evidence_ids=ids,
                    model=str(cfg.decisive.agent.model), run=run,
                    offline=bool(cfg.decisive.agent.offline),
                    max_tokens=int(cfg.decisive.agent.max_tokens),
                    base_url=str(cfg.decisive.agent.base_url))
                out.update(result.as_dict())

            per_case.append({
                "case": case,
                "comparable_key_rate": report["comparable_key_rate"],
                "disagreement_rate": report["disagreement_rate"],
                "kinds": report["kinds"],
                "served": decision["servable"],
            })

        rates = [c["comparable_key_rate"] for c in per_case] or [0.0]
        directory = run.finish({
            "cases": len(per_case),
            "mean_comparable_key_rate": round(sum(rates) / len(rates), 6),
            "per_case": per_case,
        })
        print(f"\ninspect this run:\n  {directory}/run.log"
              f"\n  {directory}/trace.jsonl"
              f"\n  {directory}/decisive.json")
    finally:
        graph.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
