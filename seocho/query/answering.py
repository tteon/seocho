from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set


_FOUR_DIGIT_YEAR_RE = re.compile(r"\b(20\d{2})\b")


@dataclass(frozen=True)
class IntentSpec:
    intent_id: str
    required_relations: tuple[str, ...]
    required_entity_types: tuple[str, ...]
    focus_slots: tuple[str, ...]
    trigger_keywords: tuple[str, ...]


INTENT_CATALOG: tuple[IntentSpec, ...] = (
    IntentSpec(
        intent_id="relationship_lookup",
        required_relations=("RELATES_TO", "USES", "OWNS", "WORKS_WITH"),
        required_entity_types=("Entity",),
        focus_slots=("source_entity", "target_entity", "relation_paths"),
        trigger_keywords=("relation", "relationship", "related", "connected", "connection", "link", "between"),
    ),
    IntentSpec(
        intent_id="responsibility_lookup",
        required_relations=("MANAGES", "OWNS", "LEADS", "OPERATES"),
        required_entity_types=("Person", "Organization"),
        focus_slots=("owner_or_operator", "target_entity", "supporting_fact"),
        trigger_keywords=("who manages", "manages", "owner", "owns", "owned", "leads", "lead", "responsible", "operates"),
    ),
    IntentSpec(
        intent_id="explanation_lookup",
        required_relations=(),
        required_entity_types=("Entity",),
        focus_slots=("target_entity", "supporting_fact"),
        trigger_keywords=("why", "how", "explain"),
    ),
    IntentSpec(
        intent_id="entity_summary",
        required_relations=(),
        required_entity_types=("Entity",),
        focus_slots=("target_entity", "supporting_fact"),
        trigger_keywords=(),
    ),
)


def infer_question_intent(question: str, entities: Sequence[str]) -> Dict[str, Any]:
    normalized = question.lower()
    best_spec = INTENT_CATALOG[-1]
    best_score = 0
    matched_keywords: List[str] = []

    for spec in INTENT_CATALOG:
        keywords = [keyword for keyword in spec.trigger_keywords if keyword and keyword in normalized]
        score = len(keywords)
        if score > best_score:
            best_spec = spec
            best_score = score
            matched_keywords = keywords

    return {
        "intent_id": best_spec.intent_id,
        "required_relations": list(best_spec.required_relations),
        "required_entity_types": list(best_spec.required_entity_types),
        "focus_slots": list(best_spec.focus_slots),
        "matched_keywords": matched_keywords,
        "candidate_entity_count": len([entity for entity in entities if str(entity).strip()]),
    }


