"""FinDER offline contracts; synthetic fixture records are not benchmark evidence."""
from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from seocho.eval.finder_collaboration import (
    RECIPE_PATHS, FinderRetriever, compact, document_members, map_gold,
    normalize_items, parse_filing, prepare_corpus, question_anchors,
    score_report, search_terms, seed_corpus, select_pilot, verify_corpus,
)

HTML = b'''<html><head><title>ignore head</title></head><body>
<div style="display: none"><ix:nonNumeric name="dei:DocumentFiscalYearFocus">2023</ix:nonNumeric>HIDDEN FACT</div>
<h2>Item 8. Financial Statements</h2>
<p>Acme revenue increased.</p><table><tr><th>Year</th><th>Revenue</th></tr>
<tr><td>2023</td><td>120 million USD</td></tr><tr><td>2022</td><td>100 million USD</td></tr></table>
<ix:hidden>hidden inline duplicate</ix:hidden><script>bad script</script><p>Other disclosures.</p></body></html>'''


def row(case_id="one", *, rtype="None", refs=None, question="ACME revenue?"):
    return {"_id": case_id, "text": question, "references": refs or ["Acme revenue increased."],
            "answer": "120 million", "category": "Financials", "type": rtype, "reasoning": rtype != "None"}


def inputs(tmp_path):
    dataset = tmp_path / "qa.json"
    dataset.write_text(json.dumps([
        row("single"), row("multi", refs=["Acme revenue increased.", "Other disclosures."]),
        row("comp", rtype="Compositional"), row("num", rtype="Subtract"),
        row("unresolved", question="Which company has revenue?"),
    ]))
    documents = tmp_path / "10-k.zip"
    with zipfile.ZipFile(documents, "w") as archive:
        archive.writestr("10k/ACME.html", HTML)
        archive.writestr("__MACOSX/10k/._ACME.html", b"ignored")
        archive.writestr("10k/OTHER.html", b"<p>Not selected</p>")
    return dataset, documents


def test_original_filing_text_preserves_table_and_excludes_hidden_content():
    filing = parse_filing(HTML, ticker="ACME", source="archive:ACME")
    assert filing.year == "2023"
    assert "hidden inline duplicate" not in filing.text
    assert "HIDDEN" not in filing.text and "bad script" not in filing.text and "ignore head" not in filing.text
    assert "2023 120 million USD" in filing.text
    assert "2022 100 million USD" in filing.text
    assert all(p.section == "item8" for p in filing.passages)
    assert filing.passages[0].source == "archive:ACME"


def test_archive_ignores_resource_forks_and_never_extracts(tmp_path):
    _, documents = inputs(tmp_path)
    assert document_members(documents) == {"ACME": "10k/ACME.html", "OTHER": "10k/OTHER.html"}
    assert not (tmp_path / "10k").exists()


def test_questions_are_not_confused_with_source_text_and_sampling_is_reproducible():
    raw = [row("one"), row("two", rtype="Compositional"), row("three", rtype="Subtract"),
           row("four", refs=["a", "b"]), row("name_only", question="Acme revenue?")]
    items, excluded = normalize_items(raw, ["ACME"])
    assert len(items) == 4 and excluded == {"unresolved_question_company": 1}
    assert items[0].case.question == "ACME revenue?"
    assert items[0].case.text == "Acme revenue increased."
    assert [i.slice_id for i in select_pilot(items, per_slice=1)] == [
        "single_reference", "multi_reference", "compositional", "numeric"]
    assert select_pilot(list(reversed(items)), per_slice=1) == select_pilot(items, per_slice=1)
    assert question_anchors("IT is a question about A, not ACME?", ["IT", "A", "ACME"]) == ("ACME",)
    assert question_anchors("Which company?", ["ACME"]) == ()
    assert question_anchors("Effective IR increases debt servicing for TMUS", ["IR", "TMUS"]) == ("TMUS",)
    assert question_anchors("IR’s revenue and ticker IT", ["IR", "IT"]) == ("IR", "IT")
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_items([row(), row()], ["ACME"])


