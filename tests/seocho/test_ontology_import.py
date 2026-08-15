"""Import converters: draft-only, warnings carry the lossy parts."""

from __future__ import annotations

import json

from seocho.ontology import Ontology
from seocho.ontology_import import detect_format, import_document

ARROWS = json.dumps({
    "title": "AML sketch",
    "nodes": [
        {"id": "n0", "labels": ["Account"], "properties": {"acct_no": "9000001"}},
        {"id": "n1", "labels": ["Account"], "properties": {"acct_no": "9000002"}},
        {"id": "n2", "labels": ["Company"], "properties": {"name": "Hanbit", "share": "0.5"}},
        {"id": "n3", "labels": [], "properties": {}},
    ],
    "relationships": [
        {"id": "r0", "type": "TRANSFER", "fromId": "n0", "toId": "n1",
         "properties": {"amount": "48000"}},
        {"id": "r1", "type": "OWN", "fromId": "n2", "toId": "n0", "properties": {}},
    ],
})

CYPHER_DDL = """
CREATE CONSTRAINT acct_no_unique IF NOT EXISTS
FOR (a:Account) REQUIRE a.acct_no IS UNIQUE;
CREATE CONSTRAINT company_name IF NOT EXISTS
FOR (c:Company) REQUIRE c.name IS NOT NULL;
CREATE INDEX transfer_ts IF NOT EXISTS FOR (a:Account) ON (a.open_date);
CREATE CONSTRAINT rel_amount IF NOT EXISTS
FOR ()-[t:TRANSFER]-() REQUIRE t.amount IS NOT NULL;
"""

NATIVE_YAML = """
graph_type: finance
graph_model: lpg
nodes:
  Account:
    properties:
      acct_no: {type: INTEGER, constraint: UNIQUE}
relationships:
  TRANSFER: {source: Account, target: Account}
"""


def test_detects_all_p1_formats():
    assert detect_format(ARROWS) == "arrows"
    assert detect_format(CYPHER_DDL) == "cypher"
    assert detect_format(NATIVE_YAML) == "native"
    assert detect_format("type Query { accounts: [Account] }") == "graphql"


def test_arrows_infers_schema_and_flags_lossy_parts():
    result = import_document(ARROWS, format="auto")
    assert result.detected_format == "arrows"
    doc = result.document
    assert set(doc["nodes"]) == {"Account", "Company"}
    assert doc["nodes"]["Account"]["properties"]["acct_no"]["type"] == "INTEGER"
    assert doc["nodes"]["Company"]["properties"]["share"]["type"] == "FLOAT"
    assert doc["relationships"]["TRANSFER"]["source"] == "Account"
    assert doc["relationships"]["OWN"] == doc["relationships"]["OWN"]  # exists
    assert result.suggested_name == "aml_sketch"
    assert any("no label" in w for w in result.warnings)          # n3 skipped
    Ontology.from_dict(doc)  # the draft must round-trip as a real ontology


def test_cypher_ddl_carries_constraints_and_warns_on_gaps():
    result = import_document(CYPHER_DDL, format="cypher")
    doc = result.document
    account = doc["nodes"]["Account"]["properties"]
    assert account["acct_no"]["constraint"] == "UNIQUE"
    assert "open_date" in account                      # index-only property
    assert doc["nodes"]["Company"]["properties"]["name"]  # NOT NULL captured
    assert doc["relationships"]["TRANSFER"]["source"] == "Any"
    assert any("endpoints" in w for w in result.warnings)
    assert any("STRING" in w for w in result.warnings)  # type default disclosed
    Ontology.from_dict(doc)


def test_native_round_trips_and_suggests_name():
    result = import_document(NATIVE_YAML, format="auto")
    assert result.detected_format == "native"
    assert result.suggested_name == "finance"
    assert result.document["nodes"]["Account"]["properties"]["acct_no"]["type"] == "INTEGER"


def test_unknown_content_asks_for_explicit_format():
    result = import_document("hello world")
    assert result.document is None
    assert any("--format" in w for w in result.warnings)


GRAPHQL_SDL = """
type Account { id: ID!  balance: Float  owner: Company  transfers: [Transfer] }
type Company { name: String! }
type Transfer { amount: Float! }
"""

