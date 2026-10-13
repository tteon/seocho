"""Derived subtask signals. Proxies never become ground-truth correctness labels."""
from __future__ import annotations

import hashlib
import copy
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ...recorder.store import canonical
from .information import normalized

ALGORITHM_VERSION = 'signals-v2-evaluator-normalization'
TIERS = ('T0_PARAMETRIC', 'T1_RETRIEVAL', 'T2_REFERENCE')


class SignalConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    silver_k: int = Field(default=2, ge=2)
    agreement: str = 'normalized_exact'
    alignment_threshold: float = Field(default=0.75, ge=0, le=1)
    ambiguity_margin: float = Field(default=0.05, ge=0, le=1)
    position_weight: float = Field(default=0.1, ge=0, le=1)
    cluster_threshold: float = Field(default=0.9, ge=0, le=1)
    entropy_drop_threshold: float = Field(default=0.5, ge=0)
    minimum_samples: int = Field(default=2, ge=2)


class Encoder(Protocol):
    identity: str
    semantic: bool
    def encode(self, text: str) -> list[float]: ...


class LexicalHashEncoder:
    """Offline deterministic lexical embedding; not a semantic paraphrase model."""
    identity = 'lexical-hash-word-bigram-256-v1'
    semantic = False

    def encode(self, text: str) -> list[float]:
        words = re.findall(r'\w+', normalized(text))
        features = words + [' '.join(pair) for pair in zip(words, words[1:])]
        values = [0.0] * 256
        for feature, count in Counter(features).items():
            key = hashlib.sha256(feature.encode()).digest()
            values[int.from_bytes(key[:2], 'big') % 256] += count * (1 if key[2] % 2 else -1)
        length = math.sqrt(sum(v * v for v in values))
        return [v / length for v in values] if length else values


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError('Encoder dimensions must be stable and nonempty')
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) /
        (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b)) or 1)))


@dataclass
class Subanswer:
    task_id: str
    text: str
    position: int
    answer: str
    tier: str
    origin: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    key: str | None = None
    alignment_method: str | None = None
    alignment_confidence: float | None = None


@dataclass
class Run:
    run_id: str
    workspace_id: str
    case_id: str
    seed: int
    sample: int
    signature: str
    correct: bool | None
    subtasks: list[Subanswer]
    condition: dict[str, Any]
    intervention: dict[str, Any] | None = None
    base_run_id: str | None = None

    @property
    def tier(self) -> str | None:
        delivered = {s.tier for s in self.subtasks}
        return next(iter(delivered)) if len(delivered) == 1 else None


def align(runs: list[Run], config: SignalConfig, encoder: Encoder) -> list[dict[str, Any]]:
    rows = []
    groups: dict[tuple[str, str], list[Run]] = defaultdict(list)
    for run in runs:
        groups[run.workspace_id, run.case_id].append(run)
    for group, members in sorted(groups.items()):
        # Frozen deterministic anchor; never use correctness or answers for alignment.
        anchor = min(members, key=lambda r: (-len(r.subtasks), [(s.text, s.position) for s in r.subtasks], r.run_id))
        for run in sorted(members, key=lambda r: r.run_id):
            candidates = []
            for sub in run.subtasks:
                for target in anchor.subtasks:
                    exact = normalized(sub.text) == normalized(target.text) and sub.position == target.position
                    sim = max(0.0, cosine(encoder.encode(sub.text), encoder.encode(target.text)))
                    score = 1.0 if exact else ((1-config.position_weight)*sim + config.position_weight/(1+abs(sub.position-target.position)))
                    candidates.append((score, sub.task_id, target.task_id, exact, target))
            used_sub, used_target = set(), set()
            for score, sub_id, target_id, exact, target in sorted(candidates, key=lambda c: (-c[0], c[1], c[2])):
                if sub_id in used_sub or target_id in used_target:
                    continue
                alternatives = [c[0] for c in candidates if c[1] == sub_id and c[2] != target_id]
                margin = score - max(alternatives, default=0)
                if score < config.alignment_threshold or (not exact and margin < config.ambiguity_margin):
                    continue
                sub = next(s for s in run.subtasks if s.task_id == sub_id)
                sub.key = hashlib.sha256(canonical([group, normalized(target.text), target.position])).hexdigest()[:24]
                sub.alignment_method = 'exact_text_position' if exact else encoder.identity + '+position'
                sub.alignment_confidence = score
                used_sub.add(sub_id)
                used_target.add(target_id)
            for sub in run.subtasks:
                rows.append({'run_id':run.run_id, 'subtask_id':sub.task_id, 'alignment_key':sub.key,
                    'method':sub.alignment_method or 'unmatched', 'confidence':sub.alignment_confidence,
                    'encoder':encoder.identity, 'semantic':encoder.semantic, 'position':sub.position,
                    'anchor_run_id':anchor.run_id, 'anchor_policy':'longest_plan_then_text_and_id'})
    return rows