def test_prepared_corpus_excludes_all_gold_annotations_from_graph(tmp_path):
    dataset, documents = inputs(tmp_path)
    corpus = prepare_corpus(dataset, documents, per_slice=1)
    assert len(corpus.selected) == 4
    assert len(corpus.filings) == 1
    assert corpus.excluded == {"unresolved_question_company": 1}
    writes = []
    seed_corpus(SimpleNamespace(execute_write=lambda q, **kw: writes.append((q, kw))), corpus, workspace_id="w")
    encoded = json.dumps(writes)
    assert "120 million USD" in encoded
    assert "expected_answer" not in encoded and "_case_id" not in encoded and "question" not in encoded
    assert "single" not in encoded and "Compositional" not in encoded
    assert all(kw["params"]["workspace_id"] == "w" for _, kw in writes)
    for item in corpus.selected:
        assert set(item.public_payload()) == {"question", "anchors", "required_slots"}
        assert "answer" not in item.public_payload()


def test_same_documents_produce_same_corpus_despite_answer_changes(tmp_path):
    dataset, documents = inputs(tmp_path)
    first = prepare_corpus(dataset, documents, per_slice=1)
    rows = json.loads(dataset.read_text())
    for r in rows:
        r["answer"] = "POISONED GOLD"
    dataset.write_text(json.dumps(rows))
    second = prepare_corpus(dataset, documents, per_slice=1)
    assert first.corpus_id == second.corpus_id
    assert first.dataset_sha256 != second.dataset_sha256
    assert [p.graph_row() for f in first.filings for p in f.passages] == [p.graph_row() for f in second.filings for p in f.passages]


def test_gold_mapping_and_evaluation_are_separate_from_retrieval(tmp_path):
    dataset, documents = inputs(tmp_path)
    corpus = prepare_corpus(dataset, documents, per_slice=1)
    item = corpus.selected[0]
    mapping = corpus.gold_mapping[item.case.case_id]
    assert mapping[0]["status"] == "mapped"
    ref = mapping[0]["matches"][0]["evidence_refs"][0]
    report = {"packets": [{"evidence_refs": [ref]}],
              "retrieval_receipts": [{"rows": [{"evidence_id": ref}]}],
              "answer": {"claims": [{"text": "120 million", "evidence_refs": [ref]}]}}
    scores = score_report(report, item, mapping)
    assert scores["retrieved_reference_coverage"] == scores["cited_reference_coverage"] == 1
    assert scores["contains_match_diagnostic"] is True
    assert scores["answer_correctness"] is None
    scores = score_report({"packets": [], "retrieval_receipts": [], "answer": None}, item,
                          [{"reference_index": 0, "status": "unmapped", "matches": []}])
    assert scores["retrieved_reference_coverage"] is None
    assert scores["unmapped_reference_count"] == 1


def test_chunk_overlap_does_not_require_redundant_citations():
    items, _ = normalize_items([row(refs=["needle"])], ["ACME"])
    mapping = [{"reference_index": 0, "status": "mapped", "matches": [{
        "ticker": "ACME", "gold_start": 10, "gold_end": 20,
        "spans": [{"evidence_id": "p1", "start": 10, "end": 20},
                  {"evidence_id": "p2", "start": 10, "end": 20}],
    }]}]
    report = {"packets": [{"evidence_refs": ["p1"]}], "retrieval_receipts": [],
              "answer": {"claims": [{"text": "120 million", "evidence_refs": ["p1"]}]}}
    assert score_report(report, items[0], mapping)["cited_reference_coverage"] == 1


def test_chunk_mapping_matches_boundaries_and_repeated_occurrences():
    body = "a " * 400 + "needle evidence" + " b" * 400 + "needle evidence"
    filing = parse_filing(f"<p>{body}</p>".encode(), ticker="ACME", source="s", chunk_chars=500, overlap_chars=50)
    item = normalize_items([row(refs=["needle evidence"])], ["ACME"])[0][0]
    mapping = map_gold(item, {"ACME": filing})
    assert len(mapping[0]["matches"]) == 2
    assert compact(filing.text) == filing.normalized_text
    assert all(filing.normalized_text[p.normalized_start:p.normalized_end] == compact(p.text) for p in filing.passages)


@pytest.mark.parametrize("recipe", list(RECIPE_PATHS))
def test_financial_retrieval_validates_scope_and_uses_only_question_or_search_terms(recipe):
    calls = []

    def query(text, **kwargs):
        calls.append(kwargs)
        assert all(f"{v}._workspace_id = $workspace_id" in text for v in "cfs pabd".replace(" ", ""))
        assert kwargs["workspace_id"] == "w" and kwargs["enforce_workspace_filter"]
        assert kwargs["params"]["corpus_id"] == "corpus"
        assert "LIMIT 4" in text and "gold" not in text
        return []
    retriever = FinderRetriever(SimpleNamespace(query=query), workspace_id="w", corpus_id="corpus",
                                question="ACME revenue growth 2023")
    retriever.search(recipe, "ACME", "operating income 2022")
    assert calls[0]["params"]["terms"] == ["operating", "income", "2022"]
    retriever(recipe, "ACME")
    assert "revenue" in calls[1]["params"]["terms"]
    assert search_terms("What is the fiscal year revenue revenue?") == ["revenue"]
    with pytest.raises(ValueError, match="Unknown"):
        retriever("write_query", "ACME")