def build_evidence_bundle(
    *,
    question: str,
    semantic_context: Dict[str, Any],
    memory: Optional[Dict[str, Any]] = None,
    matched_entities: Optional[Sequence[str]] = None,
    reasons: Optional[Sequence[str]] = None,
    score: Optional[float] = None,
    support_assessment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    intent = semantic_context.get("intent")
    if not isinstance(intent, dict) or not intent.get("intent_id"):
        intent = infer_question_intent(question, semantic_context.get("entities", []))

    matched_entity_names = [
        str(entity).strip()
        for entity in (matched_entities or [])
        if str(entity).strip()
    ]
    matched_entity_set = {entity.lower() for entity in matched_entity_names}

    candidate_entities: List[Dict[str, Any]] = []
    for question_entity, candidates in semantic_context.get("matches", {}).items():
        if not candidates:
            continue
        best = candidates[0]
        display_name = str(best.get("display_name") or question_entity).strip()
        if matched_entity_set and question_entity.lower() not in matched_entity_set and display_name.lower() not in matched_entity_set:
            continue
        candidate_entities.append(
            {
                "question_entity": question_entity,
                "display_name": display_name,
                "database": str(best.get("database", "")).strip(),
                "node_id": str(best.get("node_id", "")).strip(),
                "labels": list(best.get("labels", [])) if isinstance(best.get("labels"), list) else [],
                "source": str(best.get("source", "")).strip(),
                "confidence": float(best.get("final_score", 0.0) or 0.0),
            }
        )

    memory_payload = memory if isinstance(memory, dict) else {}
    memory_entities = memory_payload.get("entities", []) if isinstance(memory_payload.get("entities"), list) else []
    prioritized_memory_entities = sorted(
        memory_entities,
        key=lambda entity: 0 if _entity_name(entity).lower() in matched_entity_set else 1,
    )

    selected_triples: List[Dict[str, Any]] = []
    for entity in prioritized_memory_entities[:5]:
        entity_name = _entity_name(entity)
        if not entity_name:
            continue
        selected_triples.append(
            {
                "source": str(memory_payload.get("memory_id", "")).strip(),
                "relation": "MENTIONS",
                "target": entity_name,
                "target_labels": list(entity.get("labels", [])) if isinstance(entity.get("labels"), list) else [],
            }
        )

    slot_fills: Dict[str, Any] = {}
    focus_slots = [str(slot).strip() for slot in intent.get("focus_slots", []) if str(slot).strip()]
    if matched_entity_names:
        slot_fills["target_entity"] = matched_entity_names[0]
    elif candidate_entities:
        slot_fills["target_entity"] = candidate_entities[0]["display_name"]

    if len(matched_entity_names) > 1:
        slot_fills["source_entity"] = matched_entity_names[0]
        slot_fills["target_entity"] = matched_entity_names[1]

    if "relation_paths" in focus_slots and selected_triples:
        slot_fills["relation_paths"] = [triple["relation"] for triple in selected_triples]

    labeled_owner = _first_entity_with_labels(prioritized_memory_entities, {"person", "organization", "company"})
    if labeled_owner and "owner_or_operator" in focus_slots:
        slot_fills["owner_or_operator"] = labeled_owner

    preview = str(memory_payload.get("content_preview") or memory_payload.get("content") or "").strip()
    if preview and "supporting_fact" in focus_slots:
        slot_fills["supporting_fact"] = preview

    missing_slots = [slot for slot in focus_slots if slot not in slot_fills]
    grounded_slots = [slot for slot in focus_slots if slot in slot_fills]
    coverage = round(len(grounded_slots) / max(1, len(focus_slots)), 4) if focus_slots else 1.0

    confidence = 0.0
    if score is not None:
        confidence = float(score or 0.0)
    elif candidate_entities:
        confidence = max(float(entity.get("confidence", 0.0) or 0.0) for entity in candidate_entities)

    provenance: List[Dict[str, Any]] = []
    if memory_payload:
        provenance.append(
            {
                "memory_id": str(memory_payload.get("memory_id", "")).strip(),
                "database": str(memory_payload.get("database", "")).strip(),
                "content_preview": preview,
                "reasons": [str(reason).strip() for reason in (reasons or []) if str(reason).strip()],
            }
        )
    else:
        for entity in candidate_entities[:3]:
            provenance.append(
                {
                    "database": entity["database"],
                    "node_id": entity["node_id"],
                    "display_name": entity["display_name"],
                    "source": entity["source"],
                }
            )

    return {
        "schema_version": "evidence_bundle.v2",
        "intent_id": str(intent.get("intent_id", "")).strip(),
        "required_relations": list(intent.get("required_relations", [])),
        "required_entity_types": list(intent.get("required_entity_types", [])),
        "focus_slots": focus_slots,
        "candidate_entities": candidate_entities,
        "selected_triples": selected_triples,
        "slot_fills": slot_fills,
        "grounded_slots": grounded_slots,
        "missing_slots": missing_slots,
        "provenance": provenance,
        "confidence": round(confidence, 4),
        "coverage": coverage,
        "support_assessment": dict(support_assessment or {}),
    }


class QueryAnswerSynthesizer:
    """Canonical answer synthesis for local deterministic query execution."""

    def __init__(self, *, query_strategy: Any, llm: Any) -> None:
        self.query_strategy = query_strategy
        self.llm = llm

    def build_deterministic_answer(
        self,
        question: str,
        records: Sequence[Dict[str, Any]],
        intent_data: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not intent_data:
            return None
        intent = str(intent_data.get("intent", "")).strip()
        if intent not in {"financial_metric_lookup", "financial_metric_delta"}:
            return None
        return self._build_financial_answer(question, records, intent_data)

    def synthesize(
        self,
        question: str,
        records: Sequence[Dict[str, Any]],
        *,
        reasoning_trace: Optional[str] = None,
        vector_context: str = "",
    ) -> str:
        system_ans, user_ans = self.query_strategy.render_answer(
            question,
            json.dumps(records, default=str),
        )
        if reasoning_trace:
            user_ans += f"\n\nReasoning trace (query attempts):\n{reasoning_trace}"
        if vector_context:
            user_ans += f"\n\nAdditional context from vector search:\n{vector_context}"
        return self.llm.complete(system=system_ans, user=user_ans, temperature=0.1).text

    def _build_financial_answer(
        self,
        question: str,
        records: Sequence[Dict[str, Any]],
        intent_data: Dict[str, Any],
    ) -> Optional[str]:
        years = [str(year) for year in intent_data.get("years", []) if str(year).strip()]
        rows = self._normalize_financial_rows(records)
        if not rows:
            return None

        selected_rows = self._select_financial_rows(rows, intent_data)
        if not selected_rows:
            return None

        intent = str(intent_data.get("intent", ""))
        metric_label = self._humanize_metric_label(intent_data)
        company = selected_rows[0].get("company", "")

        if intent == "financial_metric_delta":
            target_years = self._ordered_years(years or [row["year"] for row in selected_rows])
            if len(target_years) < 2:
                return None
            start_year, end_year = target_years[0], target_years[-1]
            by_year = {row["year"]: row for row in selected_rows if row.get("year")}
            start_row = by_year.get(start_year)
            end_row = by_year.get(end_year)
            if not start_row or not end_row:
                available = ", ".join(sorted(by_year.keys()))
                return (
                    f"I found related {metric_label} evidence for {company or 'the company'}, "
                    f"but not enough period coverage to compare {start_year} and {end_year}. "
                    f"Available years: {available or 'none'}."
                )

            delta = round(end_row["value"] - start_row["value"], 3)
            direction = "increased" if delta > 0 else "decreased" if delta < 0 else "was flat"
            delta_abs = self._format_financial_number(abs(delta))
            start_value = self._format_financial_number(start_row["value"])
            end_value = self._format_financial_number(end_row["value"])
            if direction == "was flat":
                return (
                    f"For {company}, {metric_label} was flat from {start_year} to {end_year} "
                    f"at ${end_value}."
                )
            return (
                f"For {company}, {metric_label} {direction} by ${delta_abs} from {start_year} to {end_year}, "
                f"calculated as ${end_value} minus ${start_value}."
            )

        best_row = selected_rows[-1]
        year_suffix = f" in {best_row['year']}" if best_row.get("year") else ""
        return f"For {company}, {metric_label} was ${self._format_financial_number(best_row['value'])}{year_suffix}."

    def _normalize_financial_rows(self, records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, float, str]] = set()
        for record in records:
            if "metric_name" not in record or "value" not in record:
                continue
            value = self._coerce_number(record.get("value"))
            if value is None:
                continue
            year = self._coerce_year(record.get("year"), record.get("metric_name"), record.get("company"))
            company = str(record.get("company", "")).strip()
            metric_name = str(record.get("metric_name", "")).strip()
            key = (company, year, value, metric_name)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "company": company,
                    "metric_name": metric_name,
                    "year": year,
                    "value": value,
                    "relationship": str(record.get("relationship", "")),
                }
            )
        return rows

    def _select_financial_rows(
        self,
        rows: Sequence[Dict[str, Any]],
        intent_data: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        anchor = str(intent_data.get("anchor_entity", "")).strip()
        target_years = self._ordered_years(intent_data.get("years", []))
        metric_aliases = [str(alias).lower() for alias in intent_data.get("metric_aliases", [])]
        scope_tokens = [str(token).lower() for token in intent_data.get("metric_scope_tokens", [])]

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row.get("company", ""), []).append(row)

        best_company = ""
        best_score = -1
        for company, company_rows in grouped.items():
            years_present = {row.get("year", "") for row in company_rows if row.get("year")}
            metric_hits = 0
            for row in company_rows:
                text = str(row.get("metric_name", "")).lower()
                metric_hits += sum(1 for token in scope_tokens if token in text)
                metric_hits += sum(1 for alias in metric_aliases if alias in text)
            coverage = sum(1 for year in target_years if year in years_present)
            company_score = coverage * 10 + metric_hits + self._company_match_score(company, anchor)
            if company_score > best_score:
                best_score = company_score
                best_company = company

        selected = grouped.get(best_company, list(rows))
        if not target_years:
            return list(selected)

        best_by_year: Dict[str, Dict[str, Any]] = {}
        for row in selected:
            year = row.get("year", "")
            if not year:
                continue
            score = self._row_match_score(row, anchor, metric_aliases, scope_tokens)
            current = best_by_year.get(year)
            if current is None or score > self._row_match_score(current, anchor, metric_aliases, scope_tokens):
                best_by_year[year] = row
        return [best_by_year[year] for year in target_years if year in best_by_year]

    def _row_match_score(
        self,
        row: Dict[str, Any],
        anchor: str,
        metric_aliases: Sequence[str],
        scope_tokens: Sequence[str],
    ) -> int:
        score = self._company_match_score(str(row.get("company", "")), anchor)
        metric_text = str(row.get("metric_name", "")).lower()
        score += sum(3 for token in scope_tokens if token in metric_text)
        score += sum(1 for alias in metric_aliases if alias in metric_text)
        if str(row.get("relationship", "")) in {"REPORTED", "reported"}:
            score += 2
        return score

    def _company_match_score(self, company: str, anchor: str) -> int:
        if not anchor:
            return 0
        company_norm = re.sub(r"[^a-z0-9]+", " ", company.lower())
        anchor_norm = re.sub(r"[^a-z0-9]+", " ", anchor.lower())
        anchor_tokens = [token for token in anchor_norm.split() if token]
        return sum(2 for token in anchor_tokens if token in company_norm)

    def _coerce_number(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _coerce_year(self, raw_year: Any, *fallback_fields: Any) -> str:
        text = str(raw_year).strip()
        if text and text.lower() != "none" and len(text) == 4 and text.isdigit():
            return text
        for field in fallback_fields:
            match = _FOUR_DIGIT_YEAR_RE.search(str(field))
            if match:
                return match.group(1)
        return ""

    def _ordered_years(self, years: Sequence[Any]) -> List[str]:
        deduped: List[str] = []
        for year in years:
            text = str(year).strip()
            if text and text not in deduped:
                deduped.append(text)
        return sorted(deduped)

    def _humanize_metric_label(self, intent_data: Dict[str, Any]) -> str:
        metric_name = str(intent_data.get("metric_name", "")).strip()
        scope_tokens = [str(token) for token in intent_data.get("metric_scope_tokens", []) if str(token)]
        metric_aliases = [str(alias) for alias in intent_data.get("metric_aliases", []) if str(alias)]
        if metric_name:
            return metric_name.replace("&", "and")
        if scope_tokens and metric_aliases:
            return f"{' '.join(scope_tokens)} {metric_aliases[0]}".strip()
        if metric_aliases:
            return metric_aliases[0]
        return "financial metric"

    def _format_financial_number(self, value: float) -> str:
        return f"{value:,.1f}".rstrip("0").rstrip(".")


def _entity_name(payload: Dict[str, Any]) -> str:
    return str(payload.get("name") or payload.get("display_name") or "").strip()


def _first_entity_with_labels(entities: Sequence[Dict[str, Any]], normalized_targets: Set[str]) -> str:
    for entity in entities:
        labels = entity.get("labels", [])
        if not isinstance(labels, list):
            continue
        normalized_labels = {
            re.sub(r"[^a-z0-9]+", "", str(label).lower())
            for label in labels
        }
        if normalized_labels & {re.sub(r"[^a-z0-9]+", "", item.lower()) for item in normalized_targets}:
            entity_name = _entity_name(entity)
            if entity_name:
                return entity_name
    return ""
