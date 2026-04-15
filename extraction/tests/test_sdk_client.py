import os
import sys
from typing import Any, Dict, List, Optional

import pytest
import requests


ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import seocho as seocho_module
from seocho import (
    ApprovedArtifacts,
    EntityOverride,
    GraphRef,
    KnownEntity,
    OntologyCandidate,
    OntologyClass,
    OntologyProperty,
    Seocho,
    SemanticArtifactDraftInput,
    SemanticPromptContext,
    ShaclCandidate,
    ShaclPropertyConstraint,
    ShaclShape,
    VocabularyCandidate,
    VocabularyTerm,
)
from seocho.exceptions import SeochoConnectionError, SeochoHTTPError
from seocho.models import RawIngestResult


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: Optional[Dict[str, Any]] = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Dict[str, Any]:
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class _FakeSession:
    def __init__(self, responses: List[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, url: str, json=None, params=None, timeout=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "params": params,
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError("No fake responses left")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def test_raw_ingest_result_parses_domain_status_contract():
    success = RawIngestResult.from_dict(
        {
            "workspace_id": "default",
            "target_database": "kgnormal",
            "records_received": 1,
            "records_processed": 1,
            "records_failed": 0,
            "total_nodes": 2,
            "total_relationships": 1,
            "status": "success",
        }
    )
    failed = RawIngestResult.from_dict(
        {
            "ok": False,
            "workspace_id": "default",
            "target_database": "kgnormal",
            "records_received": 1,
            "records_processed": 0,
            "records_failed": 1,
            "total_nodes": 0,
            "total_relationships": 0,
            "status": "failed",
            "domain_error": "status=failed, records_processed=0, records_failed=1",
        }
    )

    assert success.ok is True
    assert success.domain_error == ""
    assert failed.ok is False
    assert failed.domain_error == "status=failed, records_processed=0, records_failed=1"


def test_add_search_chat_and_graphs_use_public_api_contract():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "memory": {
                        "memory_id": "mem_1",
                        "workspace_id": "default",
                        "content": "Alice manages Seoul retail.",
                        "metadata": {"source": "sdk"},
                        "status": "stored",
                        "created_at": "2026-03-13T00:00:00Z",
                        "updated_at": "2026-03-13T00:00:00Z",
                        "database": "kgnormal",
                    },
                    "ingest_summary": {"records_processed": 1},
                    "trace_id": "tr_1",
                }
            ),
            _FakeResponse(
                payload={
                    "results": [
                        {
                            "memory_id": "mem_1",
                            "content": "Alice manages Seoul retail.",
                            "content_preview": "Alice manages Seoul retail.",
                            "metadata": {"source": "sdk"},
                            "score": 0.93,
                            "reasons": ["entity_match"],
                            "matched_entities": ["Seoul"],
                            "database": "kgnormal",
                            "status": "active",
                            "evidence_bundle": {"intent_id": "responsibility_lookup"},
                        }
                    ],
                    "semantic_context": {"entities": ["Seoul"]},
                    "ontology_context_mismatch": {"mismatch": False, "databases": []},
                    "trace_id": "tr_2",
                }
            ),
            _FakeResponse(
                payload={
                    "assistant_message": "Alice manages Seoul retail.",
                    "memory_hits": [{"memory_id": "mem_1", "score": 0.93, "database": "kgnormal"}],
                    "search_results": [
                        {
                            "memory_id": "mem_1",
                            "content": "Alice manages Seoul retail.",
                            "content_preview": "Alice manages Seoul retail.",
                            "metadata": {"source": "sdk"},
                            "score": 0.93,
                            "reasons": ["entity_match"],
                            "matched_entities": ["Seoul"],
                            "database": "kgnormal",
                            "status": "active",
                            "evidence_bundle": {"intent_id": "responsibility_lookup"},
                        }
                    ],
                    "semantic_context": {"entities": ["Seoul"]},
                    "evidence_bundle": {"intent_id": "responsibility_lookup", "missing_slots": []},
                    "ontology_context_mismatch": {"mismatch": True, "databases": []},
                    "trace_id": "tr_3",
                }
            ),
            _FakeResponse(
                payload={
                    "graphs": [
                        {
                            "graph_id": "kgnormal",
                            "database": "kgnormal",
                            "uri": "bolt://neo4j:7687",
                            "ontology_id": "baseline",
                            "vocabulary_profile": "vocabulary.v2",
                            "description": "Baseline graph",
                            "workspace_scope": "default",
                        }
                    ]
                }
            ),
        ]
    )
    client = Seocho(
        base_url="http://localhost:8001",
        workspace_id="default",
        user_id="alex",
        session=session,
    )

    memory = client.add("Alice manages Seoul retail.", metadata={"source": "sdk"})
    search = client.search_with_context("Who manages Seoul retail?", graph_ids=["kgnormal"])
    results = search.results
    answer = client.chat("What do we know about Seoul retail?", graph_ids=["kgnormal"])
    graphs = client.graphs()

    assert memory.memory_id == "mem_1"
    assert results[0].matched_entities == ["Seoul"]
    assert results[0].evidence_bundle["intent_id"] == "responsibility_lookup"
    assert search.ontology_context_mismatch["mismatch"] is False
    assert answer.assistant_message == "Alice manages Seoul retail."
    assert answer.ontology_context_mismatch["mismatch"] is True
    assert answer.evidence_bundle["intent_id"] == "responsibility_lookup"
    assert graphs[0].graph_id == "kgnormal"

    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"] == "http://localhost:8001/api/memories"
    assert session.calls[0]["json"]["user_id"] == "alex"
    assert session.calls[1]["json"]["graph_ids"] == ["kgnormal"]
    assert session.calls[2]["url"] == "http://localhost:8001/api/chat"
    assert session.calls[3]["url"] == "http://localhost:8001/graphs"


