#!/usr/bin/env python3
"""EnterpriseRAG-Bench context graph + database federation harness.

This script is intentionally split into retrieval/federation plumbing before
expensive answer generation. It normalizes EnterpriseRAG-Bench questions and a
source-organized document corpus into a reproducible SEOCHO experiment plan:

* source-scoped databases, one per enterprise system/source type
* ontology/context-graph guardrail profile used to select relation cues
* retrieval-only answer JSONL compatible with EnterpriseRAG-Bench evaluation
* local smoke metrics for source routing and document recall

Use the bundled smoke fixture for CI and replace the paths with the real
EnterpriseRAG-Bench release data for larger runs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from seocho.observability import StageTimer


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOP_K = 10
DEFAULT_WORKSPACE_ID = "enterprise_rag_contextgraph_federation"
DSID_RE = re.compile(r"(dsid_[A-Za-z0-9]+)")


SOURCE_ONTOLOGY: dict[str, dict[str, Any]] = {
    "github": {
        "database": "erbgithub",
        "entities": ("Repository", "PullRequest", "Commit", "Reviewer", "Metric"),
        "relations": ("AUTHORED", "REVIEWED_BY", "MERGED_INTO", "ADDS_METRIC", "CHANGES_CODE"),
        "process_cues": ("author", "reviewer", "merged", "branch", "ci", "metric", "release"),
    },
    "jira": {
        "database": "erbjira",
        "entities": ("Ticket", "Assignee", "Priority", "Incident", "Status"),
        "relations": ("ASSIGNED_TO", "BLOCKED_BY", "RESOLVED_BY", "HAS_PRIORITY", "AFFECTS_ENVIRONMENT"),
        "process_cues": ("assignee", "status", "priority", "due", "incident", "root cause", "sla"),
    },
    "linear": {
        "database": "erblinear",
        "entities": ("WorkItem", "Project", "Assignee", "Release", "DueDate"),
        "relations": ("ASSIGNED_TO", "SCHEDULED_FOR", "PART_OF_PROJECT", "DUE_ON"),
        "process_cues": ("assigned", "planned", "release", "due", "acceptance criteria"),
    },
    "slack": {
        "database": "erbslack",
        "entities": ("Channel", "Thread", "Message", "User", "Bot"),
        "relations": ("POSTED_BY", "REPLIED_TO", "MENTIONS", "TRIGGERED_BY", "DECIDED_IN"),
        "process_cues": ("channel", "thread", "posted", "bot", "decision", "hold", "canary"),
    },
    "gmail": {
        "database": "erbgmail",
        "entities": ("Mailbox", "EmailThread", "Sender", "Recipient", "Attachment"),
        "relations": ("SENT_BY", "SENT_TO", "HAS_ATTACHMENT", "REPLIES_TO", "NEGOTIATES"),
        "process_cues": ("email", "thread", "attachment", "proposed", "sent", "from", "to"),
    },
    "confluence": {
        "database": "erbconfluence",
        "entities": ("Space", "Page", "Author", "Runbook", "Policy"),
        "relations": ("AUTHORED_BY", "PUBLISHED_IN", "UPDATED_ON", "CITES", "DEFINES_POLICY"),
        "process_cues": ("page", "space", "runbook", "policy", "updated", "published", "author"),
    },
    "google_drive": {
        "database": "erbgoogledrive",
        "entities": ("Drive", "Document", "Spreadsheet", "Owner", "Folder"),
        "relations": ("OWNED_BY", "LOCATED_IN", "MODIFIED_ON", "SHARED_WITH"),
        "process_cues": ("drive", "owner", "spreadsheet", "draft", "modified", "folder"),
    },
    "fireflies": {
        "database": "erbfireflies",
        "entities": ("Meeting", "Participant", "Organizer", "ActionItem", "Transcript"),
        "relations": ("ORGANIZED_BY", "ATTENDED_BY", "ASSIGNED_ACTION", "DUE_ON", "DISCUSSED"),
        "process_cues": ("meeting", "call", "organizer", "action item", "due", "transcript"),
    },
    "hubspot": {
        "database": "erbhubspot",
        "entities": ("Account", "Contact", "Opportunity", "Owner", "Forecast"),
        "relations": ("OWNED_BY", "ASSIGNED_SE", "FORECAST_CLOSE", "HAS_REQUIREMENT"),
        "process_cues": ("account", "forecast", "close", "owner", "solutions engineer", "requirement"),
    },
}


PROMPT_PROFILES: dict[str, dict[str, Any]] = {
    "retrieval_router_v1": {
        "stage": "source_and_subgroup_routing",
        "system_prompt": (
            "You are an enterprise retrieval router. Select only the source databases "
            "and source-native subgroups needed to answer the question. Preserve "
            "who, when, where, and how constraints. Return missing-evidence intent "
            "instead of broad retrieval when the question asks for unavailable facts."
        ),
        "expected_output": "selected_sources, selected_subgroups, required_slots, abstention_hint",
    },
    "context_graph_extractor_v1": {
        "stage": "context_graph_extraction",
        "system_prompt": (
            "Extract a compact process-aware context graph. Prefer explicit actors, "
            "timestamps, locations/environments, actions, decisions, ownership, due "
            "dates, approvals, and evidence links. Do not create relationships that "
            "are not allowed by the active source ontology profile."
        ),
        "expected_output": "nodes, relationships, validation_errors, repaired_or_dropped_relationships",
    },
    "grounded_answer_v1": {
        "stage": "answer_generation",
        "system_prompt": (
            "Answer only from the provided evidence bundle. Cite document IDs that "
            "support the answer. If required slots are missing, say what is missing "
            "instead of filling it from model memory."
        ),
        "expected_output": "answer, document_ids, supported_facts, missing_slots",
    },
    "arbiter_judge_v1": {
        "stage": "evaluation",
        "system_prompt": (
            "Judge correctness, completeness, document support, unsupported claims, "
            "and whether the answer abstains appropriately when the corpus lacks the "
            "requested information. Prefer evidence-grounded incompleteness over "
            "unsupported fluency."
        ),
        "expected_output": "correctness, completeness, support, unsupported_claims, abstention_quality",
    },
}


MODEL_PROVIDER_MATRIX: dict[str, dict[str, Any]] = {
    "mara_minimax_m27": {
        "provider": "mara",
        "model": "MiniMax-M2.7",
        "recommended_stages": ("context_graph_extraction", "grounded_answer"),
        "rationale": "Used in prior context graph extraction runs; good continuity for process-context ontology prompts.",
    },
    "mara_deepseek_v31": {
        "provider": "mara",
        "model": "DeepSeek-V3.1",
        "recommended_stages": ("grounded_answer", "peer_adapter_answer"),
        "rationale": "Default answer model in peer benchmark contract.",
    },
    "mara_gpt_oss_120b": {
        "provider": "mara",
        "model": "gpt-oss-120b",
        "recommended_stages": ("arbiter_judge",),
        "rationale": "Default judge model in peer benchmark contract.",
    },
    "local_bge": {
        "provider": "local",
        "model": "BAAI/bge-small-en-v1.5",
        "recommended_stages": ("embedding", "hybrid_rerank"),
        "rationale": "Shared CUDA-capable embedding policy for fair retrieval comparisons.",
    },
}


CONTEXT_GRAPH_FEATURES: dict[str, dict[str, Any]] = {
    "source_database_federation": {
        "hypothesis": "Routing by source_type reduces search space without hurting recall when source labels are available.",
        "metric": "source_route_accuracy, available_document_recall, latency",
    },
    "process_context_guardrail": {
        "hypothesis": "Who/when/where/how cues improve process-heavy enterprise questions across sources.",
        "metric": "available_document_recall by source/question_type, missing_slot_abstention",
    },
    "ontology_allowed_relationships": {
        "hypothesis": "Source-specific allowed relations reduce invalid graph evidence and unsupported synthesis.",
        "metric": "validation_errors, illegal_relationships, unsupported_claim_rate",
    },
    "hybrid_bge_reranking": {
        "hypothesis": "BGE reranking after context/source filtering raises recall and lowers invalid extra documents.",
        "metric": "available_document_recall, invalid_extra_documents, elapsed_seconds",
    },
    "grounded_synthesis_prompt": {
        "hypothesis": "Evidence-bundle answer prompts reduce unsupported claims and improve abstention quality.",
        "metric": "arbiter correctness/completeness/support/abstention_quality",
    },
}


@dataclass(frozen=True)
class ERBQuestion:
    question_id: str
    question_type: str
    source_types: tuple[str, ...]
    question: str
    expected_doc_ids: tuple[str, ...]
    gold_answer: str
    answer_facts: tuple[str, ...] = ()
    metadata_track: bool = False


@dataclass(frozen=True)
class ERBDocument:
    doc_id: str
    source_type: str
    subgroup: str
    path: str
    title: str
    text: str


@dataclass(frozen=True)
class FederationDatabase:
    name: str
    source_type: str
    subgroup_count: int
    document_count: int
    expected_gold_docs: int
    ontology_entities: tuple[str, ...]
    ontology_relations: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalRecord:
    question_id: str
    question_type: str
    source_types: tuple[str, ...]
    selected_databases: tuple[str, ...]
    consulted_databases: tuple[str, ...]
    document_ids: tuple[str, ...]
    answer: str
    metrics: Mapping[str, Any]
    evidence_preview: tuple[str, ...] = ()
    observability: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FederationRun:
    workspace_id: str
    corpus_root: str
    questions_file: str
    answer_mode: str
    reranker: str
    prompt_profile: str
    model_provider_profile: str
    top_k: int
    elapsed_seconds: float
    question_count: int
    document_count: int
    databases: tuple[FederationDatabase, ...]
    aggregate_metrics: Mapping[str, Any]
    ontology_guardrail: Mapping[str, Any]
    observability: Mapping[str, Any]
    records: tuple[RetrievalRecord, ...] = field(default_factory=tuple)


def _tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[^A-Za-z0-9가-힣._/-]+", " ", text.lower())
    return {tok for tok in cleaned.split() if len(tok) > 1}


def _build_token_index(documents: Sequence[ERBDocument]) -> dict[str, set[str]]:
    return {doc.doc_id: _tokens(f"{doc.title}\n{doc.text}") for doc in documents}


def _build_cue_index(documents: Sequence[ERBDocument]) -> dict[str, set[str]]:
    cue_index: dict[str, set[str]] = {}
    for doc in documents:
        haystack = f"{doc.title}\n{doc.text}".lower()
        cues = tuple(SOURCE_ONTOLOGY.get(doc.source_type, {}).get("process_cues") or ())
        cue_index[doc.doc_id] = {cue for cue in cues if cue in haystack}
    return cue_index


def _doc_id_from_path(path: Path) -> str:
    match = DSID_RE.search(path.name)
    if match:
        return match.group(1)
    return path.stem


def _infer_source_type(path: Path, corpus_root: Path) -> str:
    rel = path.relative_to(corpus_root)
    if len(rel.parts) > 1:
        return rel.parts[0].lower()
    prefix = path.name.split("_slice_", 1)[0].split("_", 1)[0]
    return prefix.lower()


def _field_value(text: str, *names: str) -> str | None:
    for line in text.splitlines()[:40]:
        stripped = line.strip()
        for name in names:
            match = re.match(rf"{re.escape(name)}\s*:\s*(.+)", stripped, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return None


def _slug(value: str, *, fallback: str = "general") -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:80] or fallback


def _infer_subgroup(source_type: str, rel_path: Path, text: str) -> str:
    if source_type == "slack":
        return _slug(_field_value(text, "Channel") or rel_path.parent.name)
    if source_type == "gmail":
        sender = _field_value(text, "From")
        if sender and "@" in sender:
            return _slug(sender.split("@", 1)[1].split(">", 1)[0])
        return _slug(rel_path.parent.name)
    if source_type == "jira":
        ticket = _field_value(text, "Ticket")
        if ticket and "-" in ticket:
            return _slug(ticket.split("-", 1)[0])
        return _slug(rel_path.parent.name)
    if source_type == "linear":
        ticket = _field_value(text, "Issue", "Ticket", "Work item")
        if ticket and "-" in ticket:
            return _slug(ticket.split("-", 1)[0])
        return _slug(rel_path.parent.name)
    if source_type == "confluence":
        owner = _field_value(text, "Owner", "Space")
        if owner:
            return _slug(owner)
        parts = rel_path.parts
        return _slug(parts[1] if len(parts) > 2 else rel_path.parent.name)
    if source_type == "google_drive":
        return _slug(_field_value(text, "Owner", "Drive", "Folder") or rel_path.parent.name)
    if source_type == "fireflies":
        return _slug(_field_value(text, "Meeting owner", "Organizer", "Owner") or rel_path.parent.name)
    if source_type == "hubspot":
        return _slug(_field_value(text, "Owner", "Account owner", "Stage") or rel_path.parent.name)
    return _slug(rel_path.parent.name)


def _title_for(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:160]
    return fallback


def load_questions(path: str | Path, *, metadata_track: bool = False) -> tuple[ERBQuestion, ...]:
    questions_path = Path(path)
    rows: list[ERBQuestion] = []
    for line_no, line in enumerate(questions_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        rows.append(
            ERBQuestion(
                question_id=str(raw.get("question_id") or f"qst_{line_no:04d}"),
                question_type=str(raw.get("question_type") or "unknown"),
                source_types=tuple(str(x).lower() for x in raw.get("source_types", ())),
                question=str(raw["question"]),
                expected_doc_ids=tuple(str(x) for x in raw.get("expected_doc_ids", ())),
                gold_answer=str(raw.get("gold_answer") or ""),
                answer_facts=tuple(str(x) for x in raw.get("answer_facts", ())),
                metadata_track=metadata_track,
            )
        )
    return tuple(rows)


def load_documents(corpus_root: str | Path) -> tuple[ERBDocument, ...]:
    root = Path(corpus_root)
    if not root.exists():
        raise FileNotFoundError(f"corpus root not found: {root}")
    docs: list[ERBDocument] = []
    for path in sorted(root.rglob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        docs.append(
            ERBDocument(
                doc_id=_doc_id_from_path(path),
                source_type=_infer_source_type(path, root),
                subgroup=_infer_subgroup(_infer_source_type(path, root), path.relative_to(root), text),
                path=str(path.relative_to(root)),
                title=_title_for(text, path.stem),
                text=text,
            )
        )
    return tuple(docs)


def _selected_sources(question: ERBQuestion, available_sources: Iterable[str]) -> tuple[str, ...]:
    available = tuple(sorted(set(available_sources)))
    if question.source_types:
        selected = tuple(source for source in question.source_types if source in available)
        if selected:
            return selected
    return available


def build_database_plan(
    questions: Sequence[ERBQuestion],
    documents: Sequence[ERBDocument],
) -> tuple[FederationDatabase, ...]:
    doc_counts = Counter(doc.source_type for doc in documents)
    subgroup_counts: dict[str, set[str]] = defaultdict(set)
    for doc in documents:
        subgroup_counts[doc.source_type].add(doc.subgroup)
    doc_sources = {doc.doc_id: doc.source_type for doc in documents}
    expected_counts: Counter[str] = Counter()
    for question in questions:
        for doc_id in question.expected_doc_ids:
            source = doc_sources.get(doc_id)
            if source:
                expected_counts[source] += 1
    databases: list[FederationDatabase] = []
    for source, count in sorted(doc_counts.items()):
        profile = SOURCE_ONTOLOGY.get(source, {})
        databases.append(
            FederationDatabase(
                name=str(profile.get("database") or f"erb{re.sub(r'[^a-z0-9]', '', source)}"),
                source_type=source,
                subgroup_count=len(subgroup_counts[source]),
                document_count=count,
                expected_gold_docs=expected_counts[source],
                ontology_entities=tuple(profile.get("entities") or ("Document", "Entity", "Evidence")),
                ontology_relations=tuple(profile.get("relations") or ("MENTIONS", "CITES", "SUPPORTS")),
            )
        )
    return tuple(databases)


def _ontology_cue_score(
    question: ERBQuestion,
    doc: ERBDocument,
    cue_index: Mapping[str, set[str]] | None = None,
) -> int:
    text = question.question.lower()
    profile = SOURCE_ONTOLOGY.get(doc.source_type, {})
    cues = tuple(profile.get("process_cues") or ())
    question_cues = {cue for cue in cues if cue in text}
    document_cues = cue_index.get(doc.doc_id, set()) if cue_index is not None else {
        cue for cue in cues if cue in f"{doc.title} {doc.text}".lower()
    }
    return len(question_cues | document_cues)


def _rank_documents(
    question: ERBQuestion,
    docs: Sequence[ERBDocument],
    *,
    top_k: int,
    source_filtered: bool,
    token_index: Mapping[str, set[str]] | None = None,
    cue_index: Mapping[str, set[str]] | None = None,
) -> tuple[ERBDocument, ...]:
    q_tokens = _tokens(question.question)
    scored: list[tuple[int, int, int, str, ERBDocument]] = []
    for doc in docs:
        doc_tokens = token_index.get(doc.doc_id) if token_index is not None else _tokens(f"{doc.title}\n{doc.text}")
        lexical = len(q_tokens & doc_tokens)
        source_bonus = 3 if doc.source_type in question.source_types else 0
        ontology_bonus = _ontology_cue_score(question, doc, cue_index)
        if source_filtered:
            source_bonus += 2
        score = lexical * 10 + source_bonus + ontology_bonus
        scored.append((score, lexical, ontology_bonus, doc.doc_id, doc))
    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    return tuple(item[-1] for item in scored[:top_k] if item[0] > 0) or tuple(doc for doc in docs[:top_k])


def _bge_rerank(
    question: ERBQuestion,
    candidates: Sequence[ERBDocument],
    *,
    top_k: int,
    bge_model: str,
    bge_device: str | None,
    embedder: Any | None = None,
) -> tuple[ERBDocument, str]:
    from seocho.store.local_embedding import LocalBGEEmbeddingBackend

    if embedder is None:
        embedder = LocalBGEEmbeddingBackend(model=bge_model, device=bge_device)
    query_vec = embedder.embed_queries([question.question])[0]
    passages = [
        f"{doc.title}\n{doc.text[:1800]}"
        for doc in candidates
    ]
    doc_vecs = embedder.embed(passages)

    def dot(left: Sequence[float], right: Sequence[float]) -> float:
        return float(sum(a * b for a, b in zip(left, right)))

    scored = sorted(
        ((dot(query_vec, vec), idx, doc) for idx, (vec, doc) in enumerate(zip(doc_vecs, candidates))),
        key=lambda item: (item[0], -item[1]),
        reverse=True,
    )
    return tuple(doc for _score, _idx, doc in scored[:top_k]), embedder.device


def _extractive_answer(question: ERBQuestion, docs: Sequence[ERBDocument]) -> str:
    q_tokens = _tokens(question.question)
    best: tuple[int, str] | None = None
    for doc in docs:
        for sentence in re.split(r"(?<=[.!?])\s+", doc.text.replace("\n", " ")):
            score = len(q_tokens & _tokens(sentence))
            if score and (best is None or score > best[0]):
                best = (score, sentence.strip())
    return best[1] if best else ""


def _answer_for(question: ERBQuestion, docs: Sequence[ERBDocument], answer_mode: str) -> str:
    if answer_mode == "retrieval_only":
        return ""
    if answer_mode == "extractive":
        return _extractive_answer(question, docs)
    if answer_mode == "oracle_gold":
        return question.gold_answer
    raise ValueError(f"unknown answer mode: {answer_mode}")


def _record_metrics(
    question: ERBQuestion,
    docs: Sequence[ERBDocument],
    selected_sources: Sequence[str],
    available_doc_ids: set[str],
    cue_index: Mapping[str, set[str]] | None = None,
) -> dict[str, Any]:
    retrieved_ids = [doc.doc_id for doc in docs]
    expected = set(question.expected_doc_ids)
    available_expected = expected & available_doc_ids
    retrieved = set(retrieved_ids)
    if expected:
        doc_recall = len(expected & retrieved) / len(expected)
        available_doc_recall = (
            len(available_expected & retrieved) / len(available_expected)
            if available_expected
            else None
        )
        invalid_extra = len(retrieved - expected)
    else:
        doc_recall = None
        available_doc_recall = None
        invalid_extra = len(retrieved)
    retrieved_sources = {doc.source_type for doc in docs}
    expected_sources = set(question.source_types)
    source_route_hit = bool(expected_sources & set(selected_sources)) if expected_sources else True
    return {
        "document_recall": None if doc_recall is None else round(doc_recall, 4),
        "available_document_recall": None
        if available_doc_recall is None
        else round(available_doc_recall, 4),
        "gold_docs_available_count": len(available_expected),
        "corpus_gold_coverage": round(len(available_expected) / len(expected), 4)
        if expected
        else None,
        "invalid_extra_documents": invalid_extra,
        "expected_doc_count": len(expected),
        "retrieved_doc_count": len(retrieved),
        "source_route_hit": source_route_hit,
        "retrieved_sources": sorted(retrieved_sources),
        "abstention_expected": not expected,
        "ontology_cue_hits": sum(_ontology_cue_score(question, doc, cue_index) for doc in docs),
    }


def run_federated_retrieval(
    *,
    questions: Sequence[ERBQuestion],
    documents: Sequence[ERBDocument],
    workspace_id: str,
    corpus_root: str,
    questions_file: str,
    top_k: int,
    answer_mode: str,
    reranker: str = "lexical",
    candidate_k: int = 80,
    bge_model: str = "BAAI/bge-small-en-v1.5",
    bge_device: str | None = None,
    prompt_profile: str = "retrieval_router_v1",
    model_provider_profile: str = "local_bge",
    include_records: bool = True,
) -> FederationRun:
    started = time.perf_counter()
    timer = StageTimer()
    with timer.stage("prepare_sources"):
        docs_by_source: dict[str, list[ERBDocument]] = defaultdict(list)
        for doc in documents:
            docs_by_source[doc.source_type].append(doc)
        databases = build_database_plan(questions, documents)
        database_by_source = {db.source_type: db.name for db in databases}
        available_doc_ids = {doc.doc_id for doc in documents}
    with timer.stage("build_indexes"):
        token_index = _build_token_index(documents)
        cue_index = _build_cue_index(documents)
    resolved_reranker = reranker
    bge_embedder: Any | None = None
    bge_load_ms: float | None = None
    if reranker == "bge_hybrid":
        from seocho.store.local_embedding import LocalBGEEmbeddingBackend

        with timer.stage("load_bge_embedder"):
            bge_embedder = LocalBGEEmbeddingBackend(model=bge_model, device=bge_device)
        bge_load_ms = timer.to_dict().get("load_bge_embedder_ms")

    records: list[RetrievalRecord] = []
    route_rank_ms_total = 0.0
    bge_rerank_ms_total = 0.0
    answer_ms_total = 0.0
    for question in questions:
        case_timer = StageTimer()
        selected_sources = _selected_sources(question, docs_by_source)
        candidate_docs = [doc for source in selected_sources for doc in docs_by_source.get(source, ())]
        if not candidate_docs:
            candidate_docs = list(documents)
        source_filtered = bool(question.source_types and set(selected_sources) <= set(question.source_types))
        with case_timer.stage("route_and_rank"):
            ranked_candidates = _rank_documents(
                question,
                candidate_docs,
                top_k=max(top_k, candidate_k if reranker == "bge_hybrid" else top_k),
                source_filtered=source_filtered,
                token_index=token_index,
                cue_index=cue_index,
            )
        if reranker == "bge_hybrid":
            with case_timer.stage("bge_rerank"):
                ranked, device = _bge_rerank(
                    question,
                    ranked_candidates[:candidate_k],
                    top_k=top_k,
                    bge_model=bge_model,
                    bge_device=bge_device,
                    embedder=bge_embedder,
                )
            resolved_reranker = f"bge_hybrid:{device}"
        else:
            ranked = ranked_candidates[:top_k]
        with case_timer.stage("answer_generation"):
            answer = _answer_for(question, ranked, answer_mode)
        case_timer.mark_total("case_total")
        case_obs = case_timer.to_dict()
        route_rank_ms_total += float(case_obs.get("route_and_rank_ms", 0.0))
        bge_rerank_ms_total += float(case_obs.get("bge_rerank_ms", 0.0))
        answer_ms_total += float(case_obs.get("answer_generation_ms", 0.0))
        selected_dbs = tuple(database_by_source.get(source, f"erb{source}") for source in selected_sources)
        records.append(
            RetrievalRecord(
                question_id=question.question_id,
                question_type=question.question_type,
                source_types=question.source_types,
                selected_databases=selected_dbs,
                consulted_databases=selected_dbs,
                document_ids=tuple(doc.doc_id for doc in ranked),
                answer=answer,
                metrics=_record_metrics(question, ranked, selected_sources, available_doc_ids, cue_index),
                evidence_preview=tuple(f"{doc.doc_id} [{doc.source_type}] {doc.title}" for doc in ranked[:3]),
                observability={
                    "schema_version": "enterprise_rag_trace_record.v1",
                    "workspace_id": workspace_id,
                    "question_id": question.question_id,
                    "stage_timings_ms": case_obs,
                    "source_candidate_count": len(candidate_docs),
                    "ranked_candidate_count": len(ranked_candidates),
                    "top_k": top_k,
                    "candidate_k": candidate_k if reranker == "bge_hybrid" else None,
                    "reranker": resolved_reranker,
                    "prompt_profile": prompt_profile,
                    "model_provider_profile": model_provider_profile,
                    "selected_sources": selected_sources,
                    "selected_databases": selected_dbs,
                },
            )
        )

    aggregate = _aggregate_metrics(records)
    timer.record("route_and_rank_total", route_rank_ms_total)
    timer.record("bge_rerank_total", bge_rerank_ms_total)
    timer.record("answer_generation_total", answer_ms_total)
    timer.mark_total("total")
    run_observability = {
        "schema_version": "enterprise_rag_run_observability.v1",
        "trace_backend": "jsonl",
        "workspace_id": workspace_id,
        "stage_timings_ms": timer.to_dict(),
        "per_case_stage_mean_ms": {
            "route_and_rank": round(route_rank_ms_total / max(len(records), 1), 2),
            "bge_rerank": round(bge_rerank_ms_total / max(len(records), 1), 2),
            "answer_generation": round(answer_ms_total / max(len(records), 1), 2),
        },
        "bge": {
            "model": bge_model if reranker == "bge_hybrid" else None,
            "requested_device": bge_device if reranker == "bge_hybrid" else None,
            "resolved_device": resolved_reranker.split(":", 1)[1] if resolved_reranker.startswith("bge_hybrid:") else None,
            "load_ms": bge_load_ms,
        },
        "routing": {
            "database_unit": "source_type",
            "subgroup_unit": "source_native_subgroup",
            "question_count": len(questions),
            "document_count": len(documents),
        },
    }
    ontology_guardrail = {
        "name": "enterprise_context_graph_process_guardrail.v1",
        "goal": "Preserve who/when/where/how process context across source-federated retrieval.",
        "runtime_policy": "Use source-specific compiled profiles for routing and evidence scoring; keep heavy ontology reasoning offline.",
        "sources": {
            source: {
                "database": profile["database"],
                "entities": profile["entities"],
                "relations": profile["relations"],
                "process_cues": profile["process_cues"],
            }
            for source, profile in sorted(SOURCE_ONTOLOGY.items())
        },
        "federation_contract": {
            "workspace_id": workspace_id,
            "database_unit": "source_type",
            "document_key": "dsid",
            "question_key": "question_id",
        },
        "context_graph_features_under_test": CONTEXT_GRAPH_FEATURES,
        "prompt_profile": PROMPT_PROFILES[prompt_profile],
        "model_provider_profile": MODEL_PROVIDER_MATRIX[model_provider_profile],
        "model_provider_matrix": MODEL_PROVIDER_MATRIX,
    }
    return FederationRun(
        workspace_id=workspace_id,
        corpus_root=corpus_root,
        questions_file=questions_file,
        answer_mode=answer_mode,
        reranker=resolved_reranker,
        prompt_profile=prompt_profile,
        model_provider_profile=model_provider_profile,
        top_k=top_k,
        elapsed_seconds=round(time.perf_counter() - started, 3),
        question_count=len(questions),
        document_count=len(documents),
        databases=databases,
        aggregate_metrics=aggregate,
        ontology_guardrail=ontology_guardrail,
        observability=run_observability,
        records=tuple(records) if include_records else (),
    )


def _aggregate_metrics(records: Sequence[RetrievalRecord]) -> dict[str, Any]:
    with_gold = [r for r in records if r.metrics["expected_doc_count"]]
    with_available_gold = [r for r in with_gold if r.metrics["gold_docs_available_count"]]
    absent = [r for r in records if r.metrics["abstention_expected"]]
    recall_values = [
        float(r.metrics["document_recall"])
        for r in with_gold
        if r.metrics["document_recall"] is not None
    ]
    available_recall_values = [
        float(r.metrics["available_document_recall"])
        for r in with_available_gold
        if r.metrics["available_document_recall"] is not None
    ]
    corpus_gold_coverages = [
        float(r.metrics["corpus_gold_coverage"])
        for r in with_gold
        if r.metrics["corpus_gold_coverage"] is not None
    ]
    source_hits = [bool(r.metrics["source_route_hit"]) for r in records]
    invalid_values = [int(r.metrics["invalid_extra_documents"]) for r in records]
    by_type: dict[str, dict[str, Any]] = {}
    for question_type in sorted({r.question_type for r in records}):
        subset = [r for r in records if r.question_type == question_type]
        subset_recall = [
            float(r.metrics["document_recall"])
            for r in subset
            if r.metrics["document_recall"] is not None
        ]
        subset_available_recall = [
            float(r.metrics["available_document_recall"])
            for r in subset
            if r.metrics["available_document_recall"] is not None
        ]
        by_type[question_type] = {
            "count": len(subset),
            "mean_document_recall": round(sum(subset_recall) / len(subset_recall), 4)
            if subset_recall
            else None,
            "mean_available_document_recall": round(
                sum(subset_available_recall) / len(subset_available_recall), 4
            )
            if subset_available_recall
            else None,
            "mean_invalid_extra_documents": round(
                sum(int(r.metrics["invalid_extra_documents"]) for r in subset) / len(subset), 4
            ),
        }
    return {
        "mean_document_recall": round(sum(recall_values) / len(recall_values), 4) if recall_values else None,
        "mean_available_document_recall": round(
            sum(available_recall_values) / len(available_recall_values), 4
        )
        if available_recall_values
        else None,
        "corpus_gold_coverage": round(sum(corpus_gold_coverages) / len(corpus_gold_coverages), 4)
        if corpus_gold_coverages
        else None,
        "mean_invalid_extra_documents": round(sum(invalid_values) / len(invalid_values), 4) if invalid_values else None,
        "source_route_accuracy": round(sum(source_hits) / len(source_hits), 4) if source_hits else None,
        "questions_with_gold_documents": len(with_gold),
        "questions_with_gold_available": len(with_available_gold),
        "abstention_questions": len(absent),
        "by_question_type": by_type,
        "by_source_type": _aggregate_by_source(records),
    }


def _aggregate_by_source(records: Sequence[RetrievalRecord]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sources = sorted({source for record in records for source in record.source_types} | {"unspecified"})
    for source in sources:
        subset = [
            record
            for record in records
            if (source == "unspecified" and not record.source_types) or source in record.source_types
        ]
        if not subset:
            continue
        available = [
            float(record.metrics["available_document_recall"])
            for record in subset
            if record.metrics["available_document_recall"] is not None
        ]
        recall = [
            float(record.metrics["document_recall"])
            for record in subset
            if record.metrics["document_recall"] is not None
        ]
        out[source] = {
            "count": len(subset),
            "mean_document_recall": round(sum(recall) / len(recall), 4) if recall else None,
            "mean_available_document_recall": round(sum(available) / len(available), 4)
            if available
            else None,
            "mean_invalid_extra_documents": round(
                sum(int(record.metrics["invalid_extra_documents"]) for record in subset) / len(subset),
                4,
            ),
        }
    return out


def analyze_federation_axes(
    questions: Sequence[ERBQuestion],
    documents: Sequence[ERBDocument],
) -> dict[str, Any]:
    doc_by_source = Counter(doc.source_type for doc in documents)
    subgroup_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    for doc in documents:
        subgroup_by_source[doc.source_type][doc.subgroup] += 1
    return {
        "question_count": len(questions),
        "document_count": len(documents),
        "recommended_topology": [
            "tenant/workspace_id",
            "source_type database",
            "source-native subgroup shard",
            "document/thread/page/ticket node",
            "ontology process-context overlay",
        ],
        "federation_axes": {
            "source_type": {
                "rationale": "Strongest fair split: benchmark questions already label source_types and release files are source-sliced.",
                "documents": dict(sorted(doc_by_source.items())),
                "questions": dict(sorted(Counter(s for q in questions for s in q.source_types).items())),
            },
            "question_type": {
                "rationale": "Controls route profile: fact lookup, semantic paraphrase, intra-doc, project aggregate, constrained, conflict, completeness, high-level, absent.",
                "questions": dict(sorted(Counter(q.question_type for q in questions).items())),
            },
            "source_native_subgroup": {
                "rationale": "Second-stage federation inside each source: channel, mailbox domain, project key, space, drive owner, meeting owner, account owner.",
                "top_subgroups": {
                    source: dict(counter.most_common(10))
                    for source, counter in sorted(subgroup_by_source.items())
                },
            },
            "process_context": {
                "rationale": "SEOCHO-specific ontology guardrail: preserve who/when/where/how, owner, assignee, reviewer, due date, action item, approval, status.",
                "source_profiles": {
                    source: {
                        "database": profile["database"],
                        "entities": profile["entities"],
                        "relations": profile["relations"],
                        "process_cues": profile["process_cues"],
                    }
                    for source, profile in sorted(SOURCE_ONTOLOGY.items())
                },
            },
            "prompt_and_provider": {
                "rationale": "Prompt/provider must be versioned per stage so retrieval, extraction, synthesis, and arbiter runs are comparable.",
                "prompt_profiles": PROMPT_PROFILES,
                "model_provider_matrix": MODEL_PROVIDER_MATRIX,
            },
        },
    }


def _emit_json(payload: Mapping[str, Any], out: str) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out == "-":
        print(text)
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def _emit_answers(records: Sequence[RetrievalRecord], out: str) -> None:
    lines = []
    for record in records:
        lines.append(
            json.dumps(
                {
                    "question_id": record.question_id,
                    "answer": record.answer,
                    "document_ids": list(record.document_ids),
                },
                ensure_ascii=False,
            )
        )
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _emit_trace(records: Sequence[RetrievalRecord], out: str) -> None:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record.observability, ensure_ascii=False) for record in records]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _asdict_run(run: FederationRun) -> dict[str, Any]:
    return asdict(run)


def _load_inputs(args: argparse.Namespace) -> tuple[tuple[ERBQuestion, ...], tuple[ERBDocument, ...]]:
    questions = load_questions(args.questions, metadata_track=args.metadata_track)
    if args.limit:
        questions = questions[: args.limit]
    documents = load_documents(args.corpus_root)
    return questions, documents


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--questions", required=True, help="EnterpriseRAG-Bench questions.jsonl or extra_questions.jsonl")
    common.add_argument("--corpus-root", required=True, help="Directory containing exported .txt documents by source type")
    common.add_argument("--metadata-track", action="store_true", help="Mark extra_questions.jsonl metadata track")
    common.add_argument("--limit", type=int, default=0, help="Optional first-N question slice")

    inspect_cmd = sub.add_parser("inspect", parents=[common], help="Inspect corpus/question coverage")
    inspect_cmd.add_argument("--out", default="-")

    analyze_cmd = sub.add_parser("analyze-federation", parents=[common], help="Analyze source and subgroup federation axes")
    analyze_cmd.add_argument("--out", default="-")

    plan_cmd = sub.add_parser("plan", parents=[common], help="Emit source database federation plan")
    plan_cmd.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    plan_cmd.add_argument("--out", default="-")

    run_cmd = sub.add_parser("run", parents=[common], help="Run deterministic federated retrieval")
    run_cmd.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    run_cmd.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    run_cmd.add_argument("--candidate-k", type=int, default=80)
    run_cmd.add_argument("--reranker", choices=("lexical", "bge_hybrid"), default="lexical")
    run_cmd.add_argument("--bge-model", default="BAAI/bge-small-en-v1.5")
    run_cmd.add_argument("--bge-device", default=None)
    run_cmd.add_argument("--prompt-profile", choices=sorted(PROMPT_PROFILES), default="retrieval_router_v1")
    run_cmd.add_argument("--model-provider-profile", choices=sorted(MODEL_PROVIDER_MATRIX), default="local_bge")
    run_cmd.add_argument(
        "--answer-mode",
        choices=("retrieval_only", "extractive", "oracle_gold"),
        default="retrieval_only",
    )
    run_cmd.add_argument("--answers-out", default="", help="Optional EnterpriseRAG-Bench answer JSONL")
    run_cmd.add_argument("--trace-out", default="", help="Optional vendor-neutral per-case trace JSONL")
    run_cmd.add_argument("--out", default="-")

    args = parser.parse_args(argv)
    questions, documents = _load_inputs(args)

    if args.cmd == "inspect":
        payload = {
            "questions": len(questions),
            "documents": len(documents),
            "question_types": dict(sorted(Counter(q.question_type for q in questions).items())),
            "question_source_types": dict(sorted(Counter(s for q in questions for s in q.source_types).items())),
            "document_source_types": dict(sorted(Counter(d.source_type for d in documents).items())),
            "gold_doc_ids_found": sum(
                1
                for q in questions
                for doc_id in q.expected_doc_ids
                if any(doc.doc_id == doc_id for doc in documents)
            ),
        }
        _emit_json(payload, args.out)
        return 0

    if args.cmd == "plan":
        run = run_federated_retrieval(
            questions=questions,
            documents=documents,
            workspace_id=args.workspace_id,
            corpus_root=args.corpus_root,
            questions_file=args.questions,
            top_k=DEFAULT_TOP_K,
            answer_mode="retrieval_only",
            reranker="lexical",
            prompt_profile="retrieval_router_v1",
            model_provider_profile="local_bge",
            include_records=False,
        )
        _emit_json(
            {
                "workspace_id": run.workspace_id,
                "databases": [asdict(db) for db in run.databases],
                "ontology_guardrail": run.ontology_guardrail,
                "fairness_controls": (
                    "same EnterpriseRAG-Bench questions and gold document IDs",
                    "same source-organized corpus for every retrieval arm",
                    "answer generation is separated from retrieval-only document scoring",
                    "database federation unit is source_type, not model-provider-specific tuning",
                    "document_ids output remains compatible with EnterpriseRAG-Bench metrics-based evaluator",
                ),
                "arms": (
                    "content_vector_bge",
                    "content_bm25",
                    "context_graph_source_local",
                    "context_graph_database_federated",
                    "hybrid_content_context_graph_bge_rerank",
                ),
                "context_graph_features_under_test": CONTEXT_GRAPH_FEATURES,
                "prompt_profiles": PROMPT_PROFILES,
                "model_provider_matrix": MODEL_PROVIDER_MATRIX,
            },
            args.out,
        )
        return 0

    if args.cmd == "analyze-federation":
        _emit_json(analyze_federation_axes(questions, documents), args.out)
        return 0

    run = run_federated_retrieval(
        questions=questions,
        documents=documents,
        workspace_id=args.workspace_id,
        corpus_root=args.corpus_root,
        questions_file=args.questions,
        top_k=args.top_k,
        answer_mode=args.answer_mode,
        reranker=args.reranker,
        candidate_k=args.candidate_k,
        bge_model=args.bge_model,
        bge_device=args.bge_device,
        prompt_profile=args.prompt_profile,
        model_provider_profile=args.model_provider_profile,
    )
    if args.answers_out:
        _emit_answers(run.records, args.answers_out)
    if args.trace_out:
        _emit_trace(run.records, args.trace_out)
    _emit_json(_asdict_run(run), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