def test_preflight_rejects_wrong_source_and_broken_topology(tmp_path):
    dataset, documents = inputs(tmp_path)
    corpus = prepare_corpus(dataset, documents, per_slice=1)
    inventory = [{key: getattr(p, key) for key in ("evidence_id", "text", "source", "ordinal", "start", "end")}
                 for f in corpus.filings for p in f.passages]
    paths = [{"ticker": f.ticker, "filing_id": f.filing_id, "year": f.year,
              "section": p.section, "evidence_id": p.evidence_id} for f in corpus.filings for p in f.passages]

    def store(inventory_rows, path_rows):
        return SimpleNamespace(query=lambda q, **kw: path_rows if "HAS_FILING" in q else inventory_rows)
    verify_corpus(store(inventory, paths), corpus, workspace_id="w")
    with pytest.raises(ValueError, match="absent, partial, or changed"):
        verify_corpus(store([], paths), corpus, workspace_id="w")
    with pytest.raises(ValueError, match="topology"):
        verify_corpus(store(inventory, []), corpus, workspace_id="w")


def test_cli_dry_run_needs_no_service_and_saves_manifest(tmp_path, monkeypatch):
    dataset, documents = inputs(tmp_path)
    output = tmp_path / "report.json"
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("finder_cli", root / "examples/graphrag-collaboration/run_finder.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("sys.argv", ["run_finder.py", "--dataset", str(dataset), "--documents", str(documents),
                                     "--dry-run", "--per-slice", "1", "--output", str(output)])
    monkeypatch.setattr(module, "Neo4jGraphStore", lambda *a, **kw: pytest.fail("No graph call allowed"))
    assert module.main() == 0
    report = json.loads(output.read_text())
    assert report["reports"] == [] and len(report["manifest"]["cases"]) == 4


def test_finder_worker_searches_subquestion_without_gold_input(tmp_path, monkeypatch):
    import asyncio
    from agents.tool_context import ToolContext
    from seocho.agent.collaboration import Answer, Claim, CollaborationExperiment, Selection

    dataset, documents = inputs(tmp_path)
    corpus = prepare_corpus(dataset, documents, per_slice=1)
    item = corpus.selected[0]
    item.case.expected_answer = "EVALUATION_ONLY_SENTINEL"
    passage = corpus.filings[0].passages[0]
    searches = []

    def query(text, **kwargs):
        searches.append(kwargs["params"]["terms"])
        return [{"evidence_id": passage.evidence_id, "text": passage.text, "source": passage.source}]

    async def runner(agent, text, **kwargs):
        assert "EVALUATION_ONLY_SENTINEL" not in text
        assert "gold_mapping" not in text and "references" not in text
        if agent.name == "RetrievalWorker":
            context = ToolContext(context=None, tool_name="retrieve_evidence", tool_call_id="test", tool_arguments="{}")
            found = await agent.tools[0].on_invoke_tool(context, json.dumps({
                "recipe_id": "financial_passages", "node_id": "ACME", "search_text": "revenue 2023"}))
            assert json.loads(found)[0]["source"] == passage.source
            output = Selection(evidence_refs=[passage.evidence_id], missing_slots=[])
        else:
            output = Answer(claims=[Claim(text="120 million USD in 2023", evidence_refs=[passage.evidence_id])],
                            missing_slots=[])
        return SimpleNamespace(final_output=output)

    monkeypatch.setattr("seocho.agent.collaboration.Runner.run", runner)
    retriever = FinderRetriever(SimpleNamespace(query=query), workspace_id="w", corpus_id=corpus.corpus_id,
                                question=item.case.question)
    result = asyncio.run(CollaborationExperiment(model=None, retrieve=retriever, retrieve_search=retriever.search,
                                                 recipes=RECIPE_PATHS, workspace_id="w").run(
        **item.public_payload(), mode="single"))
    assert result["status"] == "completed", result["error"]
    assert searches == [["revenue", "2023"]]
    assert result["retrieval_receipts"][0]["search_text"] == "revenue 2023"