def test_get_delete_and_ask_return_convenience_shapes():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "memory": {
                        "memory_id": "mem_1",
                        "workspace_id": "default",
                        "content": "Stored memory",
                        "metadata": {},
                        "status": "active",
                        "created_at": "2026-03-13T00:00:00Z",
                        "updated_at": "2026-03-13T00:00:00Z",
                    },
                    "trace_id": "tr_get",
                }
            ),
            _FakeResponse(
                payload={
                    "assistant_message": "Stored memory",
                    "memory_hits": [],
                    "search_results": [],
                    "semantic_context": {},
                    "trace_id": "tr_chat",
                }
            ),
            _FakeResponse(
                payload={
                    "memory_id": "mem_1",
                    "workspace_id": "default",
                    "database": "kgnormal",
                    "status": "archived",
                    "archived_at": "2026-03-13T00:00:00Z",
                    "archived_nodes": 3,
                    "trace_id": "tr_del",
                }
            ),
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    memory = client.get("mem_1")
    answer = client.ask("What do we know?")
    archived = client.delete("mem_1")

    assert memory.content == "Stored memory"
    assert answer == "Stored memory"
    assert archived.archived_nodes == 3
    assert session.calls[0]["params"]["workspace_id"] == "default"


def test_http_errors_are_promoted_to_sdk_exceptions():
    session = _FakeSession(
        [
            _FakeResponse(status_code=400, payload={"detail": "bad request"}),
        ]
    )
    client = Seocho(session=session)

    with pytest.raises(SeochoHTTPError, match="bad request"):
        client.search("bad query")


def test_connection_errors_are_promoted_to_sdk_exceptions(monkeypatch):
    class _BrokenSession:
        def request(self, *args, **kwargs):
            raise requests.RequestException("connection refused")

        def close(self) -> None:
            return None

    client = Seocho(session=_BrokenSession())

    with pytest.raises(SeochoConnectionError, match="Could not reach SEOCHO"):
        client.graphs()


def test_add_with_details_supports_prompt_context_and_approved_artifacts():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "memory": {
                        "memory_id": "mem_advanced",
                        "workspace_id": "default",
                        "content": "Advanced prompt memory",
                        "metadata": {"source": "sdk"},
                        "status": "stored",
                        "created_at": "2026-03-13T00:00:00Z",
                        "updated_at": "2026-03-13T00:00:00Z",
                    },
                    "ingest_summary": {"records_processed": 1},
                    "trace_id": "tr_advanced",
                }
            )
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    client.add_with_details(
        "Advanced prompt memory",
        metadata={"source": "sdk"},
        prompt_context={
            "instructions": ["Prefer the customer ontology."],
            "vocabulary_candidate": {"terms": [{"pref_label": "Customer"}]},
        },
        approved_artifact_id="sa_approved_1",
    )

    request_body = session.calls[0]["json"]
    assert request_body["approved_artifact_id"] == "sa_approved_1"
    assert request_body["metadata"]["semantic_prompt_context"]["instructions"] == [
        "Prefer the customer ontology."
    ]


