from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .config import load_experiment
from .harness import Case, Harness, HarnessRunError
from .information import Evidence, GoldGuard
from .evaluator import load_evaluation


def prepare_finder(dataset: Path, documents: Path, case_id: str, output: Path) -> None:
    from ..finder_collaboration import prepare_corpus, load_rows
    corpus = prepare_corpus(dataset, documents, case_id=case_id)
    item = corpus.selected[0]
    terms = set(re.findall(r'[a-z0-9]+', item.case.question.lower())) - set('the a an in on for of and or to from change calc calculation'.split())
    phrase = 'operating earnings' if 'operating earnings' in item.case.question.lower() else ''

    def rank(passage: Any) -> tuple[int, int, str]:
        text = passage.text.lower()
        score = sum(min(text.count(term), 8) for term in terms) + (8 * text.count(phrase) if phrase else 0)
        return -score, passage.ordinal, passage.evidence_id

    passages = sorted([p for filing in corpus.filings for p in filing.passages], key=rank)[:6]
    fixed = [Evidence(evidence_id=p.evidence_id, text=p.text, source=p.source) for p in passages]
    guard = GoldGuard([item.case.expected_answer])
    reference = []
    blocked = []
    for passage in fixed:
        try:
            guard.check([passage])
            reference.append(passage.model_dump())
        except ValueError:
            blocked.append(passage.evidence_id)
    if not reference:
        raise ValueError('No safe agent reference remains; provide separately curated evidence')
    output.mkdir(parents=True, exist_ok=True)
    public = {'case_id': case_id, 'question': item.case.question,
              'fixed_passages': [r.model_dump() for r in fixed], 'agent_reference': reference}
    (output / 'cases.json').write_text(json.dumps([public], ensure_ascii=False, indent=2))
    raw = next(row for row in load_rows(dataset) if str(row.get('_id') or row.get('id')) == case_id)
    reasoning = raw.get('reasoning')
    if reasoning is not None and not isinstance(reasoning, bool):
        raise ValueError('FinDER reasoning label must be a boolean or missing')
    private = {'gold_answer': item.case.expected_answer, 'metadata': {
        'domain': str(raw.get('category') or 'unknown'), 'reasoning_required': reasoning,
        'reasoning_type': str(raw.get('type') or 'None'), 'gold_references': list(item.references)}}
    (output / 'evaluation.json').write_text(json.dumps({case_id: private}, ensure_ascii=False))
    manifest = {'source': corpus.manifest(), 'selection': 'same question-only fixed six passages',
                'reference_preparation': 'separate agent artifact; exact gold substring matches excluded',
                'blocked_evidence_ids': blocked,
                'agent_cases_sha256': hashlib.sha256((output / 'cases.json').read_bytes()).hexdigest(),
                'contamination_measurement': 'not_measured',
                'limitation': 'Automatic isolation is not a semantic gold-leak proof or human curation'}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


async def execute(config: Path, output: Path) -> None:
    experiment = load_experiment(config)
    evaluation = json.loads(experiment.evaluation.read_text())
    cases = []
    for row in json.loads(experiment.dataset.read_text()):
        gold, metadata = load_evaluation(evaluation[row['case_id']])
        cases.append(Case.model_validate({**row, 'gold_answer': gold, 'evaluation_metadata': metadata}))
    harness = Harness(experiment)
    reports = []
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for case in cases:
            for seed in experiment.seeds:
                for sample in range(experiment.samples):
                    for condition in experiment.conditions:
                        try:
                            reports.append(await harness.run(case, condition, seed, sample))
                        except HarnessRunError as exc:
                            reports.append(exc.report)
                        output.write_text(json.dumps({'trials': reports}, ensure_ascii=False, indent=2))
        if any(r['status'] == 'failed' for r in reports):
            raise SystemExit('Experiment contains failed runs; inspect the saved report and Recorder events')
    finally:
        await harness.close()


async def intervene(config: Path, base_run: str, spec: Path, limits: Path, samples: int,
                    seed: int, output: Path, budgets: list[int] | None = None) -> None:
    import yaml
    from .intervention import CostGuard, CostLimits, Intervention, budget_sweep, load_snapshot, run_intervention
    experiment = load_experiment(config)
    harness = Harness(experiment)
    try:
        snapshot = await load_snapshot(harness.events, experiment.recorder_url, base_run, experiment.workspace_id)
        root = next(e for e in snapshot['events'] if e['kind'] == 'decision' and e['action'] == 'run')
        condition = next(c for c in experiment.conditions if c.condition_id == root['condition_id'])
        row = next(row for row in json.loads(experiment.dataset.read_text()) if row['case_id'] == root['payload']['case_id'])
        gold, metadata = load_evaluation(json.loads(experiment.evaluation.read_text())[row['case_id']])
        case = Case.model_validate({**row, 'gold_answer': gold, 'evaluation_metadata': metadata})
        change = Intervention.model_validate(yaml.safe_load(spec.read_text()))
        guard = CostGuard(CostLimits.model_validate(yaml.safe_load(limits.read_text())))
        output.parent.mkdir(parents=True, exist_ok=True)
        if budgets:
            if not change.subtask:
                raise ValueError('Budget sweep requires a subtask')
            reports = await budget_sweep(harness, case, condition, snapshot, change.subtask, budgets,
                                        samples=samples, guard=guard, seed=seed)
        else:
            reports = await run_intervention(harness, case, condition, snapshot, change,
                                            samples=samples, guard=guard, seed=seed)
        output.write_text(json.dumps({'base_run_id': base_run, 'requested_samples': samples,
            'trials': reports, 'reserved_tokens': guard.reserved_tokens,
            'observed_tokens': guard.observed_tokens, 'stopped': guard.stopped}, ensure_ascii=False, indent=2))
        if guard.stopped or any(r['status'] != 'completed' for r in reports):
            raise SystemExit('Intervention stopped or failed; inspect saved report and Recorder')
    finally:
        await harness.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='Recorder-backed Phase 2 experiments; no direct provider client')
    subs = parser.add_subparsers(dest='command', required=True)
    run = subs.add_parser('run')
    run.add_argument('--config', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    prepare = subs.add_parser('prepare-finder')
    prepare.add_argument('--dataset', type=Path, required=True)
    prepare.add_argument('--documents', type=Path, required=True)
    prepare.add_argument('--case', required=True)
    prepare.add_argument('--output', type=Path, required=True)
    intervention = subs.add_parser('intervene')
    intervention.add_argument('--config', type=Path, required=True)
    intervention.add_argument('--base-run', required=True)
    intervention.add_argument('--spec', type=Path, required=True)
    intervention.add_argument('--limits', type=Path, required=True)
    intervention.add_argument('--samples', type=int, required=True)
    intervention.add_argument('--seed', type=int, default=42)
    intervention.add_argument('--output', type=Path, required=True)
    intervention.add_argument('--budgets', type=int, nargs='+')
    args = parser.parse_args()
    if args.command == 'run':
        asyncio.run(execute(args.config, args.output))
    elif args.command == 'intervene':
        asyncio.run(intervene(args.config, args.base_run, args.spec, args.limits, args.samples,
                              args.seed, args.output, args.budgets))
    else:
        prepare_finder(args.dataset, args.documents, args.case, args.output)


if __name__ == '__main__':
    main()
