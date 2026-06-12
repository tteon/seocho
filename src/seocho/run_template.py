"""Jinja2-templated run specs and sweep specs for ``seocho run`` / ``seocho sweep``.

Pure layer: jinja2 + yaml + :mod:`seocho.run_spec` only — no store, LLM, or
graph imports (same doctrine as ``run_spec.py``).

Two distinct substitution layers, resolved in a fixed order:

1. **Jinja2** (``{{ var }}``, ``{% for %}``) — authoring-time variables,
   rendered before YAML parsing. Whether rendering happens is decided by
   the file extension (``*.j2``) only; plain ``.yaml`` files never touch
   this module, so question text containing ``{{`` stays untouched.
2. **``${ENV}``** interpolation — load-time secrets, resolved inside
   :func:`seocho.run_spec.parse_run_spec` *after* rendering. Artifacts
   persist the pre-``${ENV}`` rendered text, so secrets never land on disk.

Authoring rule (documented in the templates): quote every string
substitution, never quote numeric/boolean ones — this avoids both the
YAML flow-mapping breakage and the Norway problem in one rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from .run_spec import (
    RunSpec,
    RunSpecError,
    _check_unknown_keys,
    _string,
    parse_run_spec,
)

#: Variable names injected per variant by ``seocho sweep`` — reserved.
RESERVED_VAR_NAMES = ("variant", "sweep")

DEFAULT_SWEEP_SPEC_FILENAME = "seocho.sweep.yaml"

_SWEEP_TOP_LEVEL_KEYS = {"name", "template", "vars", "variants", "output"}
_SWEEP_VARIANT_KEYS = {"name", "vars"}
_SLUG_RE = re.compile(r"[^a-z0-9_]+")
_DB_SAFE_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", str(text).strip().lower()).strip("_")


def render_run_template(
    text: str,
    variables: Mapping[str, Any],
    *,
    source: str = "<template>",
) -> str:
    """Render a Jinja2 run-spec template with collect-all missing-variable
    reporting.

    Variables with an inline ``| default(...)`` are fine; every *used*
    undefined variable is collected (string substitutions all in one pass;
    a structural use such as looping over a missing list stops the pass)
    and reported together via :class:`RunSpecError` (CLI exit 2).
    """
    from jinja2 import Environment, StrictUndefined, TemplateSyntaxError
    from jinja2.exceptions import UndefinedError

    missing: List[str] = []

    class _CollectingUndefined(StrictUndefined):
        # NOTE: StrictUndefined binds its protocol methods (__iter__,
        # __len__, ...) to Undefined._fail_with_undefined_error at class
        # creation, so overriding that method here only affects new
        # call sites; structural uses still raise UndefinedError, which
        # the render loop below catches and records.
        def _remember(self) -> str:
            name = self._undefined_name or "<expression>"
            if name not in missing:
                missing.append(name)
            return name

        def __str__(self) -> str:
            self._remember()
            return ""

    env = Environment(
        undefined=_CollectingUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )
    # Deliberately no os.environ global — secrets stay in the ${ENV} layer.
    try:
        template = env.from_string(text)
    except TemplateSyntaxError as exc:
        raise RunSpecError(
            [f"at template {source}:{exc.lineno}: template syntax error: {exc.message}."]
        ) from exc

    rendered = ""
    try:
        rendered = template.render(**dict(variables))
    except UndefinedError as exc:
        # Structural use of a missing variable (loop/attribute) — record
        # it; string substitutions were already collected by __str__.
        match = re.search(r"'([^']+)' is undefined", str(exc))
        name = match.group(1) if match else str(exc)
        if name not in missing:
            missing.append(name)
    except TemplateSyntaxError as exc:
        raise RunSpecError(
            [f"at template {source}:{exc.lineno}: template syntax error: {exc.message}."]
        ) from exc

    if missing:
        names = ", ".join(f"'{name}'" for name in missing)
        raise RunSpecError(
            [
                f"at template {source}: undefined variable(s) {names}. "
                "Pass --var <name>=..., add them to a --vars file, or give "
                'a default in the template: {{ <name> | default("...") }}.'
            ]
        )
    return rendered


def parse_var_assignment(text: str) -> Tuple[List[str], Any]:
    """Parse one ``--var key=value`` assignment.

    Keys may be dotted (``models.indexing=...`` → nested mapping). Values
    are YAML-parsed so ``limit=10`` is an int and ``force=true`` a bool;
    force a string with explicit quotes: ``--var 'name="2024"'``.
    """
    if "=" not in text:
        raise RunSpecError(
            [f"at --var: expected key=value, got {text!r}."]
        )
    raw_key, raw_value = text.split("=", 1)
    key_path = [part.strip() for part in raw_key.strip().split(".")]
    if not all(key_path):
        raise RunSpecError([f"at --var: invalid key {raw_key!r}."])
    try:
        value = yaml.safe_load(raw_value)
    except yaml.YAMLError:
        value = raw_value
    return key_path, value


def _nest(key_path: List[str], value: Any) -> Dict[str, Any]:
    nested: Dict[str, Any] = {}
    cursor = nested
    for part in key_path[:-1]:
        cursor[part] = {}
        cursor = cursor[part]
    cursor[key_path[-1]] = value
    return nested


def merge_vars(*layers: Mapping[str, Any]) -> Dict[str, Any]:
    """Deep-merge variable layers (later wins). Mappings merge recursively;
    scalars and lists are replaced — never concatenated."""
    merged: Dict[str, Any] = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
                merged[key] = merge_vars(merged[key], value)
            elif isinstance(value, Mapping):
                merged[key] = merge_vars({}, value)
            else:
                merged[key] = value
    return merged


def load_vars_file(path: "str | Path") -> Dict[str, Any]:
    """Load one ``--vars`` YAML file (must be a mapping)."""
    vars_path = Path(path)
    if not vars_path.exists():
        raise RunSpecError([f"at --vars: file not found: {vars_path}."])
    with vars_path.open("r", encoding="utf-8") as handle:
        try:
            payload = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise RunSpecError([f"at --vars {vars_path}: invalid YAML: {exc}."]) from exc
    if not isinstance(payload, Mapping):
        raise RunSpecError([f"at --vars {vars_path}: must be a YAML mapping."])
    return dict(payload)


def collect_cli_vars(
    vars_files: Optional[List[str]] = None,
    var_flags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Assemble CLI-supplied variables: ``--vars`` files in order, then
    ``--var`` flags (highest precedence)."""
    layers: List[Mapping[str, Any]] = [load_vars_file(path) for path in (vars_files or [])]
    for assignment in var_flags or []:
        key_path, value = parse_var_assignment(assignment)
        layers.append(_nest(key_path, value))
    return merge_vars(*layers) if layers else {}