def test_add_with_details_accepts_typed_prompt_context_and_artifact_payloads():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "memory": {
                        "memory_id": "mem_typed",
                        "workspace_id": "default",
                        "content": "Typed prompt memory",
                        "metadata": {"source": "sdk"},
                        "status": "stored",
                        "created_at": "2026-03-13T00:00:00Z",
                        "updated_at": "2026-03-13T00:00:00Z",
                    },
                    "ingest_summary": {"records_processed": 1},
                    "trace_id": "tr_typed",
                }
            )
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    client.add_with_details(
        "Typed prompt memory",
        metadata={"source": "sdk"},
        prompt_context=SemanticPromptContext(
            instructions=["Prefer approved ontology labels."],
            known_entities=[KnownEntity(name="ACME Holdings", label="Company")],
            vocabulary_candidate=VocabularyCandidate(
                terms=[VocabularyTerm(pref_label="Retail Account", alt_labels=["Account"])]
            ),
        ),
        approved_artifacts=ApprovedArtifacts(
            ontology_candidate=OntologyCandidate(
                ontology_name="customer",
                classes=[
                    OntologyClass(
                        name="RetailAccount",
                        properties=[OntologyProperty(name="owner", datatype="string")],
                    )
                ],
            ),
            shacl_candidate=ShaclCandidate(
                shapes=[
                    ShaclShape(
                        target_class="RetailAccount",
                        properties=[ShaclPropertyConstraint(path="owner", constraint="required")],
                    )
                ]
            ),
        ),
    )

    request_body = session.calls[0]["json"]
    assert request_body["approved_artifacts"]["ontology_candidate"]["ontology_name"] == "customer"
    assert request_body["metadata"]["semantic_prompt_context"]["known_entities"][0]["name"] == "ACME Holdings"
    assert request_body["metadata"]["semantic_prompt_context"]["vocabulary_candidate"]["terms"][0]["pref_label"] == "Retail Account"


