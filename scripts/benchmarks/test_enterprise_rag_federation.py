"""Tests for EnterpriseRAG-Bench federation harness."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import enterprise_rag_federation as erb


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "examples" / "benchmarks" / "enterprise_rag_smoke"


def test_load_questions_and_documents_smoke() -> None:
    questions = erb.load_questions(SMOKE / "questions.jsonl")
    docs = erb.load_documents(SMOKE / "corpus")

    assert len(questions) == 3
    assert questions[0].question_id == "qst_0001"
    assert {doc.source_type for doc in docs} == {"github", "jira", "slack"}
    assert {doc.doc_id for doc in docs} >= {
        "dsid_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "dsid_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    }


def test_database_plan_uses_source_ontology() -> None:
    questions = erb.load_questions(SMOKE / "questions.jsonl")
    docs = erb.load_documents(SMOKE / "corpus")
    plan = erb.build_database_plan(questions, docs)

    github = next(db for db in plan if db.source_type == "github")
    assert github.name == "erbgithub"
    assert "PullRequest" in github.ontology_entities
    assert github.expected_gold_docs == 1


def test_run_federated_retrieval_records_doc_recall() -> None:
    questions = erb.load_questions(SMOKE / "questions.jsonl")
    docs = erb.load_documents(SMOKE / "corpus")
    run = erb.run_federated_retrieval(
        questions=questions,
        documents=docs,
        workspace_id="test_ws",
        corpus_root=str(SMOKE / "corpus"),
        questions_file=str(SMOKE / "questions.jsonl"),
        top_k=2,
        answer_mode="retrieval_only",
    )

    assert run.question_count == 3
    assert run.aggregate_metrics["questions_with_gold_documents"] == 2
    assert run.aggregate_metrics["abstention_questions"] == 1
    assert run.aggregate_metrics["mean_document_recall"] == 1.0
    first = next(record for record in run.records if record.question_id == "qst_0001")
    assert first.selected_databases == ("erbgithub",)
    assert first.document_ids[0] == "dsid_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert first.answer == ""


def test_emit_answers_schema(tmp_path: Path) -> None:
    questions = erb.load_questions(SMOKE / "questions.jsonl")
    docs = erb.load_documents(SMOKE / "corpus")
    run = erb.run_federated_retrieval(
        questions=questions[:1],
        documents=docs,
        workspace_id="test_ws",
        corpus_root=str(SMOKE / "corpus"),
        questions_file=str(SMOKE / "questions.jsonl"),
        top_k=2,
        answer_mode="extractive",
    )
    out = tmp_path / "answers.jsonl"
    erb._emit_answers(run.records, str(out))

    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["question_id"] == "qst_0001"
    assert row["document_ids"] == ["dsid_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert "upload.rejected_oversized_file" in row["answer"]
