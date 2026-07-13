"""Declarative connector run specs.

This module keeps connector orchestration small and file-based:

    seocho.connectors.yaml -> ConnectorRunPlan -> JSONL files + state file

It borrows the useful connector ideas from Airbyte/Singer/dlt without adopting
their heavier runtime assumptions: explicit source config, normalized records,
and a durable state artifact after each materialization run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

import yaml

from .records import ConnectorRecord, coerce_record, summarize_records, write_records_jsonl

DEFAULT_CONNECTORS_CONFIG_FILENAME = "seocho.connectors.yaml"
DEFAULT_CONNECTORS_OUTPUT_DIR = ".seocho/connectors"
DEFAULT_CONNECTORS_STATE_PATH = ".seocho/connectors/state.json"
CONNECTOR_CONFIG_VERSION = 1
SUPPORTED_CONFIG_PROVIDERS = {"notion", "slack", "datahub", "postgres", "neo4j"}

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


SAMPLE_CONNECTORS_YAML = """\
version: 1
output_dir: .seocho/connectors
state_path: .seocho/connectors/state.json

sources:
  - name: notion_wiki
    provider: notion
    data_source_ids: ["replace-with-notion-data-source-id"]
    token_env: NOTION_TOKEN
    output: notion.jsonl

  - name: slack_product
    provider: slack
    channels: ["replace-with-slack-channel-id"]
    token_env: SLACK_BOT_TOKEN
    limit: 15
    threads: true
    output: slack.jsonl

  - name: datahub_catalog
    provider: datahub
    server: "${DATAHUB_SERVER:-http://localhost:9002}"
    token_env: DATAHUB_TOKEN
    query: "*"
    limit: 100
    output: datahub.jsonl

  - name: postgres_schema
    provider: postgres
    dsn_env: DATABASE_URL
    schemas: [public]
    database_name: app
    output: postgres.jsonl

  - name: graph_schema
    provider: neo4j
    uri_env: NEO4J_URI
    user_env: NEO4J_USER
    password_env: NEO4J_PASSWORD
    database: neo4j
    output: neo4j.jsonl
"""


class ConnectorConfigError(ValueError):
    """Raised when a connector config cannot be parsed or validated."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


@dataclass(slots=True)
class ConnectorSourceSpec:
    """One source entry in ``seocho.connectors.yaml``."""

    name: str
    provider: str
    output: str
    category: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConnectorRunPlan:
    """Validated connector run plan."""

    sources: list[ConnectorSourceSpec]
    output_dir: str = DEFAULT_CONNECTORS_OUTPUT_DIR
    state_path: str = DEFAULT_CONNECTORS_STATE_PATH
    source_path: str = ""


@dataclass(slots=True)
class ConnectorRunResult:
    """Summary for one materialized source."""

    name: str
    provider: str
    output: str
    dry_run: bool
    records: int
    providers: dict[str, int]
    source_kinds: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "output": self.output,
            "dry_run": self.dry_run,
            "records": self.records,
            "providers": self.providers,
            "source_kinds": self.source_kinds,
        }


def write_sample_config(path: "str | Path" = DEFAULT_CONNECTORS_CONFIG_FILENAME, *, force: bool = False) -> Path:
    """Write a starter connector config and return its path."""

    output = Path(path)
    if output.exists() and not force:
        raise ConnectorConfigError([f"{output} already exists. Pass --force to overwrite."])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(SAMPLE_CONNECTORS_YAML, encoding="utf-8")
    return output


def _interpolate_env(value: Any, *, errors: list[str], where: str) -> Any:
    if isinstance(value, str):
        def _resolve(match: "re.Match[str]") -> str:
            name, default = match.group(1), match.group(2)
            resolved = os.environ.get(name)
            if resolved is not None:
                return resolved
            if default is not None:
                return default
            errors.append(
                f"at {where}: environment variable {name} is not set. "
                f"Export it or use ${{{name}:-fallback}}."
            )
            return ""
        return _ENV_PATTERN.sub(_resolve, value)
    if isinstance(value, Mapping):
        return {
            str(key): _interpolate_env(item, errors=errors, where=f"{where}.{key}" if where else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _interpolate_env(item, errors=errors, where=f"{where}[{index}]")
            for index, item in enumerate(value)
        ]
    return value


def _non_empty_strings(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if str(item or "").strip()]


def _source_output(source: Mapping[str, Any]) -> str:
    output = str(source.get("output") or "").strip()
    provider = str(source.get("provider") or "source").strip().lower() or "source"
    return output or f"{provider}.jsonl"


def _validate_source(source: Mapping[str, Any], *, index: int, errors: list[str]) -> Optional[ConnectorSourceSpec]:
    name = str(source.get("name") or f"source_{index + 1}").strip()
    provider = str(source.get("provider") or "").strip().lower()
    if not provider:
        errors.append(f"at sources[{index}].provider: provider is required.")
        return None
    if provider not in SUPPORTED_CONFIG_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_CONFIG_PROVIDERS))
        errors.append(f"at sources[{index}].provider: unsupported provider {provider!r}. Expected one of: {allowed}.")
        return None

    if provider == "notion" and not (_non_empty_strings(source.get("data_source_ids")) or _non_empty_strings(source.get("page_ids"))):
        errors.append(f"at sources[{index}]: notion requires data_source_ids or page_ids.")
    if provider == "slack" and not _non_empty_strings(source.get("channels")):
        errors.append(f"at sources[{index}]: slack requires channels.")
    if provider == "datahub" and not str(source.get("server") or "").strip():
        errors.append(f"at sources[{index}]: datahub requires server.")

    category = str(source.get("category") or provider).strip()
    config = {str(key): value for key, value in source.items() if key not in {"name", "provider", "output", "category"}}
    return ConnectorSourceSpec(
        name=name,
        provider=provider,
        output=_source_output(source),
        category=category,
        config=config,
    )