def test_artifact_client_methods_use_expert_api_surface():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "artifacts": [
                        {
                            "artifact_id": "sa_1",
                            "workspace_id": "default",
                            "name": "finance_v1",
                            "created_at": "2026-03-13T00:00:00Z",
                            "status": "approved",
                            "approved_at": "2026-03-13T01:00:00Z",
                            "approved_by": "reviewer",
                        }
                    ]
                }
            ),
            _FakeResponse(
                payload={
                    "workspace_id": "default",
                    "artifact_id": "sa_1",
                    "name": "finance_v1",
                    "status": "approved",
                    "created_at": "2026-03-13T00:00:00Z",
                    "approved_at": "2026-03-13T01:00:00Z",
                    "approved_by": "reviewer",
                    "source_summary": {},
                    "ontology_candidate": {"ontology_name": "finance", "classes": [], "relationships": []},
                    "shacl_candidate": {"shapes": []},
                    "vocabulary_candidate": {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
                }
            ),
            _FakeResponse(
                payload={
                    "workspace_id": "default",
                    "artifact_id": "sa_2",
                    "name": "finance_v2",
                    "status": "draft",
                    "created_at": "2026-03-13T02:00:00Z",
                    "source_summary": {},
                    "ontology_candidate": {"ontology_name": "finance", "classes": [], "relationships": []},
                    "shacl_candidate": {"shapes": []},
                    "vocabulary_candidate": {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
                }
            ),
            _FakeResponse(
                payload={
                    "workspace_id": "default",
                    "artifact_id": "sa_2",
                    "name": "finance_v2",
                    "status": "approved",
                    "created_at": "2026-03-13T02:00:00Z",
                    "approved_at": "2026-03-13T03:00:00Z",
                    "approved_by": "reviewer",
                    "source_summary": {},
                    "ontology_candidate": {"ontology_name": "finance", "classes": [], "relationships": []},
                    "shacl_candidate": {"shapes": []},
                    "vocabulary_candidate": {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
                }
            ),
            _FakeResponse(
                payload={
                    "workspace_id": "default",
                    "artifact_id": "sa_2",
                    "name": "finance_v2",
                    "status": "deprecated",
                    "created_at": "2026-03-13T02:00:00Z",
                    "deprecated_at": "2026-03-13T04:00:00Z",
                    "deprecated_by": "reviewer",
                    "source_summary": {},
                    "ontology_candidate": {"ontology_name": "finance", "classes": [], "relationships": []},
                    "shacl_candidate": {"shapes": []},
                    "vocabulary_candidate": {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
                }
            ),
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    draft_input = SemanticArtifactDraftInput(
        name="finance_v2",
        ontology_candidate=OntologyCandidate(ontology_name="finance"),
        shacl_candidate=ShaclCandidate(),
    )

    listed = client.list_artifacts(status="approved")
    fetched = client.get_artifact("sa_1")
    created = client.create_artifact_draft(draft_input)
    approved = client.approve_artifact("sa_2", approved_by="reviewer")
    deprecated = client.deprecate_artifact("sa_2", deprecated_by="reviewer")

    assert listed[0].artifact_id == "sa_1"
    assert fetched.artifact_id == "sa_1"
    assert created.status == "draft"
    assert approved.status == "approved"
    assert deprecated.status == "deprecated"

    assert session.calls[0]["url"] == "http://localhost:8001/semantic/artifacts"
    assert session.calls[0]["params"]["status"] == "approved"
    assert session.calls[1]["url"] == "http://localhost:8001/semantic/artifacts/sa_1"
    assert session.calls[2]["url"] == "http://localhost:8001/semantic/artifacts/drafts"
    assert session.calls[2]["json"]["name"] == "finance_v2"
    assert session.calls[3]["url"] == "http://localhost:8001/semantic/artifacts/sa_2/approve"
    assert session.calls[4]["url"] == "http://localhost:8001/semantic/artifacts/sa_2/deprecate"


def test_apply_artifact_posts_memory_with_approved_only_policy():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "memory": {
                        "memory_id": "mem_apply",
                        "workspace_id": "default",
                        "content": "ACME acquired Beta in 2024.",
                        "metadata": {"source": "sdk"},
                        "status": "stored",
                        "created_at": "2026-03-13T00:00:00Z",
                        "updated_at": "2026-03-13T00:00:00Z",
                    },
                    "ingest_summary": {"records_processed": 1},
                    "trace_id": "tr_apply",
                }
            )
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    created = client.apply_artifact(
        "sa_approved_finance_v1",
        "ACME acquired Beta in 2024.",
        metadata={"source": "sdk"},
    )

    assert created.memory.memory_id == "mem_apply"
    request_body = session.calls[0]["json"]
    assert request_body["approved_artifact_id"] == "sa_approved_finance_v1"
    assert request_body["semantic_artifact_policy"] == "approved_only"


def test_validate_and_diff_artifact_helpers_are_available_locally():
    client = Seocho()

    valid = client.validate_artifact(
        SemanticArtifactDraftInput(
            name="finance_v1",
            ontology_candidate=OntologyCandidate(
                ontology_name="finance",
                classes=[OntologyClass(name="Company")],
            ),
            shacl_candidate=ShaclCandidate(
                shapes=[ShaclShape(target_class="Company")]
            ),
            vocabulary_candidate=VocabularyCandidate(
                terms=[VocabularyTerm(pref_label="Issuer")]
            ),
        )
    )
    invalid = client.validate_artifact(
        {
            "name": "broken",
            "ontology_candidate": {"ontology_name": "", "classes": [{"name": ""}], "relationships": []},
            "shacl_candidate": {"shapes": [{"target_class": "", "properties": []}]},
            "vocabulary_candidate": {"schema_version": "vocabulary.v2", "profile": "skos", "terms": [{"pref_label": ""}]},
        }
    )
    diff = client.diff_artifacts(
        {
            "name": "finance_v1",
            "ontology_candidate": {"ontology_name": "finance", "classes": [{"name": "Company"}], "relationships": []},
            "shacl_candidate": {"shapes": [{"target_class": "Company", "properties": []}]},
            "vocabulary_candidate": {"schema_version": "vocabulary.v2", "profile": "skos", "terms": [{"pref_label": "Issuer"}]},
        },
        {
            "name": "finance_v2",
            "ontology_candidate": {
                "ontology_name": "finance",
                "classes": [{"name": "Company"}, {"name": "Subsidiary"}],
                "relationships": [{"type": "OWNS", "source": "Company", "target": "Subsidiary"}],
            },
            "shacl_candidate": {
                "shapes": [
                    {"target_class": "Company", "properties": []},
                    {"target_class": "Subsidiary", "properties": []},
                ]
            },
            "vocabulary_candidate": {
                "schema_version": "vocabulary.v2",
                "profile": "skos",
                "terms": [{"pref_label": "Issuer"}, {"pref_label": "Subsidiary"}],
            },
        },
    )

    assert valid.ok is True
    assert invalid.ok is False
    assert any(item.code == "ontology.class_name_missing" for item in invalid.errors)
    assert diff.summary["classes_added"] == 1
    assert diff.summary["relationships_added"] == 1
    assert diff.summary["terms_added"] == 1


def test_client_can_build_runtime_artifacts_from_registered_ontology():
    from seocho.ontology import NodeDef, Ontology, P, RelDef

    ontology = Ontology(
        name="customer_graph",
        package_id="customer.core",
        nodes={
            "Customer": NodeDef(
                aliases=["AccountHolder"],
                properties={"name": P(str, unique=True, aliases=["customer_name"])},
            ),
            "Account": NodeDef(properties={"number": P(str, unique=True)}),
        },
        relationships={
            "OWNS": RelDef(source="Customer", target="Account", cardinality="ONE_TO_MANY"),
        },
    )
    client = Seocho(ontology=ontology)

    artifacts = client.approved_artifacts_from_ontology()
    prompt_context = client.prompt_context_from_ontology()
    draft = client.artifact_draft_from_ontology()

    assert artifacts.ontology_candidate is not None
    assert artifacts.ontology_candidate.ontology_name == "customer_graph"
    assert artifacts.vocabulary_candidate is not None
    assert any(term.pref_label == "Customer" for term in artifacts.vocabulary_candidate.terms)
    assert prompt_context.ontology_candidate is not None
    assert draft.source_summary["package_id"] == "customer.core"


def test_runtime_client_methods_cover_semantic_debate_platform_and_admin_surfaces():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "response": "router answer",
                    "trace_steps": [{"type": "GENERATION"}],
                    "ontology_context_mismatch": {"mismatch": False, "databases": []},
                }
            ),
            _FakeResponse(
                payload={
                    "response": "semantic answer",
                    "trace_steps": [{"type": "SEMANTIC"}],
                    "route": "lpg",
                    "semantic_context": {
                        "entities": ["Neo4j"],
                        "matches": {"Neo4j": [{"database": "kgnormal"}]},
                        "unresolved_entities": [],
                    },
                    "ontology_context_mismatch": {"mismatch": True, "databases": []},
                    "lpg_result": {"records": []},
                    "rdf_result": None,
                }
            ),
            _FakeResponse(
                payload={
                    "response": "debate answer",
                    "trace_steps": [{"type": "SYSTEM"}],
                    "debate_results": [{"graph": "kgnormal", "response": "agent answer"}],
                    "agent_statuses": [{"graph": "kgnormal", "status": "ready", "reason": "checked"}],
                    "debate_state": "ready",
                    "degraded": False,
                    "ontology_context_mismatch": {"mismatch": False, "databases": []},
                }
            ),
            _FakeResponse(
                payload={
                    "session_id": "s1",
                    "mode": "debate",
                    "assistant_message": "platform answer",
                    "trace_steps": [{"type": "GENERATION"}],
                    "ui_payload": {"cards": []},
                    "runtime_payload": {
                        "response": "platform answer",
                        "debate_state": "ready",
                        "ontology_context_mismatch": {"mismatch": False, "databases": []},
                    },
                    "ontology_context_mismatch": {"mismatch": False, "databases": []},
                    "history": [
                        {"role": "user", "content": "hello", "metadata": {}},
                        {"role": "assistant", "content": "platform answer", "metadata": {}},
                    ],
                }
            ),
            _FakeResponse(
                payload={
                    "session_id": "s1",
                    "history": [{"role": "user", "content": "hello", "metadata": {}}],
                }
            ),
            _FakeResponse(
                payload={
                    "session_id": "s1",
                    "history": [],
                }
            ),
            _FakeResponse(
                payload={
                    "workspace_id": "default",
                    "target_database": "kgnormal",
                    "records_received": 1,
                    "records_processed": 1,
                    "records_failed": 0,
                    "total_nodes": 3,
                    "total_relationships": 1,
                    "status": "success",
                    "warnings": [],
                    "errors": [],
                }
            ),
            _FakeResponse(payload={"databases": ["kgnormal", "kgfinance"]}),
            _FakeResponse(payload={"agents": ["kgnormal", "kgfinance"]}),
            _FakeResponse(
                payload={
                    "results": [
                        {
                            "database": "kgnormal",
                            "index_name": "entity_fulltext",
                            "exists": True,
                            "created": False,
                            "state": "ONLINE",
                            "labels": ["Entity"],
                            "properties": ["name"],
                            "message": "ok",
                        }
                    ]
                }
            ),
        ]
    )
    client = Seocho(base_url="http://localhost:8001", workspace_id="default", user_id="alex", session=session)

    routed = client.router("hello")
    semantic = client.semantic(
        "Tell me about Neo4j",
        databases=["kgnormal"],
        entity_overrides=[EntityOverride(question_entity="Neo4j", database="kgnormal", node_id=1)],
        reasoning_mode=True,
        repair_budget=2,
    )
    debated = client.debate("Compare graphs", graph_ids=["kgnormal"])
    platform = client.platform_chat("hello", mode="debate", session_id="s1", graph_ids=["kgnormal"])
    history = client.session_history("s1")
    reset = client.reset_session("s1")
    ingested = client.raw_ingest(
        [{"id": "r1", "content": "Alpha acquired Beta."}],
        target_database="kgnormal",
    )
    databases = client.databases()
    agents = client.agents()
    fulltext = client.ensure_fulltext_indexes(databases=["kgnormal"])

    assert routed.response == "router answer"
    assert routed.ontology_context_mismatch["mismatch"] is False
    assert semantic.route == "lpg"
    assert semantic.semantic_context["entities"] == ["Neo4j"]
    assert semantic.ontology_context_mismatch["mismatch"] is True
    assert debated.debate_results[0]["graph"] == "kgnormal"
    assert debated.ontology_context_mismatch["mismatch"] is False
    assert platform.history[1].content == "platform answer"
    assert platform.ontology_context_mismatch["mismatch"] is False
    assert history.history[0].role == "user"
    assert reset.history == []
    assert ingested.records_processed == 1
    assert databases == ["kgnormal", "kgfinance"]
    assert agents == ["kgnormal", "kgfinance"]
    assert fulltext.results[0].database == "kgnormal"

    assert session.calls[0]["url"] == "http://localhost:8001/run_agent"
    assert session.calls[1]["url"] == "http://localhost:8001/run_agent_semantic"
    assert session.calls[1]["json"]["entity_overrides"][0]["question_entity"] == "Neo4j"
    assert session.calls[1]["json"]["reasoning_mode"] is True
    assert session.calls[1]["json"]["repair_budget"] == 2
    assert session.calls[2]["url"] == "http://localhost:8001/run_debate"
    assert session.calls[3]["url"] == "http://localhost:8001/platform/chat/send"
    assert session.calls[6]["url"] == "http://localhost:8001/platform/ingest/raw"
    assert session.calls[9]["url"] == "http://localhost:8001/indexes/fulltext/ensure"


