"""DataHub-to-SEOCHO connector materialization helpers."""

from __future__ import annotations

import os
from typing import Any, Iterator, Mapping, Optional

import requests

from .records import ConnectorRecord, stable_record_id


class ConnectorAPIError(RuntimeError):
    """Raised when DataHub GraphQL returns an error."""


def _items(values: Any) -> list[Any]:
    return values if isinstance(values, list) else []


def _dataset_fields(entity: Mapping[str, Any]) -> list[dict[str, Any]]:
    schema = entity.get("schemaMetadata") if isinstance(entity.get("schemaMetadata"), Mapping) else {}
    fields: list[dict[str, Any]] = []
    for field in _items(schema.get("fields")):
        if not isinstance(field, Mapping):
            continue
        fields.append(
            {
                "fieldPath": field.get("fieldPath"),
                "nativeDataType": field.get("nativeDataType"),
                "description": field.get("description"),
            }
        )
    return fields


def _term_names(entity: Mapping[str, Any]) -> list[str]:
    terms = entity.get("glossaryTerms") if isinstance(entity.get("glossaryTerms"), Mapping) else {}
    names: list[str] = []
    for item in _items(terms.get("terms")):
        term = item.get("term") if isinstance(item, Mapping) and isinstance(item.get("term"), Mapping) else {}
        name = term.get("name") or term.get("urn")
        if name:
            names.append(str(name))
    return names


def _tag_names(entity: Mapping[str, Any]) -> list[str]:
    tags = entity.get("tags") if isinstance(entity.get("tags"), Mapping) else {}
    names: list[str] = []
    for item in _items(tags.get("tags")):
        tag = item.get("tag") if isinstance(item, Mapping) and isinstance(item.get("tag"), Mapping) else {}
        name = tag.get("name") or tag.get("urn")
        if name:
            names.append(str(name))
    return names


def _owner_urns(entity: Mapping[str, Any]) -> list[str]:
    ownership = entity.get("ownership") if isinstance(entity.get("ownership"), Mapping) else {}
    owners: list[str] = []
    for item in _items(ownership.get("owners")):
        owner = item.get("owner") if isinstance(item, Mapping) and isinstance(item.get("owner"), Mapping) else {}
        urn = owner.get("urn")
        if urn:
            owners.append(str(urn))
    return owners


def dataset_entity_to_record(
    entity: Mapping[str, Any],
    *,
    category: str = "datahub",
) -> ConnectorRecord:
    urn = str(entity.get("urn") or "")
    name = str(entity.get("name") or urn.rsplit(",", 2)[0] or "DataHub dataset")
    props = entity.get("properties") if isinstance(entity.get("properties"), Mapping) else {}
    description = str(props.get("description") or "")
    fields = _dataset_fields(entity)
    terms = _term_names(entity)
    tags = _tag_names(entity)
    owners = _owner_urns(entity)

    lines = [f"# {name}"]
    if description:
        lines += ["", description]
    if fields:
        lines += ["", "## Fields"]
        for field in fields:
            dtype = field.get("nativeDataType") or "unknown"
            desc = field.get("description") or ""
            lines.append(f"- {field.get('fieldPath')} ({dtype}) {desc}".rstrip())
    if terms:
        lines += ["", "## Glossary Terms", ", ".join(terms)]
    if tags:
        lines += ["", "## Tags", ", ".join(tags)]
    content = "\n".join(lines)

    return ConnectorRecord(
        id=stable_record_id("datahub", urn, content),
        content=content,
        provider="datahub",
        source_kind="datahub_dataset",
        category=category,
        title=name,
        metadata={
            "external_id": urn,
            "datahub_urn": urn,
            "datahub_type": entity.get("type"),
            "field_count": len(fields),
            "fields": fields,
            "glossary_terms": terms,
            "tags": tags,
            "owners": owners,
        },
    )


class DataHubGraphQLClient:
    """Small read-only DataHub GraphQL client."""

    def __init__(
        self,
        *,
        server: str,
        token: Optional[str] = None,
        token_env: str = "DATAHUB_TOKEN",
        session: Optional[requests.Session] = None,
        timeout: float = 30.0,
    ) -> None:
        resolved = token or os.environ.get(token_env, "")
        self.server = server.rstrip("/")
        self.endpoint = self.server if self.server.endswith("/api/graphql") else f"{self.server}/api/graphql"
        self.session = session or requests.Session()
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        if resolved:
            self._headers["Authorization"] = f"Bearer {resolved}"

    def query(self, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        response = self.session.post(
            self.endpoint,
            headers=self._headers,
            json={"query": query, "variables": variables or {}},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise ConnectorAPIError(f"DataHub GraphQL HTTP {response.status_code}: {response.text}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ConnectorAPIError("DataHub GraphQL returned a non-object response.")
        if payload.get("errors"):
            raise ConnectorAPIError(f"DataHub GraphQL errors: {payload['errors']}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ConnectorAPIError("DataHub GraphQL response missing data.")
        return data

    def iter_dataset_search(
        self,
        *,
        query_text: str = "*",
        page_size: int = 25,
        max_results: int = 100,
    ) -> Iterator[dict[str, Any]]:
        search_query = """
        query SeochoDatasetSearch($query: String!, $start: Int!, $count: Int!) {
          search(input: { type: DATASET, query: $query, start: $start, count: $count }) {
            searchResults {
              entity {
                urn
                type
                ... on Dataset {
                  name
                  properties { name description }
                  schemaMetadata {
                    fields { fieldPath nativeDataType description }
                  }
                  ownership {
                    owners { owner { urn } }
                  }
                  glossaryTerms {
                    terms { term { urn name } }
                  }
                  tags {
                    tags { tag { urn name } }
                  }
                }
              }
            }
          }
        }
        """
        start = 0
        while start < max_results:
            count = min(max(page_size, 1), max_results - start)
            data = self.query(search_query, {"query": query_text, "start": start, "count": count})
            search = data.get("search") if isinstance(data.get("search"), Mapping) else {}
            results = _items(search.get("searchResults"))
            if not results:
                break
            for result in results:
                entity = result.get("entity") if isinstance(result, Mapping) else None
                if isinstance(entity, dict):
                    yield entity
            start += len(results)
            if len(results) < count:
                break


def fetch_dataset_records(
    *,
    server: str,
    token_env: str = "DATAHUB_TOKEN",
    query_text: str = "*",
    limit: int = 100,
    category: str = "datahub",
) -> list[ConnectorRecord]:
    client = DataHubGraphQLClient(server=server, token_env=token_env)
    return [
        dataset_entity_to_record(entity, category=category)
        for entity in client.iter_dataset_search(query_text=query_text, max_results=limit)
    ]


__all__ = [
    "ConnectorAPIError",
    "DataHubGraphQLClient",
    "dataset_entity_to_record",
    "fetch_dataset_records",
]
