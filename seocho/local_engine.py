from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from .models import Memory
from .query.answering import QueryAnswerSynthesizer
from .query.contracts import QueryPlan
from .query.executor import GraphQueryExecutor
from .query.planner import DeterministicQueryPlanner

logger = logging.getLogger(__name__)
_FOUR_DIGIT_YEAR_RE = re.compile(r"\b(20\d{2})\b")


class _LocalEngine:
    """Internal orchestrator for local engine mode.

    Wires together Ontology -> IndexingPipeline -> QueryStrategy -> GraphStore.
    """

    def __init__(
        self,
        *,
        ontology: Any,  # Ontology
        graph_store: Any,  # GraphStore
        llm: Any,  # LLMBackend
        workspace_id: str,
        extraction_prompt: Optional[Any] = None,  # PromptTemplate
        agent_config: Optional[Any] = None,  # AgentConfig
        ontology_profile: str = "default",
    ) -> None:
        from .agent_config import AgentConfig
        from .events import NullEventPublisher
        from .index.ingestion_facade import IngestRequest, IngestionFacade
        from .indexing import IndexingPipeline
        from .ontology import Ontology
        from .prompt_strategy import ExtractionStrategy, LinkingStrategy, QueryStrategy

        self.ontology: Ontology = ontology
        self.graph_store = graph_store
        self.llm = llm
        self.workspace_id = workspace_id
        self.agent_config: AgentConfig = agent_config or AgentConfig()
        self.extraction_prompt = extraction_prompt
        self.ontology_profile = str(ontology_profile or "default")

        from .ontology_context import OntologyContextCache

        self._ontology_context_cache = OntologyContextCache()
        self._last_query_metadata: Dict[str, Any] = {}
        self._events = NullEventPublisher()
        self._ingest_request_cls = IngestRequest

        # Resolve embedding backend from the LLM if the provider supports it.
        embedding_backend = None
        if hasattr(llm, "embed") and getattr(getattr(llm, "provider_spec", None), "supports_embeddings", False):
            embedding_backend = llm

        # Indexing pipeline (handles chunking, extraction, validation, dedup, write).
        self._indexing = IndexingPipeline(
            ontology=ontology,
            graph_store=graph_store,
            llm=llm,
            workspace_id=workspace_id,
            extraction_prompt=extraction_prompt,
            enable_rule_constraints=True,
            embedding_backend=embedding_backend,
            ontology_profile=self.ontology_profile,
            ontology_context_cache=self._ontology_context_cache,
        )
        self._indexing._quality_threshold = self.agent_config.extraction_quality_threshold
        self._indexing._max_retries = self.agent_config.extraction_max_retries
        self._ingestion = IngestionFacade(self._indexing, publisher=self._events)

        # Pre-build strategies (for extract-only and query).
        self._extraction = ExtractionStrategy(ontology, extraction_prompt=extraction_prompt)
        self._linking = LinkingStrategy(ontology)
        self._query = QueryStrategy(ontology)

    def add(
        self,
        content: str,
        *,
        database: str = "neo4j",
        category: str = "memory",
        metadata: Optional[Dict[str, Any]] = None,
        strict_validation: bool = False,
        ontology_override: Optional[Any] = None,
    ) -> Memory:
        """Chunk -> Extract -> Validate -> Link -> Write pipeline."""
        if ontology_override is not None:
            from .index.ingestion_facade import IngestionFacade
            from .indexing import IndexingPipeline

            pipeline = IndexingPipeline(
                ontology=ontology_override,
                graph_store=self.graph_store,
                llm=self.llm,
                workspace_id=self.workspace_id,
                extraction_prompt=self.extraction_prompt,
                strict_validation=strict_validation,
                enable_rule_constraints=True,
                ontology_profile=self.ontology_profile,
                ontology_context_cache=self._ontology_context_cache,
            )
            ingestion = IngestionFacade(pipeline, publisher=self._events)
        else:
            ingestion = self._ingestion

        result = ingestion.ingest(
            self._ingest_request_cls(
                content=content,
                workspace_id=self.workspace_id,
                database=database,
                category=category,
                metadata=metadata,
                strict_validation=strict_validation,
            )
        )

        result_metadata: Dict[str, Any] = {
            "category": category,
            "nodes_created": result.total_nodes,
            "relationships_created": result.total_relationships,
            "chunks_processed": result.chunks_processed,
            "validation_errors": result.validation_errors,
            "write_errors": result.write_errors,
            "skipped_chunks": result.skipped_chunks,
            "deduplicated": result.deduplicated,
            **(metadata or {}),
        }
        if result.rule_profile is not None:
            result_metadata["rule_profile"] = result.rule_profile
        if result.rule_validation_summary is not None:
            result_metadata["rule_validation_summary"] = result.rule_validation_summary
        if result.semantic_artifacts is not None:
            result_metadata["semantic_artifacts"] = result.semantic_artifacts
        if result.ontology_context is not None:
            result_metadata["ontology_context"] = result.ontology_context
            result_metadata["ontology_context_hash"] = result.ontology_context.get("context_hash", "")
            result_metadata["ontology_profile"] = result.ontology_context.get("profile", self.ontology_profile)
        if result.fallback_used:
            result_metadata["fallback_used"] = True
            result_metadata["fallback_reason"] = result.fallback_reason

        return Memory(
            memory_id=result.source_id,
            workspace_id=self.workspace_id,
            content=content[:500],
            metadata=result_metadata,
            status="active" if result.ok else "failed",
            database=database,
            category=category,
            source_type="text",
        )

    def add_batch(
        self,
        documents: Sequence[str],
        *,
        database: str = "neo4j",
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
        strict_validation: bool = False,
        on_progress: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Index multiple documents with progress tracking."""
        self._indexing.strict_validation = strict_validation
        batch_result = self._indexing.index_batch(
            documents,
            database=database,
            category=category,
            metadata=metadata,
            on_document=on_progress,
        )
        return batch_result.to_dict()

    def extract(
        self,
        content: str,
        *,
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run extraction only (no graph write)."""
        self._extraction.category = category
        system, user = self._extraction.render(content, metadata=metadata)

        response = self.llm.complete(
            system=system,
            user=user,
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        try:
            result = response.json()
        except (json.JSONDecodeError, ValueError):
            logger.error("LLM returned non-JSON extraction response: %s", response.text[:200])
            result = {"nodes": [], "relationships": [], "_extraction_failed": True}

        if not result.get("nodes") and not result.get("relationships"):
            logger.warning("Extraction produced no entities or relationships from input text")

        return result

    def ask(
        self,
        question: str,
        *,
        database: str = "neo4j",
        reasoning_mode: Optional[bool] = None,
        repair_budget: Optional[int] = None,
        ontology_override: Optional[Any] = None,
    ) -> str:
        """Ontology-aware query: generate Cypher -> execute -> synthesize answer."""
        active_ontology = ontology_override or self.ontology
        ontology_context = self._ontology_context_cache.get(
            active_ontology,
            workspace_id=self.workspace_id,
            profile=self.ontology_profile,
        )
        if ontology_override is not None:
            from .prompt_strategy import QueryStrategy

            self._query = QueryStrategy(active_ontology)

        if reasoning_mode is None:
            reasoning_mode = self.agent_config.reasoning_mode
        if repair_budget is None:
            repair_budget = self.agent_config.repair_budget

        import time as _time

        _query_start = _time.time()

        schema_info = self._get_schema_info(database)
        self._query.schema_info = schema_info
        planner = DeterministicQueryPlanner(
            ontology=active_ontology,
            llm=self.llm,
            workspace_id=self.workspace_id,
        )
        executor = GraphQueryExecutor(graph_store=self.graph_store, database=database)
        answer_synthesizer = QueryAnswerSynthesizer(
            query_strategy=self._query,
            llm=self.llm,
        )

        cypher, params, intent_data, error = self._generate_cypher(
            question,
            active_ontology,
            planner=planner,
        )
        if error:
            return error

        records, exec_error = self._execute_cypher(
            cypher,
            params,
            database,
            executor=executor,
        )
        if exec_error:
            return exec_error

        if not records and intent_data.get("intent") in ("relationship_lookup", "entity_lookup"):
            from .query.cypher_builder import CypherBuilder

            fb_builder = CypherBuilder(active_ontology)
            fb_cypher, fb_params = fb_builder.build(
                intent="neighbors",
                anchor_entity=intent_data.get("anchor_entity", ""),
                anchor_label=intent_data.get("anchor_label", ""),
                workspace_id=self.workspace_id,
            )
            fb_records, _ = self._execute_cypher(fb_cypher, fb_params, database)
            if fb_records:
                records = fb_records
                cypher = fb_cypher

        attempts = []
        if reasoning_mode and repair_budget > 0 and not records:
            attempts.append({"cypher": cypher, "result_count": 0, "error": None})

            for _attempt_num in range(repair_budget):
                repair_cypher, repair_params, repair_error = self._generate_repair_query(
                    question,
                    attempts,
                    schema_info,
                    intent_data,
                    active_ontology,
                    planner=planner,
                )
                if repair_error or not repair_cypher:
                    break

                repair_records, repair_exec_error = self._execute_cypher(
                    repair_cypher,
                    repair_params,
                    database,
                    executor=executor,
                )
                attempts.append(
                    {
                        "cypher": repair_cypher,
                        "result_count": len(repair_records) if repair_records else 0,
                        "error": repair_exec_error,
                    }
                )

                if repair_records:
                    records = repair_records
                    cypher = repair_cypher
                    break

        vector_context = ""
        if not records and hasattr(self, "_vector_store") and self._vector_store is not None:
            try:
                vs = self._vector_store
                if hasattr(vs, "search"):
                    vresults = vs.search(question, limit=3)
                    if vresults:
                        vector_context = "\n".join(f"[Vector result] {r.text[:300]}" for r in vresults)
            except Exception:
                pass

        ontology_context_mismatch = self._query_ontology_context_mismatch(database, ontology_context)
        self._last_query_metadata = {
            "ontology_context": ontology_context.metadata(usage="query"),
            "ontology_context_mismatch": ontology_context_mismatch,
        }
        if ontology_context_mismatch.get("mismatch"):
            logger.warning(
                "Ontology context mismatch for database=%s active=%s indexed=%s",
                database,
                ontology_context_mismatch.get("active_context_hash", ""),
                ontology_context_mismatch.get("indexed_context_hashes", []),
            )

        deterministic_answer = self._build_deterministic_answer(
            question,
            records,
            intent_data,
            answer_synthesizer=answer_synthesizer,
        )
        if deterministic_answer:
            _query_elapsed = _time.time() - _query_start
            self._log_query_trace(
                question=question,
                ontology=active_ontology,
                cypher=cypher,
                result_count=len(records) if records else 0,
                reasoning_attempts=len(attempts) if reasoning_mode and attempts else 0,
                elapsed_seconds=_query_elapsed,
            )
            return deterministic_answer

        reasoning_trace = None
        if reasoning_mode and attempts:
            reasoning_trace = json.dumps(attempts, default=str)

        answer_text = answer_synthesizer.synthesize(
            question,
            records,
            reasoning_trace=reasoning_trace,
            vector_context=vector_context,
        )

        _query_elapsed = _time.time() - _query_start
        self._log_query_trace(
            question=question,
            ontology=active_ontology,
            cypher=cypher,
            result_count=len(records) if records else 0,
            reasoning_attempts=len(attempts) if reasoning_mode and attempts else 0,
            elapsed_seconds=_query_elapsed,
        )

        return answer_text

    def _log_query_trace(
        self,
        *,
        question: str,
        ontology: Any,
        cypher: str,
        result_count: int,
        reasoning_attempts: int,
        elapsed_seconds: float,
    ) -> None:
        try:
            from .tracing import is_tracing_enabled, log_query

            if is_tracing_enabled():
                log_query(
                    question=question,
                    ontology_name=ontology.name,
                    ontology_package=getattr(ontology, "package_id", ontology.name),
                    model=getattr(self.llm, "model", "unknown"),
                    cypher=cypher,
                    result_count=result_count,
                    reasoning_attempts=reasoning_attempts,
                    elapsed_seconds=elapsed_seconds,
                    metadata=self._last_query_metadata,
                )
        except Exception:
            pass

    def _query_ontology_context_mismatch(self, database: str, ontology_context: Any) -> Dict[str, Any]:
        from .ontology_context import query_ontology_context_mismatch

        return query_ontology_context_mismatch(
            self.graph_store,
            ontology_context,
            workspace_id=self.workspace_id,
            database=database,
        )

    def _get_schema_info(self, database: str) -> Dict[str, Any]:
        try:
            schema = self.graph_store.get_schema(database=database)
            return {
                "node_labels": ", ".join(schema.get("labels", [])),
                "relationship_types": ", ".join(schema.get("relationship_types", [])),
            }
        except Exception:
            return {}

    def _generate_cypher(
        self,
        question: str,
        ontology: Any,
        *,
        planner: Optional[DeterministicQueryPlanner] = None,
    ) -> tuple:
        active_planner = planner or DeterministicQueryPlanner(
            ontology=ontology,
            llm=self.llm,
            workspace_id=self.workspace_id,
        )
        plan = active_planner.plan(question)
        return plan.cypher, plan.params, plan.intent_data, plan.error

    def _execute_cypher(
        self,
        cypher: str,
        params: Dict,
        database: str,
        *,
        executor: Optional[GraphQueryExecutor] = None,
    ) -> tuple:
        active_executor = executor or GraphQueryExecutor(
            graph_store=self.graph_store,
            database=database,
        )
        execution = active_executor.execute(QueryPlan(question="", cypher=cypher, params=params))
        return execution.records, execution.error

    def _generate_repair_query(
        self,
        question: str,
        attempts: List[Dict],
        schema_info: Dict[str, Any],
        intent_data: Optional[Dict[str, Any]] = None,
        ontology: Optional[Any] = None,
        *,
        planner: Optional[DeterministicQueryPlanner] = None,
    ) -> tuple:
        active_planner = planner or DeterministicQueryPlanner(
            ontology=ontology or self.ontology,
            llm=self.llm,
            workspace_id=self.workspace_id,
        )
        plan = active_planner.repair(
            question=question,
            attempts=attempts,
            intent_data=intent_data,
            ontology=ontology,
        )
        return plan.cypher, plan.params, plan.error

    def _build_deterministic_answer(
        self,
        question: str,
        records: Sequence[Dict[str, Any]],
        intent_data: Optional[Dict[str, Any]],
        *,
        answer_synthesizer: Optional[QueryAnswerSynthesizer] = None,
    ) -> Optional[str]:
        active_answer_synthesizer = answer_synthesizer or QueryAnswerSynthesizer(
            query_strategy=self._query,
            llm=self.llm,
        )
        return active_answer_synthesizer.build_deterministic_answer(
            question,
            records,
            intent_data,
        )

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
        if text and text.lower() != "none":
            if len(text) == 4 and text.isdigit():
                return text
        for field in fallback_fields:
            match = _FOUR_DIGIT_YEAR_RE.search(str(field))
            if match:
                return match.group(1)
        return ""

    def _ordered_years(self, years: Sequence[Any]) -> List[str]:
        deduped = []
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

    def _link(
        self,
        nodes: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        *,
        category: str = "general",
    ) -> Dict[str, Any]:
        """Run entity linking/dedup."""
        self._linking.category = category
        entities_json = json.dumps({"nodes": nodes, "relationships": relationships}, default=str)
        system, user = self._linking.render(entities_json)

        response = self.llm.complete(
            system=system,
            user=user,
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return {"nodes": nodes, "relationships": relationships}