def test_module_level_convenience_api_uses_configured_default_client():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "assistant_message": "Stored memory",
                    "memory_hits": [],
                    "search_results": [],
                    "semantic_context": {},
                    "trace_id": "tr_chat",
                }
            ),
            _FakeResponse(
                payload={
                    "response": "debate answer",
                    "trace_steps": [],
                    "debate_results": [],
                    "agent_statuses": [],
                    "debate_state": "blocked",
                    "degraded": True,
                }
            ),
            _FakeResponse(payload={"databases": ["kgnormal"]}),
        ]
    )

    seocho_module.close()
    seocho_module.configure(
        base_url="http://localhost:8001",
        workspace_id="default",
        user_id="alex",
        session=session,
    )
    try:
        answer = seocho_module.ask("What do we know?")
        debate = seocho_module.debate("Compare graphs")
        databases = seocho_module.databases()
    finally:
        seocho_module.close()

    assert answer == "Stored memory"
    assert debate.debate_state == "blocked"
    assert databases == ["kgnormal"]



def test_advanced_alias_uses_debate_endpoint_directly():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "response": "advanced debate answer",
                    "trace_steps": [],
                    "debate_results": [{"graph": "kgnormal", "response": "A"}],
                    "agent_statuses": [{"graph": "kgnormal", "status": "ready"}],
                    "debate_state": "ready",
                    "degraded": False,
                }
            )
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    result = client.advanced("Hard graph question", graph_ids=["kgnormal"])

    assert result.debate_state == "ready"
    assert session.calls[0]["url"] == "http://localhost:8001/run_debate"
    assert session.calls[0]["json"]["graph_ids"] == ["kgnormal"]


