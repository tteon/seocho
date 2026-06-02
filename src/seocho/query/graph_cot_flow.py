from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .graph_cot_contracts import (
    AnswerDraft,
    GraphCoTFinalAnswer,
    GraphCoTQuestionFrame,
    GuardrailFinding,
    GuardrailVerdict,
    QueryEvidencePacket,
    SupervisorDirective,
)


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


@dataclass(frozen=True, slots=True)
class GraphCoTRetrievalResult:
    """Internal retrieval result for Graph-CoT orchestration."""

    packet: QueryEvidencePacket
    lpg_result: Optional[Dict[str, Any]] = None
    rdf_result: Optional[Dict[str, Any]] = None


class QuerySupervisorAgent:
    """Deterministic planner/finalizer for the Graph-CoT lane."""

    def build_question_frame(
        self,
        *,
        question: str,
        workspace_id: str,
        databases: Sequence[str],
        semantic_context: Dict[str, Any],
        ontology_context_mismatch: Optional[Dict[str, Any]] = None,
    ) -> GraphCoTQuestionFrame:
        support = semantic_context.get("preflight_support_assessment", {})
        intent = semantic_context.get("intent", {})
        return GraphCoTQuestionFrame(
            question=question,
            workspace_id=workspace_id,
            databases=tuple(str(database).strip() for database in databases if str(database).strip()),
            intent_id=str(intent.get("intent_id", "")).strip(),
            entity_candidates=tuple(
                str(item).strip()
                for item in semantic_context.get("entities", [])
                if str(item).strip()
            ),
            unresolved_entities=tuple(
                str(item).strip()
                for item in semantic_context.get("unresolved_entities", [])
                if str(item).strip()
            ),
            support_status=str(support.get("status", "")).strip(),
            support_reason=str(support.get("reason", "")).strip(),
            ontology_context_mismatch=dict(
                ontology_context_mismatch
                or semantic_context.get("ontology_context_mismatch", {})
                or {}
            ),
            semantic_context=dict(semantic_context),
        )

    def plan(
        self,
        *,
        question_frame: GraphCoTQuestionFrame,
        semantic_context: Dict[str, Any],
        route: str,
        repair_budget: int,
    ) -> SupervisorDirective:
        intent = semantic_context.get("intent", {})
        focus_slots = tuple(
            str(slot).strip()
            for slot in intent.get("focus_slots", [])
            if str(slot).strip()
        )
        preflight_support = semantic_context.get("preflight_support_assessment", {})
        missing_slots = [
            str(slot).strip()
            for slot in preflight_support.get("missing_slots", [])
            if str(slot).strip()
        ]
        must_not_infer: List[str] = [f"slot:{slot}" for slot in missing_slots]
        must_not_infer.extend(
            f"entity:{entity}"
            for entity in question_frame.unresolved_entities
            if entity
        )
        answer_style = "evidence"
        if question_frame.unresolved_entities or question_frame.support_status in {"partial", "unsupported"}:
            answer_style = "partial"
        objective = question_frame.intent_id or "graph_grounded_answer"
        return SupervisorDirective(
            objective=objective,
            route=route if route in {"lpg", "rdf", "hybrid"} else "lpg",
            answer_style=answer_style,
            must_ground_slots=focus_slots,
            must_not_infer=tuple(dict.fromkeys(must_not_infer)),
            max_repair_attempts=max(0, min(1, int(repair_budget or 0))),
            require_guardrail=True,
        )

    def finalize(
        self,
        *,
        answer_text: str,
        draft: AnswerDraft,
        verdict: GuardrailVerdict,
        evidence: QueryEvidencePacket,
    ) -> GraphCoTFinalAnswer:
        status: str = "answered"
        if draft.abstain or verdict.decision == "refuse":
            status = "abstained"
        elif draft.is_partial or verdict.decision == "revise":
            status = "partial"
        return GraphCoTFinalAnswer(
            answer_text=answer_text,
            status=status,  # type: ignore[arg-type]
            draft=draft,
            verdict=verdict,
            evidence=evidence,
        )


