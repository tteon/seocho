"""FederationAgent — routes a query, fans out to provider-agents, merges (hq-42k).

Two merge operators, chosen by the question-type router (the 10_question_router
insight): reference-data questions → survivorship vote over provider facts
(lib/survivorship.survive_numeric); narrative questions → concat provider
contexts into one synthesis call. Partial failure is recorded, never imputed
(§20.2): providers that error/abstain contribute nothing and are listed in
`unavailable`; the answer carries `degraded` + attempted/answered counts.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parents[1]
ROOT = MDM_ROOT.parents[1]
for p in (str(MDM_ROOT), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agents.contracts import (  # noqa: E402
    ABSTAIN_MARK, FederationRequest, FederationResponse, ProviderResponse,
)
from agents.provider_agent import ProviderAgent  # noqa: E402
from lib.survivorship import SourceFact, survive_numeric  # noqa: E402

# Deterministic router (reused shape from 10_question_router.py): a reported
# figure for a stated period -> reference (survivorship); else narrative.
_METRIC_KW = re.compile(
    r"\b(eps|margin|revenue|income|profit|cost|costs|ratio|debt|ocf|"
    r"cash flow|p/e|assets|liabilities|equity|dividend|payout)\b", re.IGNORECASE)
_YEAR_KW = re.compile(r"\b(?:fy\s?'?\d{2,4}|(?:19|20)\d{2})\b", re.IGNORECASE)
_EXPLAIN_KW = re.compile(
    r"\b(driver|drivers|sustainab\w*|analysis|explain|why|how|impact|trend)\b",
    re.IGNORECASE)


def route_deterministic(query: str) -> str:
    if (_METRIC_KW.search(query) and _YEAR_KW.search(query)
            and not _EXPLAIN_KW.search(query)):
        return "reference"
    return "narrative"


_NARRATIVE_SYNTH_SYSTEM = (
    "You are a financial analyst. Multiple specialist providers each extracted "
    "a knowledge graph from the same SEC 10-K filings and returned their view "
    "below. Synthesize ONE answer using only the provided context. Preserve "
    "units, scale, period, basis; show arithmetic for any growth/ratio. If the "
    "providers conflict, say so. If the needed figure is absent, say 'not in "
    "the provided context'."
)


class FederationAgent:
    def __init__(self, *, providers: list[ProviderAgent], ruleset,
                 synth_client, synth_spec, answer_system: str):
        self.providers = providers
        self.ruleset = ruleset
        self._client = synth_client
        self._spec = synth_spec
        self._answer_system = answer_system   # per-provider answer prompt (silo)

    # -- stage 1: select ----------------------------------------------------

    def select(self, req: FederationRequest) -> list[ProviderAgent]:
        """Replicate-and-route: default fan out to ALL providers (overlap +
        disagreement is the federation value). Capability routing to a subset
        is a measured ablation, not the default (panel decision)."""
        return list(self.providers)

    # -- stage 2: fan out ---------------------------------------------------

    def fan_out(self, providers, req: FederationRequest,
                *, retrieve_only: bool) -> list[ProviderResponse]:
        """Sequential fan-out (auditability over speed; per-provider latency
        attributed). A provider exception is caught into the response's error
        — the fan-out never fails as a whole (§20.2)."""
        out = []
        for pa in providers:
            try:
                out.append(pa.answer(req.query, req.case_id,
                                     answer_system=self._answer_system,
                                     retrieve_only=retrieve_only))
            except Exception as exc:  # noqa: BLE001 — record, don't impute
                from agents.contracts import Provenance
                out.append(ProviderResponse(
                    provider_id=pa.provider_id, query=req.query, case_id=req.case_id,
                    abstain=True, context="", facts=tuple(), answer=None,
                    confidence=0.0, retrieval_ms=0.0, answer_ms=0.0,
                    error=f"{type(exc).__name__}: {exc}",
                    provenance=Provenance(pa.provider_id, pa.instance.uri, pa.model,
                                          "", 0)))
        return out

    # -- stage 3: merge -----------------------------------------------------

    def _merge_reference(self, responses, req) -> tuple[str, dict]:
        """Group provider facts by (metric, period, basis) and survivorship-vote.

        Reuses survive_numeric: abstention is a non-vote; no majority quarantines
        (no silent pick, §20.2). Returns (answer_text, survived_summary)."""
        answered = [r for r in responses if not r.abstain and not r.error]
        panel = len(responses)
        groups: dict[tuple, list] = {}
        for r in answered:
            for f in r.facts:
                key = (f.metric_raw.strip().lower(), f.period.strip().lower(),
                       f.basis.strip().lower())
                groups.setdefault(key, []).append((r, f))
        golden, quarantined = [], []
        for (metric, period, basis), items in sorted(groups.items()):
            facts = [SourceFact(source=f"{r.provider_id}/{r.provenance.model}",
                                raw=f.value_raw) for r, f in items]
            out = survive_numeric(facts, panel_size=panel or 1, ruleset=self.ruleset)
            label = items[0][1].metric_raw
            if out.status == "golden":
                golden.append({"metric": label, "period": period,
                               "value": out.value_raw, "confidence": out.confidence,
                               "agreement": f"{out.agreement_count}/{out.sources_reporting}",
                               "rule": out.rule})
            elif out.status == "quarantine":
                quarantined.append({"metric": label, "period": period,
                                    "reason": out.rule,
                                    "candidates": [f"{r.provider_id}:{f.value_raw}"
                                                   for r, f in items]})
        # Deterministic answer text built from survived facts (no extra LLM call).
        if not golden and not quarantined:
            return ABSTAIN_MARK, {"golden": [], "quarantined": []}
        lines = []
        for g in golden:
            lines.append(f"{g['metric']} [{g['period']}] = {g['value']} "
                         f"(confidence {g['confidence']}, agreement {g['agreement']})")
        for q in quarantined:
            lines.append(f"{q['metric']} [{q['period']}]: UNRESOLVED — providers "
                         f"disagree ({'; '.join(q['candidates'])})")
        return " | ".join(lines), {"golden": golden, "quarantined": quarantined,
                                   "panel_size": panel}

    def _merge_narrative(self, responses, req) -> str:
        """Concat non-abstaining provider contexts -> one synthesis LLM call."""
        from examples.finder.lib import llm_io

        parts = [r.context for r in responses if r.context.strip() and not r.error]
        if not parts:
            return ABSTAIN_MARK
        ctx = ("=== LIVE FEDERATION across providers (views may conflict) ===\n\n"
               + "\n\n".join(parts))
        return llm_io.chat_complete(
            client=self._client, model=self._spec.model,
            system=_NARRATIVE_SYNTH_SYSTEM, user=f"Question: {req.query}\n\n{ctx}",
            temperature=0.0, label="fed-synth", max_attempts=3, spec=self._spec)

    # -- orchestration ------------------------------------------------------

    def answer(self, req: FederationRequest) -> FederationResponse:
        route = req.mode if req.mode in ("reference", "narrative") \
            else route_deterministic(req.query)
        providers = self.select(req)
        # Reference path needs only facts (cheap retrieve-only fan-out); narrative
        # path also needs contexts (still retrieve-only — synthesis is one call).
        responses = self.fan_out(providers, req, retrieve_only=True)

        attempted = len(responses)
        answered = [r for r in responses if not r.abstain and not r.error]
        unavailable = tuple(
            {"provider": r.provider_id,
             "reason": r.error or ("abstain" if r.abstain else "")}
            for r in responses if r.error or r.abstain)
        fan_lat = {r.provider_id: round(r.retrieval_ms, 1) for r in responses}

        t0 = time.perf_counter()
        survived = None
        if route == "reference":
            answer_text, survived = self._merge_reference(responses, req)
        else:
            answer_text = self._merge_narrative(responses, req)
        merge_ms = (time.perf_counter() - t0) * 1000

        return FederationResponse(
            query=req.query, case_id=req.case_id, route=route,
            selected_providers=tuple(p.provider_id for p in providers),
            provider_responses=tuple(responses), answer=answer_text,
            abstain=(ABSTAIN_MARK in (answer_text or "").lower()),
            survived=survived, providers_attempted=attempted,
            providers_answered=len(answered),
            degraded=len(answered) < attempted, unavailable=unavailable,
            fanout_latency_ms=fan_lat, answer_ms=merge_ms)