def test_semantic_accepts_graph_ids_and_resolves_them_to_databases():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "graphs": [
                        {
                            "graph_id": "kgnormal",
                            "database": "kgnormal",
                            "uri": "bolt://neo4j:7687",
                            "ontology_id": "baseline",
                            "vocabulary_profile": "vocabulary.v2",
                            "description": "Baseline graph",
                            "workspace_scope": "default",
                        }
                    ]
                }
            ),
            _FakeResponse(
                payload={
                    "response": "semantic answer",
                    "trace_steps": [{"type": "SEMANTIC"}],
                    "route": "lpg",
                    "semantic_context": {"entities": ["Seoul"]},
                    "lpg_result": {"records": []},
                    "rdf_result": None,
                }
            ),
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    result = client.semantic("Who manages Seoul retail?", graph_ids=[GraphRef(graph_id="kgnormal")])

    assert result.route == "lpg"
    assert session.calls[0]["url"] == "http://localhost:8001/graphs"
    assert session.calls[1]["url"] == "http://localhost:8001/run_agent_semantic"
    assert session.calls[1]["json"]["databases"] == ["kgnormal"]


def test_execution_plan_builder_defaults_to_semantic_and_advanced_remains_explicit():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "graphs": [
                        {
                            "graph_id": "kgnormal",
                            "database": "kgnormal",
                            "uri": "bolt://neo4j:7687",
                            "ontology_id": "baseline",
                            "vocabulary_profile": "vocabulary.v2",
                            "description": "Baseline graph",
                            "workspace_scope": "default",
                        }
                    ]
                }
            ),
            _FakeResponse(
                payload={
                    "response": "semantic answer",
                    "trace_steps": [{"type": "SEMANTIC"}],
                    "route": "lpg",
                    "semantic_context": {"entities": ["Alex"]},
                    "lpg_result": {"records": []},
                    "rdf_result": None,
                    "ontology_context_mismatch": {"mismatch": False, "databases": []},
                }
            ),
            _FakeResponse(
                payload={
                    "response": "advanced debate answer",
                    "trace_steps": [],
                    "debate_results": [{"graph": "kgnormal", "response": "A"}],
                    "agent_statuses": [{"graph": "kgnormal", "status": "ready"}],
                    "debate_state": "ready",
                    "degraded": False,
                    "ontology_context_mismatch": {"mismatch": True, "databases": []},
                }
            ),
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    semantic_result = client.plan("What do you know about Alex?").on_graph("kgnormal").run()
    advanced_result = client.plan("Hard graph question").on_graph("kgnormal").advanced().run()

    assert semantic_result.route == "lpg"
    assert semantic_result.ontology_context_mismatch["mismatch"] is False
    assert advanced_result.debate_state == "ready"
    assert advanced_result.ontology_context_mismatch["mismatch"] is True
    assert session.calls[1]["url"] == "http://localhost:8001/run_agent_semantic"
    assert session.calls[2]["url"] == "http://localhost:8001/run_debate"