class Text2CypherAgent:
    """Graph-CoT retrieval agent over the existing LPG/RDF specialists."""

    def __init__(self, *, lpg_agent: Any, rdf_agent: Any) -> None:
        self.lpg_agent = lpg_agent
        self.rdf_agent = rdf_agent

    def retrieve(
        self,
        *,
        question: str,
        databases: Sequence[str],
        workspace_id: str,
        semantic_context: Dict[str, Any],
        directive: SupervisorDirective,
        constraint_slices: Dict[str, Dict[str, Any]],
        ranked_matches: Sequence[Dict[str, Any]],
    ) -> GraphCoTRetrievalResult:
        if directive.route == "abstain":
            return GraphCoTRetrievalResult(
                packet=self._empty_packet(
                    semantic_context=semantic_context,
                    ontology_context_mismatch=semantic_context.get("ontology_context_mismatch", {}),
                )
            )

        lpg_result: Optional[Dict[str, Any]] = None
        rdf_result: Optional[Dict[str, Any]] = None
        packet: Optional[QueryEvidencePacket] = None

        if directive.route in {"lpg", "hybrid"}:
            lpg_result = self.lpg_agent.run(
                question,
                databases,
                semantic_context,
                workspace_id=workspace_id,
                reasoning_mode=True,
                repair_budget=max(0, int(directive.max_repair_attempts or 0)),
                constraint_slices=constraint_slices,
                ranked_matches=ranked_matches,
            )
            packet = self._packet_from_lpg(
                lpg_result=lpg_result,
                semantic_context=semantic_context,
            )

        if directive.route in {"rdf", "hybrid"}:
            rdf_result = self.rdf_agent.run(question, databases, semantic_context)
            rdf_packet = self._packet_from_rdf(
                rdf_result=rdf_result,
                semantic_context=semantic_context,
            )
            if packet is None or (not packet.has_grounded_support and rdf_packet.has_grounded_support):
                packet = rdf_packet

        if packet is None:
            packet = self._empty_packet(
                semantic_context=semantic_context,
                ontology_context_mismatch=semantic_context.get("ontology_context_mismatch", {}),
            )

        return GraphCoTRetrievalResult(packet=packet, lpg_result=lpg_result, rdf_result=rdf_result)

    @staticmethod
    def _packet_from_lpg(
        *,
        lpg_result: Dict[str, Any],
        semantic_context: Dict[str, Any],
    ) -> QueryEvidencePacket:
        bundle = lpg_result.get("evidence_bundle", {})
        support = lpg_result.get("support_assessment", {})
        query_plan = lpg_result.get("query_plan", {})
        query_plan = query_plan if isinstance(query_plan, dict) else {}
        raw_params = query_plan.get("params", {})
        return QueryEvidencePacket(
            database=str(
                query_plan.get("database")
                or bundle.get("database")
                or ""
            ).strip(),
            cypher=str(query_plan.get("query", "")).strip(),
            params=dict(raw_params) if isinstance(raw_params, dict) else {},
            records=tuple(
                dict(record)
                for record in lpg_result.get("records", [])
                if isinstance(record, dict)
            ),
            selected_triples=tuple(
                dict(triple)
                for triple in bundle.get("selected_triples", [])
                if isinstance(triple, dict)
            ),
            slot_fills=dict(bundle.get("slot_fills", {}) if isinstance(bundle, dict) else {}),
            grounded_slots=tuple(
                str(slot).strip()
                for slot in bundle.get("grounded_slots", [])
                if str(slot).strip()
            ),
            missing_slots=tuple(
                str(slot).strip()
                for slot in bundle.get("missing_slots", [])
                if str(slot).strip()
            ),
            support_status=str(support.get("status", "")).strip(),
            support_reason=str(support.get("reason", "")).strip(),
            ontology_context_mismatch=dict(
                lpg_result.get("ontology_context_mismatch", {})
                or semantic_context.get("ontology_context_mismatch", {})
                or {}
            ),
            query_diagnostics=tuple(
                dict(item)
                for item in lpg_result.get("query_diagnostics", [])
                if isinstance(item, dict)
            ),
            repair_trace=tuple(
                dict(item)
                for item in lpg_result.get("reasoning", {}).get("repair_trace", [])
                if isinstance(item, dict)
            ),
        )

    @staticmethod
    def _packet_from_rdf(
        *,
        rdf_result: Dict[str, Any],
        semantic_context: Dict[str, Any],
    ) -> QueryEvidencePacket:
        records = [
            dict(record)
            for record in rdf_result.get("records", [])
            if isinstance(record, dict)
        ]
        first = records[0] if records else {}
        target_entity = str(first.get("name") or first.get("resource") or "").strip()
        slot_fills: Dict[str, Any] = {}
        selected_triples: List[Dict[str, Any]] = []
        grounded_slots: List[str] = []
        if target_entity:
            slot_fills["target_entity"] = target_entity
            slot_fills["supporting_fact"] = f"{target_entity} was matched as an RDF-like resource."
            grounded_slots.extend(["target_entity", "supporting_fact"])
            selected_triples.append(
                {
                    "source": target_entity,
                    "relation": "rdf_match",
                    "target": target_entity,
                    "target_labels": list(first.get("labels", [])) if isinstance(first.get("labels"), list) else [],
                }
            )
        intent = semantic_context.get("intent", {})
        focus_slots = [
            str(slot).strip()
            for slot in intent.get("focus_slots", [])
            if str(slot).strip()
        ]
        return QueryEvidencePacket(
            database=str(first.get("database", "")).strip(),
            cypher="",
            params={},
            records=tuple(records),
            selected_triples=tuple(selected_triples),
            slot_fills=slot_fills,
            grounded_slots=tuple(dict.fromkeys(grounded_slots)),
            missing_slots=tuple(slot for slot in focus_slots if slot not in grounded_slots),
            support_status="supported" if records else "unsupported",
            support_reason="rdf_resource_match" if records else "rdf_no_match",
            ontology_context_mismatch=dict(semantic_context.get("ontology_context_mismatch", {}) or {}),
            query_diagnostics=tuple(
                dict(item)
                for item in semantic_context.get("query_diagnostics", [])
                if isinstance(item, dict)
            ),
            repair_trace=(),
        )

    @staticmethod
    def _empty_packet(
        *,
        semantic_context: Dict[str, Any],
        ontology_context_mismatch: Dict[str, Any],
    ) -> QueryEvidencePacket:
        intent = semantic_context.get("intent", {})
        focus_slots = tuple(
            str(slot).strip()
            for slot in intent.get("focus_slots", [])
            if str(slot).strip()
        )
        return QueryEvidencePacket(
            database="",
            cypher="",
            params={},
            records=(),
            selected_triples=(),
            slot_fills={},
            grounded_slots=(),
            missing_slots=focus_slots,
            support_status="unsupported",
            support_reason="no_retrieval_route",
            ontology_context_mismatch=dict(ontology_context_mismatch or {}),
            query_diagnostics=tuple(
                dict(item)
                for item in semantic_context.get("query_diagnostics", [])
                if isinstance(item, dict)
            ),
            repair_trace=(),
        )


