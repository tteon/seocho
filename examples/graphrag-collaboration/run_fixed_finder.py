"""One FinDER question, fixed original-source evidence, five agent designs, MARA only."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
from pathlib import Path
from typing import Any


from seocho.agent.collaboration import Limits
from seocho.eval.finder_collaboration import Corpus, prepare_corpus, score_report
from seocho.eval.fixed_context_designs import DESIGNS, judge_answer, run_design


def fixed_evidence(corpus: Corpus, count: int = 6) -> list[dict[str, Any]]:
    """Rank original source once by question terms, never gold or candidate answers."""
    question = corpus.selected[0].case.question.lower()
    stop = set('the a an in on for of and or to from change calc calculation'.split())
    terms = set(re.findall(r'[a-z0-9]+', question)) - stop
    phrase = 'operating earnings' if 'operating earnings' in question else ''

    def rank(passage: Any) -> tuple[int, int, str]:
        text = passage.text.lower()
        score = sum(min(text.count(term), 8) for term in terms)
        if phrase:
            score += 8 * text.count(phrase)
        return (-score, passage.ordinal, passage.evidence_id)

    ranked = sorted([p for f in corpus.filings for p in f.passages], key=rank)
    return [p.graph_row() for p in ranked[:count]]


def save(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


async def execute(args: argparse.Namespace, corpus: Corpus, evidence: list[dict[str, Any]],
                  report: dict[str, Any], limits: Limits) -> int:
    if not args.recorder_url:
        raise ValueError('Live experiments require --recorder-url; direct provider bypass is disabled')
    from seocho.recorder.adapters.openai_agents import recorded_client
    from uuid import uuid4
    report['recorder_run_id'] = uuid4().hex
    model, client = recorded_client(recorder_url=args.recorder_url, model=args.model,
        workspace_id=args.workspace_id, run_id=report['recorder_run_id'], tier='T1_RETRIEVAL',
        template_version='finder-fixed-context-v1', timeout=limits.timeout_seconds)
    try:
        item = corpus.selected[0]
        order = list(args.designs)
        random.Random(args.order_seed).shuffle(order)
        report['execution_order'] = order
        for design in order:
            trial_run_id = uuid4().hex if args.recorder_url else None
            trial_model = model.for_run(trial_run_id, design) if args.recorder_url else model
            result = await run_design(model=trial_model, question=item.case.question, evidence=evidence,
                                      design=design, workspace_id=args.workspace_id, limits=limits)
            if args.recorder_url:
                result['recorder_run_id'] = trial_run_id
            refs = [r['evidence_id'] for r in evidence]
            # Metrics are evaluated after inference, with no evaluation gold in agent input.
            metric_input = {'answer': result['answer'], 'retrieval_receipts': [{'rows': evidence}],
                            'packets': [{'evidence_refs': refs}]}
            scores = score_report(metric_input, item, corpus.gold_mapping[item.case.case_id])
            scores['provided_reference_coverage'] = scores.pop('retrieved_reference_coverage')
            scores.pop('selected_reference_coverage')
            result['scores'] = scores
            report['trials'].append(result)
            save(args.output, report)
            print(f"{design}: {result['status']}; calls={result['budget']['model_calls']}; "
                  f"parallel={result['parallel_dispatch_observed']}", flush=True)
        # All candidate outputs are frozen before evaluator gold is exposed.
        report['expected_answer'] = item.case.expected_answer
        for result in report['trials']:
            if result['answer'] and not args.no_judge:
                judge_model = model.for_run(result['recorder_run_id'], result['design']) if args.recorder_url else model
                result['evaluation'] = await judge_answer(model=judge_model, question=item.case.question,
                    evidence=evidence, expected=item.case.expected_answer, answer=result['answer'])
                save(args.output, report)
                print(f"judge/{result['design']}: {result['evaluation']['status']}", flush=True)
        save(args.output, report)
        return int(any(t['status'] != 'completed' or
                       (not args.no_judge and t.get('evaluation', {}).get('status') != 'completed')
                       for t in report['trials']))
    finally:
        if args.recorder_url:
            report['attempts'] = model.attempts
            save(args.output, report)
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default='7e6c28fd')
    parser.add_argument('--dataset', type=Path, default=Path('.seocho/datasets/finder/data/train-00000-of-00001.parquet'))
    parser.add_argument('--documents', type=Path, default=Path('.seocho/datasets/finder/10-k.zip'))
    parser.add_argument('--model', default='DeepSeek-V3.1')
    parser.add_argument('--recorder-url', default=os.getenv('SEOCHO_RECORDER_URL'), help='Strict tagged Recorder base URL, e.g. http://127.0.0.1:8800/v1')
    parser.add_argument('--designs', nargs='+', choices=DESIGNS, default=list(DESIGNS))
    parser.add_argument('--workspace-id', default='finder-fixed-context-lab-v1')
    parser.add_argument('--order-seed', type=int, default=42)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--no-judge', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('outputs/collaboration-mara/finder-fixed-context.json'))
    args = parser.parse_args()
    try:
        if len(args.designs) != len(set(args.designs)):
            raise ValueError('Designs must be unique')
        corpus = prepare_corpus(args.dataset, args.documents, case_id=args.case)
        evidence = fixed_evidence(corpus)
        fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        limits = Limits(max_calls=24, max_tool_calls=24, max_output_tokens=2048, timeout_seconds=120,
                        max_context_bytes=100_000, max_admission_units=1_000_000)
        report = {'schema_version': 'seocho.finder-fixed-context.v1', 'manifest': corpus.manifest(),
                  'model': args.model, 'workspace_id': args.workspace_id, 'limits': vars(limits),
                  'fixed_evidence': evidence, 'evidence_sha256': fingerprint,
                  'designs': args.designs, 'trials': [],
                  'retry_policy': {'max_attempts_per_turn': 3, 'backoff_seconds': [5, 10],
                                   'charged_to_shared_budget': True},
                  'scope': 'One case, one trial per design, fixed question-only source selection, no database. '
                           'CoT means a prompt instruction, not observed private reasoning. Same-model judge '
                           'is separate from inference budgets and may exhibit self-preference bias. '
                           'Evidence coverage is not answer correctness; no general quality or speedup claim.'}
        save(args.output, report)
        print(f"Case {args.case}: {corpus.selected[0].case.question}; fixed passages={len(evidence)}", flush=True)
        if args.dry_run:
            return 0
        return asyncio.run(execute(args, corpus, evidence, report, limits))
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
