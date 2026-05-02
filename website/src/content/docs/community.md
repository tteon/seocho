---
title: Community
description: Join the SEOCHO community.
---

# Community

## Get Involved

| Channel | Link |
|---------|------|
| **GitHub** | [tteon/seocho](https://github.com/tteon/seocho) — issues, PRs, discussions |
| **Documentation** | [seocho.blog](https://seocho.blog) — this site |

## Contributing

1. Fork the [seocho repository](https://github.com/tteon/seocho)
2. Read `CLAUDE.md` and `AGENTS.md` for project conventions
3. Pick an issue or create one with the `kind-*` label
4. Submit a PR against `main`

## Architecture Decisions

All significant changes go through an ADR (Architecture Decision Record) process. See `docs/decisions/DECISION_LOG.md` in the repo for the full history (ADR-0026 through ADR-0035).

## Automated Reviews

The repository runs Claude-powered CI reviews:
- **Daily**: maintenance tasks (test gaps, dead code, type hints)
- **Weekly**: SDK architecture review (API surface audit, prompt quality, refactor opportunities)

These generate draft PRs that maintainers review and merge.