class GraphCoTAnswerGenerationAgent:
    """Wrap the existing answer generator into a structured AnswerDraft."""

    def __init__(self, *, base_answer_agent: Any) -> None:
        self.base_answer_agent = base_answer_agent

    def draft(
        self,
        *,
        question: str,
        route: str,
        semantic_context: Dict[str, Any],
        lpg_result: Optional[Dict[str, Any]],
        rdf_result: Optional[Dict[str, Any]],
        packet: QueryEvidencePacket,
        unresolved_entities: Sequence[str],
    ) -> AnswerDraft:
        answer_text = self.base_answer_agent.synthesize(
            question=question,
            route=route,
            semantic_context=semantic_context,
            lpg_result=lpg_result,
            rdf_result=rdf_result,
        )
        cited_facts = self._cited_facts(packet)
        abstain = (
            packet.support_status == "unsupported"
            or "no matching graph records were found" in answer_text.lower()
        )
        return AnswerDraft(
            answer_text=answer_text,
            cited_facts=cited_facts,
            grounded_slots=packet.grounded_slots,
            missing_slots=packet.missing_slots,
            unresolved_entities=tuple(
                str(item).strip()
                for item in unresolved_entities
                if str(item).strip()
            ),
            abstain=abstain,
            confidence_note=f"{packet.support_status}: {packet.support_reason}".strip(": "),
        )

    def revise(
        self,
        *,
        draft: AnswerDraft,
        question_frame: GraphCoTQuestionFrame,
        packet: QueryEvidencePacket,
        verdict: GuardrailVerdict,
    ) -> AnswerDraft:
        revised_text = draft.answer_text.strip()
        repair_hints = tuple(str(item).strip() for item in verdict.required_repairs if str(item).strip())
        forced_abstain = False
        if any("Abstain because the evidence packet does not support the requested claim." == hint for hint in repair_hints):
            revised_text = self._abstention_text(draft=draft, packet=packet)
            forced_abstain = True
        if any("State missing slots explicitly." == hint for hint in repair_hints):
            if packet.missing_slots and "missing slots:" not in revised_text.lower():
                revised_text += f" Missing slots: {', '.join(packet.missing_slots)}."
        if any("Name unresolved entities and keep the answer partial." == hint for hint in repair_hints):
            if question_frame.unresolved_entities and "unresolved entities:" not in revised_text.lower():
                revised_text += f" Unresolved entities: {', '.join(question_frame.unresolved_entities)}."
        if any("Add an ontology drift caveat." == hint for hint in repair_hints):
            if "ontology context warning" not in revised_text.lower():
                revised_text += (
                    " Ontology context warning: the active query context does not exactly match "
                    "the indexed graph context for this answer."
                )
        if any("Avoid unsupported time references." == hint for hint in repair_hints):
            if "time scope warning" not in revised_text.lower():
                revised_text += " Time scope warning: the cited evidence may not cover every requested year."
        return replace(
            draft,
            answer_text=revised_text.strip(),
            abstain=forced_abstain or packet.support_status == "unsupported" or draft.abstain,
        )

    @staticmethod
    def _abstention_text(*, draft: AnswerDraft, packet: QueryEvidencePacket) -> str:
        parts = ["I could not verify a grounded answer from the current graph evidence."]
        if packet.support_status:
            parts.append(f"Support status: {packet.support_status} ({packet.support_reason or 'unspecified'}).")
        if packet.missing_slots:
            parts.append(f"Missing slots: {', '.join(packet.missing_slots)}.")
        if draft.cited_facts:
            parts.append(f"Closest evidence: {draft.cited_facts[0]}")
        return " ".join(parts)

    @staticmethod
    def _cited_facts(packet: QueryEvidencePacket) -> Tuple[str, ...]:
        facts: List[str] = []
        supporting_fact = str(packet.slot_fills.get("supporting_fact") or "").strip()
        if supporting_fact:
            facts.append(supporting_fact)
        for triple in packet.selected_triples[:3]:
            source = str(triple.get("source") or "").strip()
            relation = str(triple.get("relation") or "").strip()
            target = str(triple.get("target") or "").strip()
            if source and relation and target:
                facts.append(f"{source} {relation} {target}")
        deduped: List[str] = []
        seen = set()
        for fact in facts:
            key = fact.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(fact)
        return tuple(deduped)


