---
title: Community
description: Join the SEOCHO community.
---

# Community

SEOCHO is an alpha open-source project for ontology-aligned graph memory and
agent runtime work. The public source of truth is GitHub; Discord is for
curated, real-time project conversation.

## Channels

| Channel | Use it for |
|---|---|
| [GitHub](https://github.com/tteon/seocho) | issues, PRs, releases, roadmap labels, and source history |
| [Docs](https://seocho.blog) | product docs, quickstarts, architecture, and release guidance |
| `#seocho` Discord | release announcements, contributor calls, and short curated updates |

Do not treat Discord as the canonical changelog. Durable decisions should link
back to GitHub issues, PRs, releases, docs, or ADRs.

## Contributing

1. Read [Why SEOCHO](/docs/why_seocho/) and [Quickstart](/docs/quickstart/).
2. Review the [Open Source Playbook](/docs/open_source_playbook/).
3. Open or claim a GitHub issue with clear `area-*` and `kind-*` labels.
4. Submit a PR against `main` with exact validation commands.

Good first contribution surfaces:

- docs clarity and broken-link fixes
- small examples that use current public APIs
- issue reproduction cases
- tests around SDK/runtime contracts

## Releases And Updates

SEOCHO releases should be evidence-backed. A release is ready only when the
release issue, changelog, validation, docs, and known gaps are clear.

Read [Release And Community Operations](/docs/release_and_community_operations/)
for release gates, Discord update classes, and the `#seocho` operating policy.

## Automation

The repository uses GitHub Actions for CI, docs checks, docs deploy, label
triage, scheduled Codex maintenance drafts, and maintainer-triggered merge
automation. Automation should stay reviewable and should not post every commit
or successful check to public community channels.
