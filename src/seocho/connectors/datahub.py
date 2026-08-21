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

    def iter_glossary_terms(
        self,
        *,
        query_text: str = "*",
        page_size: int = 50,
        max_results: int = 200,
    ) -> Iterator[dict[str, Any]]:
        search_query = """
        query SeochoGlossaryTermSearch($query: String!, $start: Int!, $count: Int!) {
          search(input: { type: GLOSSARY_TERM, query: $query, start: $start, count: $count }) {
            searchResults {
              entity {
                urn
                type
                ... on GlossaryTerm {
                  name
                  properties { name description }
                  tags { tags { tag { urn name } } }
                  isRelatedTerms: relationships(input: { types: ["IsA"], direction: OUTGOING, count: 10 }) {
                    relationships { entity { ... on GlossaryTerm { urn properties { name } } } }
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


# --------------------------------------------------------------------------
# Glossary pull (seocho-v6w.3): read reviewed glossary terms back so the human
# approval loop closes. This is the INBOUND half of the DataHub round-trip; the
# output shape is the neutral ``term_records`` contract consumed by
# ``datahub_export.datahub_glossary_to_mapping_spec`` — no urn:li or aspect
# names cross out of this module (ADR-0216 seam rule).
# --------------------------------------------------------------------------

# The approval signal is a tag on the term (ADR-0216): SEOCHO never writes the
# globalTags aspect, so a human's approval mark cannot be clobbered by re-export.
DEFAULT_APPROVED_TAG = "seocho:approved"


def _tag_names(entity: Mapping[str, Any]) -> list[str]:
    tags = entity.get("tags") if isinstance(entity.get("tags"), Mapping) else {}
    out: list[str] = []
    for t in _items(tags.get("tags")):
        if not isinstance(t, Mapping):
            continue
        tag = t.get("tag") if isinstance(t.get("tag"), Mapping) else {}
        name = tag.get("name") or (str(tag.get("urn", "")).rsplit(":", 1)[-1])
        if name:
            out.append(str(name))
    return out


def _parent_term_name(entity: Mapping[str, Any]) -> str:
    """First is-a parent's name, from the aliased ``isRelatedTerms`` relationships
    block (``relationships[].entity.properties.name``)."""
    rel = entity.get("isRelatedTerms") if isinstance(entity.get("isRelatedTerms"), Mapping) else {}
    for r in _items(rel.get("relationships")):
        if not isinstance(r, Mapping):
            continue
        term = r.get("entity") if isinstance(r.get("entity"), Mapping) else {}
        props = term.get("properties") if isinstance(term.get("properties"), Mapping) else {}
        name = props.get("name") or term.get("name")
        if name:
            return str(name)
    return ""


def glossary_term_to_record(
    entity: Mapping[str, Any],
    *,
    known_labels: frozenset[str] = frozenset(),
    approved_tag: str = DEFAULT_APPROVED_TAG,
) -> dict[str, Any]:
    """Normalize a DataHub glossary-term entity into a SEOCHO ``term_record``.

    - ``review_status`` = APPROVED iff the approval tag is present, else PROPOSED
      (the tag is the human signal; a definition edit alone is not approval).
    - ``action`` = ``annotate`` when the term names a class SEOCHO already has
      (a definition edit on an existing class), else ``new_class``.
    - ``description`` carries the human-authored definition back.
    The result is exactly what ``datahub_glossary_to_mapping_spec`` consumes."""
    props = entity.get("properties") if isinstance(entity.get("properties"), Mapping) else {}
    name = str(props.get("name") or entity.get("name") or "").strip()
    description = str(props.get("description") or props.get("definition") or "").strip()
    approved = approved_tag in _tag_names(entity)
    action = "annotate" if name in known_labels else "new_class"
    rec: dict[str, Any] = {
        "name": name,
        "review_status": "APPROVED" if approved else "PROPOSED",
        "action": action,
        "target": name,
        "description": description,
    }
    if action == "new_class":
        parent = _parent_term_name(entity)
        if parent:
            rec["parent"] = parent
    return rec


def fetch_glossary_term_records(
    *,
    server: str,
    token_env: str = "DATAHUB_TOKEN",
    query_text: str = "*",
    limit: int = 200,
    known_labels: frozenset[str] = frozenset(),
    approved_tag: str = DEFAULT_APPROVED_TAG,
    urn_prefix: str = "",
) -> list[dict[str, Any]]:
    """Pull reviewed glossary terms from a live GMS as ``term_records`` ready for
    ``datahub_glossary_to_mapping_spec`` → ``apply_mapping_spec``.

    ``urn_prefix`` scopes the pull to one ontology's terms (see
    ``datahub_export.package_term_urn_prefix``). Without it a GMS holding two
    SEOCHO ontologies would leak ontology B's approved terms into ontology A's
    apply — the search is server-wide and the record does not carry the URN."""
    client = DataHubGraphQLClient(server=server, token_env=token_env)
    return [
        glossary_term_to_record(entity, known_labels=known_labels, approved_tag=approved_tag)
        for entity in client.iter_glossary_terms(query_text=query_text, max_results=limit)
        if not urn_prefix or str(entity.get("urn") or "").startswith(urn_prefix)
    ]


__all__ = [
    "ConnectorAPIError",
    "DEFAULT_APPROVED_TAG",
    "DataHubGraphQLClient",
    "dataset_entity_to_record",
    "fetch_dataset_records",
    "fetch_glossary_term_records",
    "glossary_term_to_record",
]
