"""Offline FinDER label inventory; never infer reasoning from operation type."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from seocho.eval.finder_collaboration import load_rows


def prepare(dataset: Path, output: Path) -> None:
    rows = load_rows(dataset)
    counts: Counter[tuple[str, str, str]] = Counter()
    conflicts = []
    for row in rows:
        label = row.get('reasoning')
        if label is not None and not isinstance(label, bool):
            raise ValueError('Non-boolean FinDER reasoning label')
        domain = str(row.get('category') or 'unknown')
        kind = str(row.get('type') or 'None')
        counts[domain, 'unknown' if label is None else str(label).lower(), kind] += 1
        if (label is True and kind == 'None') or (label is False and kind != 'None'):
            conflicts.append({'case_id': row.get('_id') or row.get('id'),
                             'reasoning_required': label, 'reasoning_type': kind})
    output.mkdir(parents=True, exist_ok=True)
    payload = {'dataset_sha256': hashlib.sha256(dataset.read_bytes()).hexdigest(),
        'records': len(rows), 'label_source': 'original reasoning boolean; never derived from type',
        'strata': [{'domain': d, 'reasoning_required': r, 'reasoning_type': t, 'count': n}
                   for (d,r,t),n in sorted(counts.items())],
        'label_type_mismatches': conflicts,
        'empty_cell_policy': 'unobserved combinations are absent, never synthetic negative labels',
        'design_hypotheses': {
            'single_reference': 'direct evidence-grounded answer baseline',
            'numeric': 'extract operands and units/periods, verify arithmetic, synthesize',
            'multi_reference_compositional': 'decompose, delegate independent evidence tasks, reconcile',
            'risk_legal_accounting': 'preserve conditions, qualifications and source scope; test verifier'},
        'selection_policy': 'stratify by domain x original reasoning, then operation and reference count; held-out companies for router selection',
        'routing_policy': 'gold, references and dataset labels are evaluator-only; oracle-label routing must be a separately named condition'}
    (output/'inventory.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    prepare(args.dataset,args.output)