def clusters(items: list[Subanswer], encoder: Encoder, threshold: float, exact: bool = False) -> list[list[Subanswer]]:
    groups: list[list[Subanswer]] = []
    for item in sorted(items, key=lambda s: (normalized(s.answer), s.origin)):
        # Complete-link membership avoids transitive A~B~C agreement inflation.
        target = next((g for g in groups if all(normalized(item.answer) == normalized(member.answer)
            if exact else cosine(encoder.encode(item.answer), encoder.encode(member.answer)) >= threshold
            for member in g)), None)
        if target is None:
            groups.append([item])
        else:
            target.append(item)
    return groups


def derive(runs: list[Run], config: SignalConfig, encoder: Encoder | None = None) -> dict[str, list[dict[str, Any]]]:
    encoder = encoder or LexicalHashEncoder()
    runs = copy.deepcopy(runs)
    if config.agreement not in ('normalized_exact', 'embedding'):
        raise ValueError('Agreement must be normalized_exact or embedding')
    result: dict[str, list[dict[str, Any]]] = {name:[] for name in
        ('alignment','tier_transitions','silver_labels','first_divergence','knock_in',
         'entropy','entropy_change','information_mapping','gaps')}
    result['alignment'] = align(runs, config, encoder)
    units: dict[tuple[Any,...], dict[str,list[Run]]] = defaultdict(lambda:defaultdict(list))
    for run in runs:
        if not run.intervention and run.tier in TIERS:
            units[(run.workspace_id, run.case_id, run.seed, run.sample, run.signature)][run.tier].append(run)
    for key, variants in sorted(units.items()):
        for low, high in ((TIERS[0],TIERS[1]),(TIERS[0],TIERS[2]),(TIERS[1],TIERS[2])):
            left, right = variants.get(low, []), variants.get(high, [])
            if len(left) != 1 or len(right) != 1 or left[0].correct is None or right[0].correct is None:
                result['gaps'].append({'signal':'tier_transition','unit':list(key),'tiers':[low,high],
                    'reason':'missing_unknown_or_ambiguous_pair'})
                continue
            result['tier_transitions'].append({'unit':list(key),'from':low,'to':high,
                'from_run':left[0].run_id,'to_run':right[0].run_id,
                'from_correct':left[0].correct,'to_correct':right[0].correct,
                'retrieval_harm':low==TIERS[0] and high==TIERS[1] and left[0].correct and not right[0].correct})
    silver: dict[tuple[str,str,str], dict[str, Any]] = {}
    pools: dict[tuple[str,str,str], list[tuple[Run,Subanswer]]] = defaultdict(list)
    for run in runs:
        if run.correct is True and run.tier == TIERS[2] and not run.intervention:
            for sub in run.subtasks:
                if sub.key:
                    pools[run.workspace_id,run.case_id,sub.key].append((run,sub))
    for key, members in sorted(pools.items()):
        # A cached origin is not another independent support sample.
        unique = {sub.origin:sub for _,sub in members}
        grouped = clusters(list(unique.values()), encoder, config.cluster_threshold, config.agreement=='normalized_exact')
        eligible = [g for g in grouped if len(g)>=config.silver_k]
        if len(eligible) != 1:
            result['gaps'].append({'signal':'silver','key':list(key),'reason':'insufficient_or_conflicting_consensus'})
            continue
        winner = eligible[0]
        label = {'workspace_id':key[0],'case_id':key[1],'alignment_key':key[2], 'answer':winner[0].answer,
            'support_origins':sorted(s.origin for s in winner),
            'support_runs':sorted({r.run_id for r,s in members if s.origin in {v.origin for v in winner}}),
            'agreement':config.agreement,'encoder':encoder.identity,'silver_k':config.silver_k,
            'status':'SILVER_NOT_GOLD'}
        result['silver_labels'].append(label)
        silver[key] = label
    for run in runs:
        if run.tier not in TIERS[:2]:
            continue
        for sub in sorted(run.subtasks, key=lambda s:s.position):
            label = silver.get((run.workspace_id,run.case_id,sub.key))
            if label is None:
                result['first_divergence'].append({'run_id':run.run_id,'status':'unresolved_prefix',
                    'subtask_id':sub.task_id,'position':sub.position})
                break
            match = normalized(sub.answer)==normalized(label['answer']) if config.agreement=='normalized_exact' else cosine(encoder.encode(sub.answer),encoder.encode(label['answer']))>=config.cluster_threshold
            if not match:
                result['first_divergence'].append({'run_id':run.run_id,'status':'first_silver_divergence',
                    'subtask_id':sub.task_id,'alignment_key':sub.key,'position':sub.position,'approximation':True})
                break
        else:
            result['first_divergence'].append({'run_id':run.run_id,'status':'no_silver_divergence'})
    by_id = {r.run_id:r for r in runs}
    knock: dict[tuple[str,str],list[tuple[Run,Run]]] = defaultdict(list)
    for run in runs:
        if run.intervention and run.intervention.get('operator')=='knock_in_reference':
            base = by_id.get(run.base_run_id)
            target = run.intervention.get('subtask')
            if not base or base.workspace_id!=run.workspace_id or base.signature!=run.signature or base.seed!=run.seed or base.case_id!=run.case_id or base.correct is not False or run.correct is None:
                result['gaps'].append({'signal':'knock_in','run_id':run.run_id,'reason':'missing_unpaired_or_not_incorrect_base'})
                continue
            sub = next((s for s in base.subtasks if s.task_id==target),None)
            derived_sub = next((s for s in run.subtasks if s.task_id==target),None)
            if not sub or not sub.key or not derived_sub or derived_sub.key!=sub.key or derived_sub.origin==sub.origin or derived_sub.tier!=TIERS[2] or sub.tier not in TIERS[:2]:
                result['gaps'].append({'signal':'knock_in','run_id':run.run_id,'reason':'target_alignment_or_delivery_unverified'})
                continue
            bucket = knock[base.run_id,sub.key]
            if any(next(s for s in previous.subtasks if s.task_id==target).origin==derived_sub.origin for _,previous in bucket):
                result['gaps'].append({'signal':'knock_in','run_id':run.run_id,'reason':'reused_target_origin'})
                continue
            bucket.append((base,run))
    for (base,key), members in sorted(knock.items()):
        result['knock_in'].append({'base_run_id':base,'alignment_key':key,'samples':len(members),
            'rescued':sum(r.correct is True for _,r in members),
            'bottleneck_score':sum(r.correct is True for _,r in members)/len(members),
            'derived_run_ids':[r.run_id for _,r in members], 'status':'paired_rescue_proxy_not_causal_proof'})
    entropy_pools: dict[tuple[Any,...], list[Subanswer]] = defaultdict(list)
    for run in runs:
        if not run.intervention:
            for sub in run.subtasks:
                if sub.key:
                    entropy_pools[run.workspace_id,run.case_id,run.seed,run.signature,sub.key,sub.tier].append(sub)
    for key, items in sorted(entropy_pools.items()):
        independent = list({s.origin:s for s in items}.values())
        grouped = clusters(independent,encoder,config.cluster_threshold)
        n = len(independent)
        entropy = -sum((len(g)/n)*math.log2(len(g)/n) for g in grouped) if n>=config.minimum_samples else None
        result['entropy'].append({'unit':list(key[:-1]),'tier':key[-1],'samples':n,'entropy_bits':entropy,
            'cluster_sizes':[len(g) for g in grouped], 'encoder':encoder.identity,
            'semantic':encoder.semantic,'proxy':'semantic_cluster_entropy' if encoder.semantic else 'lexical_disagreement_entropy'})
    ent = {(tuple(e['unit']),e['tier']):e for e in result['entropy']}
    for unit,tier in sorted(ent):
        if tier not in TIERS[:2]:
            continue
        high = ent.get((unit,TIERS[2]))
        low = ent[unit,tier]
        if not high or low['entropy_bits'] is None or high['entropy_bits'] is None:
            continue
        drop = low['entropy_bits']-high['entropy_bits']
        result['entropy_change'].append({'unit':list(unit),'from':tier,'to':TIERS[2], 'drop_bits':drop,
            'classification':'knowledge_gap_proxy' if drop>=config.entropy_drop_threshold else 'reasoning_difficulty_proxy',
            'semantic':encoder.semantic,'not_a_diagnosis':True})
    for run in runs:
        for sub in run.subtasks:
            for item in sub.evidence:
                result['information_mapping'].append({'run_id':run.run_id,'subtask_id':sub.task_id,
                    'alignment_key':sub.key,'content_hash':hashlib.sha256(item['text'].encode()).hexdigest(),
                    'source':item['source'],'evidence_id':item['evidence_id'],'tier':sub.tier,
                    'method':'observed_delivery','delivery_confidence':1.0,
                    'relevance_similarity':max(0.0,cosine(encoder.encode(sub.text),encoder.encode(item['text']))),
                    'encoder':encoder.identity,'semantic':encoder.semantic,
                    'limitation':'Delivery proves availability, not relevance or evidence use'})
    return result