LINKML = """
name: aml-mini
classes:
  account:
    attributes:
      acct_no: {range: integer, identifier: true}
      owner: {range: company}
  company:
    attributes:
      name: {range: string, required: true}
  shell company:
    is_a: company
"""

DATA_IMPORTER = """
{"dataModel": {"graphSchema": {
  "nodeLabels": [
    {"$id": "n0", "token": "Account",
     "properties": [{"token": "acct_no", "type": {"type": "integer"}}]},
    {"$id": "n1", "token": "Company",
     "properties": [{"token": "name", "type": {"type": "string"}}]}
  ],
  "relationshipTypes": [
    {"token": "OWNS", "from": {"$ref": "#n1"}, "to": {"$ref": "#n0"}}
  ]}}}
"""


def test_graphql_object_types_become_labels_and_relationships():
    result = import_document(GRAPHQL_SDL, format="auto")
    assert result.detected_format == "graphql"
    doc = result.document
    assert doc["nodes"]["Account"]["properties"]["id"]["constraint"] == "UNIQUE"
    assert doc["nodes"]["Account"]["properties"]["balance"]["type"] == "FLOAT"
    assert doc["relationships"]["OWNER"]["target"] == "Company"
    assert doc["relationships"]["TRANSFERS"]["target"] == "Transfer"
    Ontology.from_dict(doc)


def test_linkml_classes_map_and_is_a_becomes_broader():
    result = import_document(LINKML, format="linkml")
    doc = result.document
    assert doc["nodes"]["Account"]["properties"]["acct_no"]["type"] == "INTEGER"
    assert doc["relationships"]["OWNER"]["target"] == "Company"
    assert doc["nodes"]["ShellCompany"]["broader"] == ["Company"]
    Ontology.from_dict(doc)


def test_data_importer_model_resolves_endpoint_refs():
    result = import_document(DATA_IMPORTER, format="auto")
    assert result.detected_format == "data-importer"
    doc = result.document
    assert doc["relationships"]["OWNS"]["source"] == "Company"
    assert doc["relationships"]["OWNS"]["target"] == "Account"
    Ontology.from_dict(doc)


def test_every_template_clones_into_a_valid_ontology():
    from seocho.ontology_templates import list_templates, load_template

    names = [t["name"] for t in list_templates()]
    assert {"quickstart", "finance-aml", "finance-compliance"} <= set(names)
    for name in names:
        Ontology.from_dict(load_template(name))


def test_import_never_persists(tmp_path, monkeypatch):
    """The result is data; nothing on disk changes."""
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    import_document(ARROWS)
    import_document(CYPHER_DDL, format="cypher")
    assert set(tmp_path.iterdir()) == before


def test_cli_import_prints_draft_and_review_hint(tmp_path, capsys):
    from seocho.cli import main

    src = tmp_path / "schema.cypher"
    src.write_text(CYPHER_DDL)
    out = tmp_path / "draft.yaml"
    code = main(["ontology", "import", str(src), "--output", str(out)])
    printed = capsys.readouterr().out
    assert code == 0
    assert "format: cypher" in printed
    assert "seocho ontology check" in printed
    assert out.exists()
    Ontology.from_dict(__import__("yaml").safe_load(out.read_text()))


def test_cli_import_unusable_draft_exits_nonzero(tmp_path, capsys):
    from seocho.cli import main

    src = tmp_path / "mystery.txt"
    src.write_text("completely unrecognizable content")
    code = main(["ontology", "import", str(src)])
    assert code == 1
    assert "warning:" in capsys.readouterr().out


def test_detects_typed_index_ddl_as_dumped_by_show_indexes():
    """SHOW INDEXES emits CREATE RANGE INDEX `name` FOR (n:`L`) ON (n.`p`) —
    the backticked, typed shape a live DozerDB actually produces."""
    ddl = 'CREATE RANGE INDEX `finbench_account_acct_no` FOR (n:`Account`) ON (n.`acct_no`);'
    assert detect_format(ddl) == "cypher"
    result = import_document(ddl, format="auto")
    assert result.document["nodes"]["Account"]["properties"]["acct_no"]["index"] is True