def load_connector_config(path: "str | Path" = DEFAULT_CONNECTORS_CONFIG_FILENAME) -> ConnectorRunPlan:
    """Load and validate ``seocho.connectors.yaml``."""

    config_path = Path(path)
    errors: list[str] = []
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConnectorConfigError([f"{config_path} not found. Run: seocho connect init"]) from exc
    except yaml.YAMLError as exc:
        raise ConnectorConfigError([f"{config_path} is not valid YAML: {exc}"]) from exc

    if not isinstance(payload, Mapping):
        raise ConnectorConfigError([f"{config_path} must contain a YAML mapping."])

    payload = _interpolate_env(dict(payload), errors=errors, where="")
    version = int(payload.get("version") or CONNECTOR_CONFIG_VERSION)
    if version != CONNECTOR_CONFIG_VERSION:
        errors.append(f"at version: unsupported connector config version {version}.")

    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        errors.append("at sources: must be a non-empty list.")
        raw_sources = []

    sources: list[ConnectorSourceSpec] = []
    seen_names: set[str] = set()
    for index, item in enumerate(raw_sources):
        if not isinstance(item, Mapping):
            errors.append(f"at sources[{index}]: must be a mapping.")
            continue
        source = _validate_source(item, index=index, errors=errors)
        if source is None:
            continue
        if source.name in seen_names:
            errors.append(f"at sources[{index}].name: duplicate source name {source.name!r}.")
        seen_names.add(source.name)
        sources.append(source)

    if errors:
        raise ConnectorConfigError(errors)

    return ConnectorRunPlan(
        sources=sources,
        output_dir=str(payload.get("output_dir") or DEFAULT_CONNECTORS_OUTPUT_DIR),
        state_path=str(payload.get("state_path") or DEFAULT_CONNECTORS_STATE_PATH),
        source_path=str(config_path),
    )


def resolve_source_output(plan: ConnectorRunPlan, source: ConnectorSourceSpec) -> Path:
    """Resolve a source output path relative to the plan output directory."""

    output = Path(source.output)
    if output.is_absolute():
        return output
    return Path(plan.output_dir) / output