def _check_reserved(variables: Mapping[str, Any], *, where: str, errors: List[str]) -> None:
    for reserved in RESERVED_VAR_NAMES:
        if reserved in (variables or {}):
            errors.append(
                f"at {where}: '{reserved}' is a reserved variable name "
                "(injected per variant by seocho sweep)."
            )


def is_template_path(path: "str | Path") -> bool:
    """Rendering is decided by extension only (``*.j2``)."""
    return str(path).endswith(".j2")


def _rendered_yaml_payload(rendered: str, *, source: str) -> Any:
    try:
        return yaml.safe_load(rendered) or {}
    except yaml.YAMLError as exc:
        excerpt_lines: List[str] = []
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            lines = rendered.splitlines()
            start = max(0, mark.line - 1)
            for index in range(start, min(len(lines), mark.line + 2)):
                excerpt_lines.append(f"    {index + 1}: {lines[index]}")
        excerpt = ("\n" + "\n".join(excerpt_lines)) if excerpt_lines else ""
        raise RunSpecError(
            [
                f"rendered YAML from {source} is invalid: {exc}. "
                "Note: line numbers refer to the RENDERED text, not the .j2 "
                f"source — inspect it with --show-rendered.{excerpt}"
            ]
        ) from exc


def parse_rendered_run_spec(rendered: str, *, source: str) -> RunSpec:
    """Parse rendered template text into a validated :class:`RunSpec`."""
    payload = _rendered_yaml_payload(rendered, source=source)
    return parse_run_spec(payload, source_path=source)


def load_templated_run_spec(
    path: "str | Path",
    variables: Mapping[str, Any],
) -> Tuple[RunSpec, str]:
    """Load a ``.j2`` run-spec template: render → YAML → ``parse_run_spec``.

    Returns ``(spec, rendered_text)`` — the rendered text is pre-``${ENV}``
    and safe to persist as an artifact.
    """
    template_path = Path(path)
    if not template_path.exists():
        raise RunSpecError([f"template not found: {template_path}."])
    text = template_path.read_text(encoding="utf-8")
    rendered = render_run_template(text, variables, source=str(template_path))
    return parse_rendered_run_spec(rendered, source=str(template_path)), rendered


# ---------------------------------------------------------------------------
# Sweep spec
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SweepVariant:
    name: str
    vars: Dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return _slug(self.name)