def test_execution_plan_builder_passes_semantic_repair_budget():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "graphs": [
                        {
                            "graph_id": "kgnormal",
                            "database": "kgnormal",
                            "uri": "bolt://neo4j:7687",
                            "ontology_id": "baseline",
                            "vocabulary_profile": "vocabulary.v2",
                            "description": "Baseline graph",
                            "workspace_scope": "default",
                        }
                    ]
                }
            ),
            _FakeResponse(
                payload={
                    "response": "semantic answer",
                    "trace_steps": [{"type": "SEMANTIC"}],
                    "route": "lpg",
                    "semantic_context": {"entities": ["Alex"], "reasoning": {"requested": True, "attempt_count": 2}},
                    "lpg_result": {"records": []},
                    "rdf_result": None,
                }
            ),
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    result = client.plan("What do you know about Alex?").on_graph("kgnormal").with_repair_budget(2).run()

    assert result.route == "lpg"
    assert session.calls[1]["json"]["reasoning_mode"] is True
    assert session.calls[1]["json"]["repair_budget"] == 2


def test_semantic_response_exposes_support_strategy_run_and_evidence_helpers():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "response": "semantic answer",
                    "trace_steps": [{"type": "SEMANTIC"}],
                    "route": "lpg",
                    "semantic_context": {
                        "entities": ["Neo4j"],
                        "support_assessment": {"intent_id": "relationship_lookup", "status": "supported"},
                    },
                    "lpg_result": {"records": [{"target_entity": "Cypher"}]},
                    "rdf_result": None,
                    "support_assessment": {
                        "intent_id": "relationship_lookup",
                        "supported": True,
                        "status": "supported",
                        "reason": "sufficient",
                        "graph_id": "kgnormal",
                        "database": "kgnormal",
                        "coverage": 1.0,
                        "grounded_slots": ["source_entity", "target_entity", "relation_paths"],
                        "missing_slots": [],
                    },
                    "strategy_decision": {
                        "requested_mode": "semantic",
                        "initial_mode": "semantic_direct",
                        "executed_mode": "semantic_direct",
                        "support_status": "supported",
                        "reason": "intent support is available for the selected graph scope",
                    },
                    "run_metadata": {
                        "run_id": "run_123",
                        "recorded": True,
                        "registry_path": "/tmp/seocho/semantic_run_registry.jsonl",
                        "timestamp": "2026-04-11T10:00:00Z",
                    },
                    "evidence_bundle": {
                        "intent_id": "relationship_lookup",
                        "focus_slots": ["source_entity", "target_entity", "relation_paths"],
                        "grounded_slots": ["source_entity", "target_entity", "relation_paths"],
                        "missing_slots": [],
                        "slot_fills": {"source_entity": "Neo4j", "target_entity": "Cypher"},
                        "selected_triples": [{"source": "Neo4j", "relation": "USES", "target": "Cypher"}],
                        "confidence": 0.99,
                        "coverage": 1.0,
                        "database": "kgnormal",
                        "graph_id": "kgnormal",
                    },
                }
            )
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    result = client.semantic("What is Neo4j connected to?", databases=["kgnormal"])

    assert result.support.supported is True
    assert result.strategy.executed_mode == "semantic_direct"
    assert result.run_record.run_id == "run_123"
    assert result.evidence.graph_id == "kgnormal"


