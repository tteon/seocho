---
title: "SEOCHO Runtime Update: Strict E2E Smoke Tests and the Open Source Playbook"
date: 2026-02-21
authors:
  - seocho
tags:
  - Release
  - Documentation
  - Testing
---

We are excited to announce a major update pushed to the SEOCHO main repository today, focusing heavily on **runtime stability, reproducible quickstarts, and contributor experience**.

If you are an external team or an open-source contributor looking to adopt the SEOCHO engine, this update is specifically designed for you.

## 1. Reproducible User Activation Path & New Quickstart

We have completely overhauled our [Quick Start guide](/docs/quickstart/) to follow a strict, reproducible User Activation Critical Path.

Previously, setting up the graph and running the agents could be highly environmental. Moving forward, no architecture changes will be merged unless this exact user path functions flawlessly:

1. **Ingest raw data** via the `/platform/ingest/raw` endpoint.
2. **Ensure semantic index** provisioning via the `/indexes/fulltext/ensure` API.
3. **Execute semantic/debate chat** successfully.
4. **Pass the new `make e2e-smoke`** automated test gate.

This flow is the definitive acceptance criteria for our user-facing releases.

## 2. Introducing the Open Source Playbook

To support external teams adapting SEOCHO to real, proprietary domain data, we have published the **[Open Source Playbook](/docs/open_source_playbook/)**.

This playbook serves as a direct extension guide, detailing exactly where to hook into the engine:
* **Route Extensibility:** How to modify `extraction/semantic_query_flow.py` for custom dispatch logic.
* **Ontology Injection:** How to replace the default FIBO constraints with your own domain ontology.
* **Testing Contracts:** Required quality gates (`make test-integration`, `make e2e-smoke`) that you must pass before expanding features.

## 3. Priority Execution Board Published

To ensure total transparency into the SEOCHO system's next steps, we have published our current [Priority Execution Board](/docs/architecture/#priority-execution-board-2026-02-21) within the Architecture documentation.

**P0 Priorities currently underway:**
* Isolating the OpenAI Agents SDK calls behind a unified adapter layer.
* Enforcing contract tests for runner signatures.
* Real-database Agent Provisioning (gracefully handling missing/stale databases in the debate fan-out phase).

## Update Now

To experience the new stable routing and explore the updated CLI trace capabilities:

```bash
git pull origin main
make e2e-smoke
```

Read the full details in our updated [Docs section](/docs/).