@dataclass(slots=True)
class SweepSpec:
    """One template × N named variable sets → N runs → one summary."""

    name: str
    template: str
    variants: List[SweepVariant]
    vars: Dict[str, Any] = field(default_factory=dict)
    output_dir: str = "runs"
    source_path: str = ""

    def template_path(self) -> Path:
        path = Path(self.template)
        if path.is_absolute():
            return path
        base = Path(self.source_path).parent if self.source_path else Path(".")
        return base / path

    def variant_variables(
        self,
        variant: SweepVariant,
        index: int,
        cli_vars: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Effective variables for one variant. Precedence (low→high):
        sweep ``vars`` < variant ``vars`` < CLI vars < injected built-ins."""
        return merge_vars(
            self.vars,
            variant.vars,
            dict(cli_vars or {}),
            {
                "variant": {"name": variant.name, "index": index},
                "sweep": {"name": self.name},
            },
        )


def parse_sweep_spec(payload: Any, *, source_path: str = "") -> SweepSpec:
    errors: List[str] = []
    if not isinstance(payload, Mapping):
        raise RunSpecError(["sweep spec must be a YAML mapping."])

    _check_unknown_keys(payload, allowed=_SWEEP_TOP_LEVEL_KEYS, where="top level", errors=errors)

    output = payload.get("output")
    output_dir = "runs"
    if output is not None:
        if isinstance(output, Mapping):
            _check_unknown_keys(output, allowed={"dir"}, where="output", errors=errors)
            output_dir = _string(output.get("dir")) or "runs"
        else:
            errors.append("at output: must be a mapping with 'dir'.")

    shared_vars = payload.get("vars") or {}
    if not isinstance(shared_vars, Mapping):
        errors.append("at vars: must be a mapping.")
        shared_vars = {}
    _check_reserved(shared_vars, where="vars", errors=errors)

    variants: List[SweepVariant] = []
    raw_variants = payload.get("variants")
    if not isinstance(raw_variants, list) or not raw_variants:
        errors.append("at variants: a sweep requires a non-empty list of variants.")
        raw_variants = []
    seen_slugs: Dict[str, str] = {}
    for index, item in enumerate(raw_variants):
        where = f"variants[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"at {where}: must be a mapping with 'name' (and optional 'vars').")
            continue
        _check_unknown_keys(item, allowed=_SWEEP_VARIANT_KEYS, where=where, errors=errors)
        name = _string(item.get("name"))
        if not name:
            errors.append(f"at {where}: requires a non-empty 'name'.")
            continue
        slug = _slug(name)
        if not slug:
            errors.append(f"at {where}: name {name!r} has no filesystem-safe characters.")
            continue
        if slug in seen_slugs:
            errors.append(
                f"at {where}: name {name!r} collides with variant "
                f"{seen_slugs[slug]!r} (both slug to '{slug}')."
            )
            continue
        seen_slugs[slug] = name
        variant_vars = item.get("vars") or {}
        if not isinstance(variant_vars, Mapping):
            errors.append(f"at {where}.vars: must be a mapping.")
            variant_vars = {}
        _check_reserved(variant_vars, where=f"{where}.vars", errors=errors)
        variants.append(SweepVariant(name=name, vars=dict(variant_vars)))

    default_name = Path(source_path).stem.replace(".sweep", "") if source_path else "sweep"
    spec = SweepSpec(
        name=_string(payload.get("name")) or default_name,
        template=_string(payload.get("template")),
        variants=variants,
        vars=dict(shared_vars),
        output_dir=output_dir,
        source_path=source_path,
    )
    if not spec.template:
        errors.append("at template: a sweep requires a 'template' path (.yaml.j2).")

    if errors:
        raise RunSpecError(errors)
    return spec


def load_sweep_spec(path: "str | Path") -> SweepSpec:
    sweep_path = Path(path)
    if not sweep_path.exists():
        raise RunSpecError(
            [f"sweep spec not found: {sweep_path}. Create one with: seocho sweep --init"]
        )
    with sweep_path.open("r", encoding="utf-8") as handle:
        try:
            payload = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise RunSpecError([f"invalid YAML in {sweep_path}: {exc}"]) from exc
    return parse_sweep_spec(payload, source_path=str(sweep_path))


# ---------------------------------------------------------------------------
# Variant isolation
# ---------------------------------------------------------------------------

_BOLT_SCHEMES = ("bolt://", "neo4j://", "neo4j+s://", "bolt+s://")


def _database_safe(text: str) -> str:
    """Pure local equivalent of the store's database-name sanitizer
    (``^[a-z][a-z0-9]{2,62}$``) — this module must not import the store."""
    cleaned = _DB_SAFE_RE.sub("", str(text).lower())
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"db{cleaned}"
    return cleaned[:63]


def derive_variant_isolation(
    spec: RunSpec,
    *,
    variant_name: str,
    sweep_run_dir: Path,
) -> RunSpec:
    """Apply per-variant isolation defaults (mutates and returns ``spec``).

    Fills blanks only — an explicit ``graph:``/``database:`` from the
    rendered template is never overridden. The ``workspace_id`` suffix is
    UNCONDITIONAL: the response cache key is (workspace, database,
    ontology hash, question), so two variants differing only by model
    would otherwise serve each other's cached answers.
    """
    slug = _slug(variant_name)
    variant_dir = sweep_run_dir / slug

    spec.workspace_id = f"{spec.resolved_workspace_id()}_{slug}"
    spec.name = f"{spec.name}-{slug}"

    if not spec.graph:
        # Embedded ladybug: one .lbug file = one graph; the `database`
        # parameter does not partition storage — only the path does.
        spec.graph = str(variant_dir / "graph.lbug")
    elif spec.graph.startswith(_BOLT_SCHEMES) and not spec.database:
        spec.database = _database_safe(f"{spec.name}")

    return spec


def absolutized_rendered_text(
    rendered: str,
    *,
    template_path: Path,
    provenance: str,
) -> str:
    """Rewrite relative input paths in rendered YAML to absolute ones so the
    persisted ``rendered.yaml`` reproduces the variant standalone via
    ``seocho run rendered.yaml``. Pre-``${ENV}`` strings pass through
    untouched (secrets never resolve into artifacts)."""
    payload = yaml.safe_load(rendered) or {}
    if not isinstance(payload, dict):
        return rendered
    base = template_path.parent.resolve()

    def _absolutize(value: Any) -> Any:
        if isinstance(value, str) and value.strip() and not Path(value).is_absolute():
            return str((base / value).resolve())
        return value

    for key in ("ontology", "documents"):
        section = payload.get(key)
        if isinstance(section, str):
            payload[key] = _absolutize(section)
        elif isinstance(section, dict) and "path" in section:
            section["path"] = _absolutize(section["path"])
    for key in ("indexing", "agent"):
        section = payload.get(key)
        if isinstance(section, dict) and isinstance(section.get("design"), str):
            section["design"] = _absolutize(section["design"])

    header = f"# {provenance}\n"
    return header + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# --init templates
# ---------------------------------------------------------------------------

RUN_J2_TEMPLATE = """\
# run.yaml.j2 — Jinja2 run-spec template for `seocho run` / `seocho sweep`.
#
{% raw %}
# Two substitution layers (resolved in this order):
#   {{ var }}      Jinja2, authoring-time — supplied via --var/--vars or sweep variants
#   ${ENV_VAR}     load-time secrets — resolved when the spec is loaded, never persisted
#
# Quoting rule: QUOTE every string substitution ("{{ model }}"), never quote
# numbers/booleans ({{ limit | default(5) }}).
{% endraw %}
# seocho sweep suffixes the variant name onto name/workspace automatically —
# no need to interpolate {{ '{{' }} variant.name {{ '}}' }} yourself.
name: demo

ontology:
  path: ./schema.yaml
  enforcement: "{{ enforcement | default('guided') }}"   # strict | guided | open

documents: ./docs/

models:
  default: "{{ model | default('mara/MiniMax-M2.5') }}"

query:
  limit: {{ limit | default(5) }}

questions:
{% for q in questions %}
  - "{{ q }}"
{% endfor %}
"""

SWEEP_TEMPLATE = """\
# seocho.sweep.yaml — one template x N variants -> one comparison table.
#
#   seocho sweep                # runs every variant sequentially
#   seocho sweep --dry-run      # render + validate + preflight, no LLM calls
#   seocho sweep --show-rendered strict   # inspect one variant's YAML
#
# Each variant gets an isolated graph, workspace, and output directory.
name: enforcement-shootout
template: ./run.yaml.j2

vars:                         # shared by every variant (variant vars override)
  model: mara/MiniMax-M2.5
  questions:
    - Who is the CEO of Acme Corp?
    - What did Beta Industries acquire?

variants:
  - name: guided
    vars: { enforcement: guided }
  - name: strict
    vars: { enforcement: strict }
  - name: open
    vars: { enforcement: open }

output:
  dir: runs                   # summary + per-variant artifacts land in
                              # runs/<name>-<timestamp>/
"""


__all__ = [
    "DEFAULT_SWEEP_SPEC_FILENAME",
    "RESERVED_VAR_NAMES",
    "RUN_J2_TEMPLATE",
    "SWEEP_TEMPLATE",
    "SweepSpec",
    "SweepVariant",
    "absolutized_rendered_text",
    "collect_cli_vars",
    "derive_variant_isolation",
    "is_template_path",
    "load_sweep_spec",
    "load_templated_run_spec",
    "load_vars_file",
    "merge_vars",
    "parse_rendered_run_spec",
    "parse_sweep_spec",
    "parse_var_assignment",
    "render_run_template",
]
