import os
import sys
import json


ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from seocho.cli import main
from seocho.governance import ArtifactDiff, ArtifactValidationMessage, ArtifactValidationResult
from seocho.local import LocalRuntimeStatus
from seocho.semantic import SemanticArtifact, SemanticArtifactSummary
from seocho.types import ArchiveResult, ChatResponse, GraphTarget, Memory, MemoryCreateResult, SearchResult


class _FakeSeocho:
    last_add_kwargs = None
    last_artifact_payload = None
    last_apply_kwargs = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def close(self) -> None:
        return None

    def add_with_details(self, content, **kwargs):
        _FakeSeocho.last_add_kwargs = kwargs
        return MemoryCreateResult(
            memory=Memory(
                memory_id="mem_cli",
                workspace_id=self.kwargs.get("workspace_id") or "default",
                content=content,
                metadata={"source": "cli"},
                status="stored",
            ),
            ingest_summary={"records_processed": 1},
            trace_id="tr_cli",
        )

    def search(self, query, **kwargs):
        return [
            SearchResult(
                memory_id="mem_cli",
                content="Alice manages Seoul retail.",
                content_preview="Alice manages Seoul retail.",
                metadata={"source": "cli"},
                score=0.91,
                reasons=["entity_match"],
                matched_entities=["Seoul"],
                database="kgnormal",
                status="active",
            )
        ]

    def chat(self, message, **kwargs):
        return ChatResponse(
            assistant_message="Alice manages Seoul retail.",
            memory_hits=[{"memory_id": "mem_cli", "score": 0.91, "database": "kgnormal"}],
            search_results=[],
            semantic_context={},
            trace_id="tr_chat",
        )

    def get(self, memory_id, **kwargs):
        return Memory(
            memory_id=memory_id,
            workspace_id=self.kwargs.get("workspace_id") or "default",
            content="Alice manages Seoul retail.",
            metadata={"source": "cli"},
            status="active",
        )

    def delete(self, memory_id, **kwargs):
        return ArchiveResult(
            memory_id=memory_id,
            workspace_id=self.kwargs.get("workspace_id") or "default",
            database="kgnormal",
            status="archived",
            archived_at="2026-03-13T00:00:00Z",
            archived_nodes=3,
            trace_id="tr_delete",
        )

    def graphs(self):
        return [
            GraphTarget(
                graph_id="kgnormal",
                database="kgnormal",
                uri="bolt://neo4j:7687",
                ontology_id="baseline",
                vocabulary_profile="vocabulary.v2",
                description="Baseline graph",
                workspace_scope="default",
            )
        ]

    def health(self, scope="runtime"):
        return {"status": "ready", "scope": scope}

    def list_artifacts(self, status=None):
        return [
            SemanticArtifactSummary(
                artifact_id="sa_1",
                workspace_id=self.kwargs.get("workspace_id") or "default",
                name="finance_v1",
                created_at="2026-03-13T00:00:00Z",
                status=status or "approved",
                approved_at="2026-03-13T01:00:00Z",
                approved_by="reviewer",
            )
        ]

    def get_artifact(self, artifact_id):
        return SemanticArtifact(
            workspace_id=self.kwargs.get("workspace_id") or "default",
            artifact_id=artifact_id,
            name="finance_v1",
            status="approved",
            created_at="2026-03-13T00:00:00Z",
            approved_at="2026-03-13T01:00:00Z",
            approved_by="reviewer",
            ontology_candidate={"ontology_name": "finance", "classes": [], "relationships": []},
            shacl_candidate={"shapes": []},
            vocabulary_candidate={"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
        )

    def create_artifact_draft(self, payload):
        _FakeSeocho.last_artifact_payload = payload
        return SemanticArtifact(
            workspace_id=self.kwargs.get("workspace_id") or "default",
            artifact_id="sa_2",
            name=payload.get("name", "draft"),
            status="draft",
            created_at="2026-03-13T02:00:00Z",
            ontology_candidate={"ontology_name": "finance", "classes": [], "relationships": []},
            shacl_candidate={"shapes": []},
            vocabulary_candidate={"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
        )

    def approve_artifact(self, artifact_id, **kwargs):
        return SemanticArtifact(
            workspace_id=self.kwargs.get("workspace_id") or "default",
            artifact_id=artifact_id,
            name="finance_v2",
            status="approved",
            created_at="2026-03-13T02:00:00Z",
            approved_at="2026-03-13T03:00:00Z",
            approved_by=kwargs["approved_by"],
            ontology_candidate={"ontology_name": "finance", "classes": [], "relationships": []},
            shacl_candidate={"shapes": []},
            vocabulary_candidate={"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
        )

    def deprecate_artifact(self, artifact_id, **kwargs):
        return SemanticArtifact(
            workspace_id=self.kwargs.get("workspace_id") or "default",
            artifact_id=artifact_id,
            name="finance_v2",
            status="deprecated",
            created_at="2026-03-13T02:00:00Z",
            deprecated_at="2026-03-13T04:00:00Z",
            deprecated_by=kwargs["deprecated_by"],
            ontology_candidate={"ontology_name": "finance", "classes": [], "relationships": []},
            shacl_candidate={"shapes": []},
            vocabulary_candidate={"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
        )

    def validate_artifact(self, artifact):
        if isinstance(artifact, dict) and artifact.get("name") == "broken":
            return ArtifactValidationResult(
                ok=False,
                errors=[
                    ArtifactValidationMessage(
                        level="error",
                        code="ontology.class_name_missing",
                        message="Ontology class name is required.",
                        path="ontology_candidate.classes[0].name",
                    )
                ],
                warnings=[],
                summary={"error_count": 1, "warning_count": 0},
            )
        return ArtifactValidationResult(
            ok=True,
            errors=[],
            warnings=[],
            summary={"error_count": 0, "warning_count": 0},
        )

    def diff_artifacts(self, left, right):
        return ArtifactDiff(
            left_name=(left.get("name") if isinstance(left, dict) else getattr(left, "name", "left")) or "left",
            right_name=(right.get("name") if isinstance(right, dict) else getattr(right, "name", "right")) or "right",
            changes={
                "metadata": {"changed": ["name"]},
                "ontology_classes": {"added": ["Subsidiary"], "removed": [], "changed": []},
                "ontology_relationships": {"added": ["OWNS (Company -> Subsidiary)"], "removed": [], "changed": []},
                "shacl_shapes": {"added": [], "removed": [], "changed": []},
                "vocabulary_terms": {"added": ["Subsidiary"], "removed": [], "changed": []},
            },
            summary={"classes_added": 1, "relationships_added": 1, "terms_added": 1},
        )

    def apply_artifact(self, artifact_id, content, **kwargs):
        _FakeSeocho.last_apply_kwargs = {"artifact_id": artifact_id, "content": content, **kwargs}
        return MemoryCreateResult(
            memory=Memory(
                memory_id="mem_apply",
                workspace_id=self.kwargs.get("workspace_id") or "default",
                content=content,
                metadata={"source": "cli"},
                status="stored",
            ),
            ingest_summary={"records_processed": 1},
            trace_id="tr_apply",
        )


def test_cli_add_json_output(monkeypatch, capsys):
    monkeypatch.setattr("seocho.cli.Seocho", _FakeSeocho)

    exit_code = main(
        [
            "add",
            "hello seocho",
            "--json",
            "--workspace-id",
            "demo",
            "--approved-artifact-id",
            "sa_1",
            "--prompt-context",
            '{"instructions":["Prefer approved ontology labels."]}',
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert '"memory_id": "mem_cli"' in captured.out
    assert _FakeSeocho.last_add_kwargs["approved_artifact_id"] == "sa_1"
    assert _FakeSeocho.last_add_kwargs["prompt_context"]["instructions"] == [
        "Prefer approved ontology labels."
    ]


def test_cli_search_human_output(monkeypatch, capsys):
    monkeypatch.setattr("seocho.cli.Seocho", _FakeSeocho)

    exit_code = main(["search", "Seoul retail"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "1. [0.91] Alice manages Seoul retail." in captured.out


def test_cli_chat_and_doctor(monkeypatch, capsys):
    monkeypatch.setattr("seocho.cli.Seocho", _FakeSeocho)

    chat_exit = main(["chat", "Who manages Seoul retail?"])
    ask_exit = main(["ask", "Who manages Seoul retail?"])
    doctor_exit = main(["doctor"])

    assert chat_exit == 0
    assert ask_exit == 0
    assert doctor_exit == 0
    captured = capsys.readouterr()
    assert "Alice manages Seoul retail." in captured.out
    assert "runtime: ready" in captured.out


def test_cli_artifacts_commands(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("seocho.cli.Seocho", _FakeSeocho)
    artifact_file = tmp_path / "artifact.json"
    artifact_file.write_text(
        json.dumps(
            {
                "name": "finance_v2",
                "ontology_candidate": {"ontology_name": "finance", "classes": [], "relationships": []},
                "shacl_candidate": {"shapes": []},
            }
        ),
        encoding="utf-8",
    )

    list_exit = main(["artifacts", "list", "--status", "approved"])
    get_exit = main(["artifacts", "get", "sa_1", "--json"])
    create_exit = main(["artifacts", "create-draft", "--artifact-file", str(artifact_file)])
    approve_exit = main(["artifacts", "approve", "sa_2", "--approved-by", "reviewer"])
    deprecate_exit = main(["artifacts", "deprecate", "sa_2", "--deprecated-by", "reviewer"])

    assert list_exit == 0
    assert get_exit == 0
    assert create_exit == 0
    assert approve_exit == 0
    assert deprecate_exit == 0
    assert _FakeSeocho.last_artifact_payload["name"] == "finance_v2"
    captured = capsys.readouterr()
    assert "sa_1 [approved] finance_v1" in captured.out
    assert '"artifact_id": "sa_1"' in captured.out
    assert "sa_2 [draft] finance_v2" in captured.out


def test_cli_artifact_validate_diff_apply_and_local_runtime(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("seocho.cli.Seocho", _FakeSeocho)
    monkeypatch.setattr(
        "seocho.cli.serve_local_runtime",
        lambda **kwargs: LocalRuntimeStatus(
            action="serve",
            status="ready",
            project_dir="/tmp/seocho",
            command=["docker", "compose", "up", "-d"],
            api_url="http://localhost:8001",
            ui_url="http://localhost:8501",
            graph_url="http://localhost:7474",
            used_fallback_openai_key=True,
            runtime_status="ready",
            graph_count=1,
        ),
    )
    monkeypatch.setattr(
        "seocho.cli.stop_local_runtime",
        lambda **kwargs: LocalRuntimeStatus(
            action="stop",
            status="stopped",
            project_dir="/tmp/seocho",
            command=["docker", "compose", "down"],
        ),
    )

    left_file = tmp_path / "left.json"
    right_file = tmp_path / "right.json"
    broken_file = tmp_path / "broken.json"
    left_file.write_text(
        json.dumps(
            {
                "name": "finance_v1",
                "ontology_candidate": {"ontology_name": "finance", "classes": [], "relationships": []},
                "shacl_candidate": {"shapes": []},
            }
        ),
        encoding="utf-8",
    )
    right_file.write_text(
        json.dumps(
            {
                "name": "finance_v2",
                "ontology_candidate": {"ontology_name": "finance", "classes": [{"name": "Subsidiary"}], "relationships": []},
                "shacl_candidate": {"shapes": []},
            }
        ),
        encoding="utf-8",
    )
    broken_file.write_text(
        json.dumps(
            {
                "name": "broken",
                "ontology_candidate": {"ontology_name": "", "classes": [{"name": ""}], "relationships": []},
                "shacl_candidate": {"shapes": []},
            }
        ),
        encoding="utf-8",
    )

    serve_exit = main(["serve"])
    validate_exit = main(["artifacts", "validate", "--artifact-file", str(broken_file)])
    diff_exit = main(
        [
            "artifacts",
            "diff",
            "--left-artifact-file",
            str(left_file),
            "--right-artifact-file",
            str(right_file),
        ]
    )
    apply_exit = main(
        [
            "artifacts",
            "apply",
            "sa_approved_finance_v1",
            "ACME acquired Beta in 2024.",
            "--prompt-context",
            '{"instructions":["Prefer finance ontology labels."]}',
        ]
    )
    stop_exit = main(["stop"])

    assert serve_exit == 0
    assert validate_exit == 1
    assert diff_exit == 0
    assert apply_exit == 0
    assert stop_exit == 0
    assert _FakeSeocho.last_apply_kwargs["artifact_id"] == "sa_approved_finance_v1"
    assert _FakeSeocho.last_apply_kwargs["prompt_context"]["instructions"] == [
        "Prefer finance ontology labels."
    ]
    captured = capsys.readouterr()
    assert "runtime ready at http://localhost:8001 using fallback OPENAI_API_KEY" in captured.out
    assert "artifact invalid: 1 errors, 0 warnings" in captured.out
    assert "ontology_classes added: Subsidiary" in captured.out
    assert "stored mem_apply in workspace=default" in captured.out
    assert "runtime stopped in /tmp/seocho" in captured.out
