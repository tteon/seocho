from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from .intent import build_evidence_bundle, infer_question_intent


_FOUR_DIGIT_YEAR_RE = re.compile(r"\b(20\d{2})\b")


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
        if intent in {"financial_metric_lookup", "financial_metric_delta"}:
            return self._build_financial_answer(question, records, intent_data)
        if intent == "relationship_lookup":
            return self._build_relationship_answer(question, records, intent_data)
        if intent in {"neighbors", "entity_lookup"}:
            return self._build_supporting_fact_answer(question, records)
        return None

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
        supporting_fact = self._supporting_fact(records)
        direct_answer = self._direct_answer(question, supporting_fact)
        if direct_answer:
            return direct_answer

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
        if "shareholder return" in metric_label.lower():
            return (
                f"For {company}, total shareholder return including dividends was approximately "
                f"{self._format_financial_number(best_row['value'])}%{year_suffix}."
            )
        return f"For {company}, {metric_label} was ${self._format_financial_number(best_row['value'])}{year_suffix}."

    def _build_relationship_answer(
        self,
        question: str,
        records: Sequence[Dict[str, Any]],
        intent_data: Dict[str, Any],
    ) -> Optional[str]:
        supporting_fact = self._supporting_fact(records)
        direct_answer = self._direct_answer(question, supporting_fact)
        if direct_answer:
            return direct_answer

        rows = self._normalize_relationship_rows(records)
        if not rows:
            return None

        relationship_type = str(intent_data.get("relationship_type", "")).strip().upper()
        if relationship_type == "EMPLOYS":
            source = rows[0].get("source", "")
            people = []
            for row in rows:
                target = row.get("target", "")
                title = row.get("title", "")
                if title:
                    people.append(f"{target} as {title}")
                elif target:
                    people.append(target)
            people = [person for person in people if person]
            if people:
                return f"The key executives at {source} include {', '.join(people)}."

        if relationship_type == "INVOLVED_IN":
            source = rows[0].get("source", "")
            issues = self._unique_targets(rows)
            if issues:
                return f"{source} faces {self._join_items(issues)}."

        if relationship_type == "USES_STANDARD":
            source = rows[0].get("source", "")
            standards = [row.get("target", "") for row in rows if row.get("target")]
            if standards:
                return f"{source} follows the accounting standards {', '.join(standards)}."
        return None

    def _build_supporting_fact_answer(
        self,
        question: str,
        records: Sequence[Dict[str, Any]],
    ) -> Optional[str]:
        supporting_fact = self._supporting_fact(records)
        direct_answer = self._direct_answer(question, supporting_fact)
        return direct_answer or None

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
                    "supporting_fact": str(self._record_value(record, "supporting_fact", 5)).strip(),
                }
            )
        return rows

    def _normalize_relationship_rows(self, records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for record in records:
            source = str(self._record_value(record, "source", 0)).strip()
            relationship = str(self._record_value(record, "relationship", 1)).strip()
            target = str(self._record_value(record, "target", 2)).strip()
            if not source or not relationship or not target:
                continue
            target_properties = self._record_value(record, "target_properties", 4)
            title = ""
            if isinstance(target_properties, dict):
                title = str(target_properties.get("title", "")).strip()
            rows.append(
                {
                    "source": source,
                    "relationship": relationship,
                    "target": target,
                    "title": title,
                    "supporting_fact": str(self._record_value(record, "supporting_fact", 5)).strip(),
                }
            )
        return rows

    def _unique_targets(self, rows: Sequence[Dict[str, Any]]) -> List[str]:
        ordered: List[str] = []
        for row in rows:
            target = str(row.get("target", "")).strip()
            if target and target not in ordered:
                ordered.append(target)
        return ordered

    def _join_items(self, items: Sequence[str]) -> str:
        values = [str(item).strip() for item in items if str(item).strip()]
        if not values:
            return ""
        if len(values) == 1:
            return values[0]
        if len(values) == 2:
            return f"{values[0]} and {values[1]}"
        return ", ".join(values[:-1]) + f", and {values[-1]}"

    def _supporting_fact(self, records: Sequence[Dict[str, Any]]) -> str:
        for record in records:
            for key, index in (
                ("supporting_fact", 5),
                ("properties", 1),
                ("target_properties", 4),
            ):
                value = self._record_value(record, key, index)
                if isinstance(value, dict):
                    for prop_key in ("content_preview", "description", "content"):
                        fact = str(value.get(prop_key, "")).strip()
                        if fact:
                            return fact
                else:
                    fact = str(value or "").strip()
                    if fact:
                        return fact
        return ""

    def _direct_answer(self, question: str, supporting_fact: str) -> str:
        if not supporting_fact:
            return ""
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", supporting_fact)
            if sentence.strip()
        ]
        if not sentences:
            return supporting_fact

        question_terms = {
            token
            for token in re.findall(r"[a-z0-9]+", question.lower())
            if len(token) > 1
        }
        if not question_terms:
            return sentences[0]

        if "executive" in question_terms or "executives" in question_terms:
            question_terms.update({"ceo", "cfo", "chairman", "board", "director", "officer"})
        if "legal" in question_terms or "issues" in question_terms or "issue" in question_terms:
            question_terms.update(
                {
                    "investigation",
                    "investigations",
                    "litigation",
                    "claim",
                    "claims",
                    "proceeding",
                    "proceedings",
                    "antitrust",
                    "patent",
                    "bundling",
                }
            )

        def score(sentence: str) -> tuple[int, int]:
            terms = set(re.findall(r"[a-z0-9]+", sentence.lower()))
            numeric_hits = len(set(re.findall(r"\d+(?:\.\d+)?", sentence.lower())) & question_terms)
            return (len(terms & question_terms), numeric_hits, len(sentence))

        scored_sentences = [(index, sentence, score(sentence)) for index, sentence in enumerate(sentences)]
        if "legal" in question_terms or "issues" in question_terms or "issue" in question_terms:
            relevant = [
                (index, sentence)
                for index, sentence, sentence_score in scored_sentences
                if sentence_score[0] >= 2 and len(sentence) >= 24
            ]
            if len(relevant) >= 2:
                return " ".join(sentence for _, sentence in sorted(relevant, key=lambda item: item[0]))

        best_sentence = max(scored_sentences, key=lambda item: item[2])[1]
        best_score = score(best_sentence)
        if best_score[0] < 2 or len(best_sentence) < 24:
            return supporting_fact
        return best_sentence

    @staticmethod
    def _record_value(record: Dict[str, Any], key: str, fallback_index: int) -> Any:
        if key in record:
            return record.get(key)
        return record.get(f"col_{fallback_index}")

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
