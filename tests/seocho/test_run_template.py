from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from seocho.run_spec import RunSpecError, parse_run_spec
from seocho.run_template import (
    RUN_J2_TEMPLATE,
    SWEEP_TEMPLATE,
    absolutized_rendered_text,
    collect_cli_vars,
    derive_variant_isolation,
    is_template_path,
    load_templated_run_spec,
    merge_vars,
    parse_rendered_run_spec,
    parse_sweep_spec,
    parse_var_assignment,
    render_run_template,
)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_substitutes_and_defaults() -> None:
    out = render_run_template(
        'name: "{{ name }}"\nlimit: {{ limit | default(3) }}',
        {"name": "x"},
        source="t.j2",
    )
    assert 'name: "x"' in out
    assert "limit: 3" in out


def test_render_collects_all_missing_string_vars() -> None:
    with pytest.raises(RunSpecError) as excinfo:
        render_run_template(
            'a: "{{ foo }}"\nb: "{{ bar }}"\nc: {{ ok | default(1) }}',
            {},
            source="t.j2",
        )
    message = str(excinfo.value)
    assert "'foo'" in message and "'bar'" in message
    assert "'ok'" not in message  # defaulted vars are fine
    assert "--var" in message and "default(" in message


def test_render_reports_structural_missing_var() -> None:
    with pytest.raises(RunSpecError) as excinfo:
        render_run_template(
            "qs:\n{% for q in questions %}\n  - \"{{ q }}\"\n{% endfor %}",
            {},
            source="t.j2",
        )
    assert "'questions'" in str(excinfo.value)


def test_render_syntax_error_includes_line() -> None:
    with pytest.raises(RunSpecError) as excinfo:
        render_run_template("a: 1\nb: {% if %}", {}, source="t.j2")
    assert "t.j2:2" in str(excinfo.value)
    assert "syntax error" in str(excinfo.value)


def test_rendered_yaml_error_points_at_rendered_text() -> None:
    rendered = render_run_template("a: {{ v }}", {"v": "[unclosed"}, source="t.j2")
    with pytest.raises(RunSpecError) as excinfo:
        parse_rendered_run_spec(rendered, source="t.j2")
    message = str(excinfo.value)
    assert "RENDERED" in message
    assert "--show-rendered" in message


def test_env_interpolation_happens_after_render(monkeypatch) -> None:
    monkeypatch.setenv("RUN_TEMPLATE_TEST_DB", "secretdb")
    rendered = render_run_template(
        textwrap.dedent(
            """
            ontology: "{{ schema }}"
            documents: ./docs/
            database: ${RUN_TEMPLATE_TEST_DB}
            """
        ),
        {"schema": "./s.yaml"},
        source="t.j2",
    )
    # the rendered artifact text keeps the placeholder (secret-safe)
    assert "${RUN_TEMPLATE_TEST_DB}" in rendered
    spec = parse_rendered_run_spec(rendered, source="t.j2")
    assert spec.database == "secretdb"


def test_is_template_path_by_extension_only() -> None:
    assert is_template_path("run.yaml.j2")
    assert is_template_path("x.j2")
    assert not is_template_path("run.yaml")
    assert not is_template_path("braces{{}}.yaml")


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------


def test_parse_var_assignment_types_and_dotted_keys() -> None:
    assert parse_var_assignment("query.limit=10") == (["query", "limit"], 10)
    assert parse_var_assignment("force=true") == (["force"], True)
    assert parse_var_assignment('name="2024"') == (["name"], "2024")
    with pytest.raises(RunSpecError):
        parse_var_assignment("no-equals")


def test_merge_vars_deep_merges_maps_replaces_lists() -> None:
    merged = merge_vars(
        {"a": {"x": 1, "keep": True}, "l": [1, 2]},
        {"a": {"y": 2}, "l": [9]},
    )
    assert merged == {"a": {"x": 1, "keep": True, "y": 2}, "l": [9]}


def test_collect_cli_vars_precedence(tmp_path) -> None:
    vars_file = tmp_path / "vars.yaml"
    vars_file.write_text("model: from_file\nlimit: 1\n", encoding="utf-8")
    merged = collect_cli_vars([str(vars_file)], ["model=from_flag"])
    assert merged["model"] == "from_flag"  # --var beats --vars file
    assert merged["limit"] == 1


# ---------------------------------------------------------------------------
# Sweep spec
# ---------------------------------------------------------------------------


def test_sweep_template_round_trip() -> None:
    sweep = parse_sweep_spec(yaml.safe_load(SWEEP_TEMPLATE), source_path="seocho.sweep.yaml")
    assert sweep.name == "enforcement-shootout"
    assert [variant.name for variant in sweep.variants] == ["guided", "strict", "open"]
    rendered = render_run_template(
        RUN_J2_TEMPLATE,
        sweep.variant_variables(sweep.variants[1], 1),
        source="run.yaml.j2",
    )
    spec = parse_rendered_run_spec(rendered, source="run.yaml.j2")
    assert spec.enforcement == "strict"