class AnswerGuardrailAgent:
    """Deterministic evidence/ontology guardrail for Graph-CoT answers."""

    def review(
        self,
        *,
        question_frame: GraphCoTQuestionFrame,
        packet: QueryEvidencePacket,
        draft: AnswerDraft,
    ) -> GuardrailVerdict:
        answer_text = draft.answer_text.lower()
        hard_findings: List[GuardrailFinding] = []
        soft_findings: List[GuardrailFinding] = []
        required_repairs: List[str] = []
        supported_claims: List[str] = []
        unsupported_claims: List[str] = []

        mismatch = dict(packet.ontology_context_mismatch or question_frame.ontology_context_mismatch or {})
        if mismatch.get("mismatch") and "ontology context warning" not in answer_text:
            hard_findings.append(
                GuardrailFinding(
                    code="ontology_violation",
                    severity="hard",
                    message=str(mismatch.get("warning") or "Active ontology context differs from indexed graph context."),
                    repair_hint="Add an ontology drift caveat.",
                )
            )
            required_repairs.append("Add an ontology drift caveat.")

        if draft.unresolved_entities and "unresolved entities:" not in answer_text and not draft.abstain:
            hard_findings.append(
                GuardrailFinding(
                    code="entity_ambiguity",
                    severity="hard",
                    message="The question still contains unresolved entity mentions.",
                    repair_hint="Name unresolved entities and keep the answer partial.",
                )
            )
            required_repairs.append("Name unresolved entities and keep the answer partial.")

        if packet.support_status == "unsupported" and not draft.abstain:
            hard_findings.append(
                GuardrailFinding(
                    code="unsupported_claim",
                    severity="hard",
                    message="The evidence packet does not support a direct answer.",
                    repair_hint="Abstain because the evidence packet does not support the requested claim.",
                )
            )
            required_repairs.append("Abstain because the evidence packet does not support the requested claim.")

        if (
            packet.support_status in {"partial", "unsupported"}
            and "i could not verify a grounded answer" not in answer_text
            and not packet.selected_triples
        ):
            hard_findings.append(
                GuardrailFinding(
                    code="unsupported_claim",
                    severity="hard",
                    message="The answer should explicitly abstain or downgrade because support remains partial.",
                    repair_hint="Abstain because the evidence packet does not support the requested claim.",
                )
            )
            required_repairs.append("Abstain because the evidence packet does not support the requested claim.")

        if packet.missing_slots and "missing slots:" not in answer_text and not draft.abstain:
            hard_findings.append(
                GuardrailFinding(
                    code="unsupported_claim",
                    severity="hard",
                    message="The answer hides missing required slots.",
                    repair_hint="State missing slots explicitly.",
                )
            )
            required_repairs.append("State missing slots explicitly.")

        packet_years = self._years_in_packet(packet)
        answer_years = self._years_in_text(draft.answer_text)
        if answer_years and packet_years and not answer_years.issubset(packet_years):
            hard_findings.append(
                GuardrailFinding(
                    code="temporal_mismatch",
                    severity="hard",
                    message="The answer uses time references not present in the supporting evidence.",
                    repair_hint="Avoid unsupported time references.",
                )
            )
            required_repairs.append("Avoid unsupported time references.")

        if packet.query_diagnostics and packet.support_status != "supported":
            soft_findings.append(
                GuardrailFinding(
                    code="epistemic_suspicion",
                    severity="soft",
                    message="Query diagnostics indicate the retrieval path was unstable or partial.",
                    repair_hint="Prefer partial wording or abstention.",
                )
            )

        if hard_findings:
            unsupported_claims.append(draft.answer_text)
        elif draft.answer_text:
            supported_claims.append(draft.answer_text)

        if any(
            finding.code == "unsupported_claim"
            and "Abstain because the evidence packet does not support the requested claim." in required_repairs
            for finding in hard_findings
        ):
            decision: str = "revise"
            summary = "The answer is not sufficiently grounded and should abstain."
        elif hard_findings:
            decision = "revise"
            summary = "The answer requires repair before it can be returned."
        else:
            decision = "pass"
            summary = "The answer is consistent with the current evidence and guardrails."

        return GuardrailVerdict(
            decision=decision,  # type: ignore[arg-type]
            summary=summary,
            supported_claims=tuple(supported_claims),
            unsupported_claims=tuple(unsupported_claims),
            hard_findings=tuple(hard_findings),
            soft_findings=tuple(soft_findings),
            required_repairs=tuple(dict.fromkeys(required_repairs)),
            ontology_consistent=not bool(mismatch.get("mismatch")),
            suspicious=bool(soft_findings),
        )

    @staticmethod
    def _years_in_packet(packet: QueryEvidencePacket) -> set[str]:
        values: List[str] = []
        supporting_fact = str(packet.slot_fills.get("supporting_fact") or "").strip()
        if supporting_fact:
            values.append(supporting_fact)
        values.extend(str(item) for item in packet.records)
        return AnswerGuardrailAgent._years_in_text(" ".join(values))

    @staticmethod
    def _years_in_text(text: str) -> set[str]:
        return {match.group(0) for match in _YEAR_RE.finditer(text or "")}


class GraphCoTQueryOrchestrator:
    """Coordinator for the query-time Graph-CoT lane."""

    def __init__(self, *, lpg_agent: Any, rdf_agent: Any, answer_agent: Any) -> None:
        self.supervisor = QuerySupervisorAgent()
        self.text2cypher = Text2CypherAgent(lpg_agent=lpg_agent, rdf_agent=rdf_agent)
        self.answer_generation = GraphCoTAnswerGenerationAgent(base_answer_agent=answer_agent)
        self.guardrail = AnswerGuardrailAgent()


__all__ = [
    "AnswerGuardrailAgent",
    "GraphCoTAnswerGenerationAgent",
    "GraphCoTQueryOrchestrator",
    "GraphCoTRetrievalResult",
    "QuerySupervisorAgent",
    "Text2CypherAgent",
]