def materialize_source(source: ConnectorSourceSpec) -> list[ConnectorRecord]:
    """Fetch records for one configured source."""

    cfg = source.config
    provider = source.provider
    if provider == "notion":
        from .notion import fetch_data_source_records, fetch_page_records

        records: list[ConnectorRecord] = []
        include_blocks = bool(cfg.get("include_blocks", not bool(cfg.get("no_blocks", False))))
        page_ids = _non_empty_strings(cfg.get("page_ids") or cfg.get("page_id"))
        data_source_ids = _non_empty_strings(cfg.get("data_source_ids") or cfg.get("data_source_id"))
        if page_ids:
            records.extend(
                fetch_page_records(
                    page_ids,
                    token_env=str(cfg.get("token_env") or "NOTION_TOKEN"),
                    notion_version=str(cfg.get("notion_version") or "2026-03-11"),
                    category=source.category,
                    include_blocks=include_blocks,
                )
            )
        for data_source_id in data_source_ids:
            records.extend(
                fetch_data_source_records(
                    data_source_id,
                    token_env=str(cfg.get("token_env") or "NOTION_TOKEN"),
                    notion_version=str(cfg.get("notion_version") or "2026-03-11"),
                    category=source.category,
                    max_pages=cfg.get("max_pages"),
                    include_blocks=include_blocks,
                )
            )
        return records

    if provider == "slack":
        from .slack import fetch_channel_records

        return fetch_channel_records(
            _non_empty_strings(cfg.get("channels") or cfg.get("channel")),
            token_env=str(cfg.get("token_env") or "SLACK_BOT_TOKEN"),
            team_id=str(cfg.get("team_id") or ""),
            channel_name=str(cfg.get("channel_name") or ""),
            category=source.category,
            limit=int(cfg.get("limit") or 15),
            max_pages=cfg.get("max_pages"),
            include_threads=bool(cfg.get("threads", False)),
        )

    if provider == "datahub":
        from .datahub import fetch_dataset_records

        return fetch_dataset_records(
            server=str(cfg.get("server") or ""),
            token_env=str(cfg.get("token_env") or "DATAHUB_TOKEN"),
            query_text=str(cfg.get("query") or "*"),
            limit=int(cfg.get("limit") or 100),
            category=source.category,
        )

    if provider == "postgres":
        from .postgres import fetch_schema_records

        return fetch_schema_records(
            dsn_env=str(cfg.get("dsn_env") or "DATABASE_URL"),
            schemas=_non_empty_strings(cfg.get("schemas") or cfg.get("schema")) or None,
            database=str(cfg.get("database_name") or cfg.get("database") or ""),
            category=source.category,
        )

    if provider == "neo4j":
        from .neo4j import fetch_schema_records

        return fetch_schema_records(
            uri_env=str(cfg.get("uri_env") or "NEO4J_URI"),
            user_env=str(cfg.get("user_env") or "NEO4J_USER"),
            password_env=str(cfg.get("password_env") or "NEO4J_PASSWORD"),
            database=str(cfg.get("database") or ""),
            category=source.category,
        )

    raise ConnectorConfigError([f"unsupported provider {provider!r}."])


def _state_for_records(records: Iterable["ConnectorRecord | Mapping[str, Any]"]) -> dict[str, Any]:
    ids: list[str] = []
    digest = hashlib.sha256()
    count = 0
    for record in records:
        item = coerce_record(record)
        ids.append(item.id)
        digest.update(item.id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.content.encode("utf-8"))
        digest.update(b"\0")
        count += 1
    return {
        "records": count,
        "record_ids": ids,
        "content_fingerprint": digest.hexdigest(),
    }


def write_connector_state(
    plan: ConnectorRunPlan,
    results: Iterable[ConnectorRunResult],
    records_by_source: Mapping[str, list["ConnectorRecord | Mapping[str, Any]"]],
) -> Path:
    """Write a content-free state summary after a materialization run."""

    state_path = Path(plan.state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    sources: dict[str, Any] = {}
    result_by_name = {result.name: result for result in results}
    for name, records in records_by_source.items():
        result = result_by_name[name]
        sources[name] = {
            "provider": result.provider,
            "output": result.output,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            **_state_for_records(records),
        }
    state = {
        "version": CONNECTOR_CONFIG_VERSION,
        "source_path": plan.source_path,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
    }
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    return state_path


def run_connector_plan(
    plan: ConnectorRunPlan,
    *,
    dry_run: bool = False,
    fetcher: Optional[Callable[[ConnectorSourceSpec], list[ConnectorRecord]]] = None,
) -> list[ConnectorRunResult]:
    """Materialize every source in a connector plan."""

    results: list[ConnectorRunResult] = []
    records_by_source: dict[str, list["ConnectorRecord | Mapping[str, Any]"]] = {}
    fetch = fetcher or materialize_source
    for source in plan.sources:
        records = fetch(source)
        output = resolve_source_output(plan, source)
        if not dry_run:
            write_records_jsonl(records, output)
        summary = summarize_records(records)
        records_by_source[source.name] = records
        results.append(
            ConnectorRunResult(
                name=source.name,
                provider=source.provider,
                output=str(output),
                dry_run=dry_run,
                records=int(summary["records"]),
                providers=dict(summary["providers"]),
                source_kinds=dict(summary["source_kinds"]),
            )
        )
    if not dry_run:
        write_connector_state(plan, results, records_by_source)
    return results


__all__ = [
    "CONNECTOR_CONFIG_VERSION",
    "DEFAULT_CONNECTORS_CONFIG_FILENAME",
    "DEFAULT_CONNECTORS_OUTPUT_DIR",
    "DEFAULT_CONNECTORS_STATE_PATH",
    "SAMPLE_CONNECTORS_YAML",
    "ConnectorConfigError",
    "ConnectorRunPlan",
    "ConnectorRunResult",
    "ConnectorSourceSpec",
    "load_connector_config",
    "materialize_source",
    "resolve_source_output",
    "run_connector_plan",
    "write_connector_state",
    "write_sample_config",
]