def test_sweep_spec_validation_collects_errors() -> None:
    with pytest.raises(RunSpecError) as excinfo:
        parse_sweep_spec(
            {
                "templte": "x.j2",
                "vars": {"variant": 1},
                "variants": [{"name": "a b"}, {"name": "A/B"}, {"nam": "x"}],
            }
        )
    message = str(excinfo.value)
    assert "Did you mean 'template'" in message
    assert "reserved variable" in message
    assert "collides" in message
    assert "requires a 'template' path" in message


def test_sweep_spec_requires_variants() -> None:
    with pytest.raises(RunSpecError, match="non-empty list of variants"):
        parse_sweep_spec({"template": "x.j2"})


def test_variant_variables_precedence_and_builtins() -> None:
    sweep = parse_sweep_spec(
        {
            "name": "s",
            "template": "t.j2",
            "vars": {"model": "shared", "limit": 1},
            "variants": [{"name": "v1", "vars": {"model": "variant"}}],
        }
    )
    merged = sweep.variant_variables(sweep.variants[0], 0, {"limit": 9})
    assert merged["model"] == "variant"  # variant beats shared
    assert merged["limit"] == 9  # CLI beats shared
    assert merged["variant"] == {"name": "v1", "index": 0}
    assert merged["sweep"] == {"name": "s"}


# ---------------------------------------------------------------------------
# Isolation derivation
# ---------------------------------------------------------------------------


def _spec(**overrides):
    payload = {"ontology": "s.yaml", "documents": "docs", "name": "base"}
    payload.update(overrides)
    return parse_run_spec(payload, source_path="run.yaml.j2")


def test_isolation_fills_blank_graph_with_variant_path(tmp_path) -> None:
    spec = derive_variant_isolation(_spec(), variant_name="strict", sweep_run_dir=tmp_path)
    assert spec.graph == str(tmp_path / "strict" / "graph.lbug")
    assert spec.workspace_id.endswith("_strict")
    assert spec.name == "base-strict"


def test_isolation_never_overrides_explicit_graph(tmp_path) -> None:
    spec = derive_variant_isolation(
        _spec(graph="bolt://localhost:7687", database="explicitdb"),
        variant_name="v1",
        sweep_run_dir=tmp_path,
    )
    assert spec.graph == "bolt://localhost:7687"
    assert spec.database == "explicitdb"


def test_isolation_fills_blank_database_for_bolt(tmp_path) -> None:
    spec = derive_variant_isolation(
        _spec(graph="bolt://localhost:7687"), variant_name="v1", sweep_run_dir=tmp_path
    )
    assert spec.database
    assert spec.database != "neo4j"


def test_isolation_workspace_suffix_is_unconditional(tmp_path) -> None:
    """Response-cache key invariant: two variants differing only by model
    must land in distinct workspaces, or one serves the other's cached
    answers."""
    spec_a = derive_variant_isolation(
        _spec(workspace_id="shared"), variant_name="model-a", sweep_run_dir=tmp_path
    )
    spec_b = derive_variant_isolation(
        _spec(workspace_id="shared"), variant_name="model-b", sweep_run_dir=tmp_path
    )
    assert spec_a.workspace_id != spec_b.workspace_id


# ---------------------------------------------------------------------------
# Templated run spec loading + artifact text
# ---------------------------------------------------------------------------


def test_load_templated_run_spec(tmp_path) -> None:
    template = tmp_path / "run.yaml.j2"
    template.write_text(
        textwrap.dedent(
            """
            ontology: ./schema.yaml
            documents: ./docs/
            models:
              default: "{{ model }}"
            questions:
              - Who is the CEO?
            """
        ).strip(),
        encoding="utf-8",
    )
    spec, rendered = load_templated_run_spec(template, {"model": "mara/MiniMax-M2"})
    assert spec.indexing_model() == "mara/MiniMax-M2"
    assert "mara/MiniMax-M2" in rendered


def test_absolutized_rendered_text(tmp_path) -> None:
    rendered = textwrap.dedent(
        """
        ontology:
          path: ./schema.yaml
        documents: ./docs/
        graph_password: ${SECRET:-pw}
        agent:
          design: ./design.yaml
        """
    ).strip()
    text = absolutized_rendered_text(
        rendered, template_path=tmp_path / "run.yaml.j2", provenance="prov line"
    )
    assert text.startswith("# prov line\n")
    payload = yaml.safe_load(text.split("\n", 1)[1])
    assert Path(payload["ontology"]["path"]).is_absolute()
    assert Path(payload["documents"]).is_absolute()
    assert Path(payload["agent"]["design"]).is_absolute()
    # secrets stay as unresolved placeholders
    assert payload["graph_password"] == "${SECRET:-pw}"
