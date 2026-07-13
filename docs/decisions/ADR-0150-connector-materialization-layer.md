# ADR-0150: Connector Materialization Layer

Status: Accepted

Date: 2026-07-13

## Context

SEOCHO needs to meet users where their data already lives: Notion, Slack,
DataHub, PostgreSQL, Neo4j/DozerDB, LangChain loaders, and similar ecosystem
tools. The risky path would be to let every connector write directly into the
graph or to make `seocho.run.yaml` own API credentials, pagination, and sync
state.

SEOCHO already has a reliable indexing path for files and JSONL records. It
also already owns ontology extraction, graph shaping, validation, evidence
bundles, and query reports. Connectors should feed that path instead of
replacing it.

## Decision

Add a built-in connector materialization layer:

- connector adapters read external systems in a read-only mode
- each item is normalized into `seocho.connector_record.v1`
- records are written as JSONL
- existing `seocho run` / `documents.path` consumes the JSONL
- repeatable multi-source imports use `seocho.connectors.yaml`
- successful config runs write a content-free state artifact for source names,
  output files, record IDs, and content fingerprints

The first public CLI is:

```bash
seocho connect <notion|slack|datahub|postgres|neo4j> --output .seocho/connectors/source.jsonl
seocho connect init
seocho connect run seocho.connectors.yaml
```

`seocho connectors` is an alias. LangChain and LlamaIndex integrations are
Python-level converters that duck-type their document objects and do not import
those frameworks.

This is not a new public plugin API. It is a small set of built-in
materializers plus a stable normalized record format. The narrow public plugin
surface in `seocho.__init__` remains unchanged.

## Consequences

- First-run connector UX is simple: materialize JSONL, then run the normal
  SEOCHO flow.
- External API secrets stay outside run specs and generated records.
- Connector tests can be mostly offline and deterministic.
- Live compatibility, performance, and rate-limit claims still require real
  runs against each named service.
- The state artifact establishes the place for future provider cursors without
  pretending the first adapters are incremental.
- Stateful sync configs, checkpoints, OAuth rotation, and direct graph-payload
  connectors can land later without disturbing the core indexing pipeline.

## Follow-Ups

- Add provider-specific incremental cursors once live gates prove each source's
  pagination and timestamp semantics.
- Add optional live gates for Notion, Slack, DataHub, PostgreSQL, and
  Neo4j/DozerDB behind env vars.
- Add Neo4j offline import artifact export only after the normalized record
  contract stabilizes.