def test_semantic_run_registry_endpoints_return_typed_records():
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "runs": [
                        {
                            "run_id": "run_123",
                            "workspace_id": "default",
                            "timestamp": "2026-04-11T10:00:00Z",
                            "route": "lpg",
                            "intent_id": "relationship_lookup",
                            "query_preview": "What is Neo4j connected to?",
                            "support_status": "supported",
                            "support_reason": "sufficient",
                            "support_coverage": 1.0,
                            "response_preview": "Neo4j uses Cypher.",
                        }
                    ]
                }
            ),
            _FakeResponse(
                payload={
                    "run_id": "run_123",
                    "workspace_id": "default",
                    "timestamp": "2026-04-11T10:00:00Z",
                    "route": "lpg",
                    "intent_id": "relationship_lookup",
                    "query_preview": "What is Neo4j connected to?",
                    "support_status": "supported",
                    "support_reason": "sufficient",
                    "support_coverage": 1.0,
                    "support_assessment": {
                        "intent_id": "relationship_lookup",
                        "supported": True,
                        "status": "supported",
                        "reason": "sufficient",
                        "coverage": 1.0,
                    },
                    "strategy_decision": {
                        "executed_mode": "semantic_direct",
                        "support_status": "supported",
                    },
                    "reasoning": {"requested": False},
                    "evidence_summary": {"grounded_slots": ["source_entity", "target_entity"]},
                    "lpg_record_count": 1,
                    "rdf_record_count": 0,
                    "response_preview": "Neo4j uses Cypher.",
                }
            ),
        ]
    )
    client = Seocho(base_url="http://localhost:8001", session=session)

    rows = client.semantic_runs(limit=10, route="lpg")
    record = client.semantic_run("run_123")

    assert rows[0].run_id == "run_123"
    assert rows[0].support.supported is True
    assert record.strategy.executed_mode == "semantic_direct"
    assert session.calls[0]["url"] == "http://localhost:8001/semantic/runs"
    assert session.calls[0]["params"]["route"] == "lpg"
    assert session.calls[1]["url"] == "http://localhost:8001/semantic/runs/run_123"
