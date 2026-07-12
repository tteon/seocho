# Release And Community Operations

This page is the operator guide for SEOCHO releases and GraphUserGroup community
publishing. It answers one question first:

> Where should this update live: GitHub, Ghost, or Discord?

Use this page for community operations. Do not use Discord as a grant-reporting
board, monthly planning archive, or evidence appendix.

## Start Here

| If you have... | Publish first in... | Then route to... | Keep out of... |
|---|---|---|---|
| a code change, release, issue, PR, or API decision | GitHub | `#seocho-updates` when users should know | Ghost-only announcements |
| a product review or field note | Ghost | `#product-reviews` | raw Discord threads |
| a weekly trend or paper/product digest | Ghost or Knowledge OS | `#weekly-trends` | `#seocho-updates` |
| a GraphTalk, interview, or video recap | Ghost, YouTube, or event page | `#graphtalks` or `#events-workshops` | full article mirrors in Discord |
| a job, collaboration, internship, or research opportunity | approved short note | `#job-board` | vague recruiter spam |
| a user question that reveals a SEOCHO gap | Discord | GitHub issue or docs update | untracked decisions |
| a security, abuse, or account incident | private maintainer channel | public note only after safe review | public Discord details |

## Surface Ownership

Each surface has a different job.

| Surface | Owns | Best content | Avoid |
|---|---|---|---|
| SEOCHO GitHub | source of truth | code, issues, PRs, releases, docs, ADRs, reproducible commands | casual discussion and untracked decisions |
| `seocho.blog` | SEOCHO docs and onboarding | quickstart, concepts, API docs, tutorials, release docs | community news that belongs on Ghost |
| GraphUserGroup / Ghost | public editorial archive | Graph Omakase, GraphTravel, product reviews, GraphTalk recaps, interviews, jobs | raw coordination and private planning |
| Discord | real-time community | Q&A, study support, product-review discussion, job-board posts, event follow-up | evidence packs and monthly reports |

Default flow:

1. GitHub creates the durable project artifact.
2. Ghost turns selected artifacts into public context.
3. Discord shares the short link and hosts discussion.
4. Useful Discord discussion becomes a GitHub issue, docs update, or Ghost post.

Keep durable titles and summaries English-first so the same item can move to
LinkedIn, X, Reddit, Substack, GitHub, and conference follow-up without a full
rewrite.

## Release Flow

SEOCHO is alpha software. Releases should be boring, evidence-backed, and easy
to audit.

### When To Release

| Release type | Use when | Examples |
|---|---|---|
| Patch | users hit a small bug, docs error, packaging issue, or compatibility problem | broken import, dependency metadata, small query bug |
| Minor | a new SDK, runtime, docs, or example capability is added without breaking users | connector surface, run-spec feature, public tutorial |
| Major | public API, runtime contract, response shape, or persisted data contract changes | renamed SDK facade, graph schema migration |
| Pre-release | a risky capability needs feedback before a stable release | connector preview, distributed runtime preview, `0.6.0rc1` |

Do not cut a release only because commits landed. Release when users have a
reason to update.

### Required Gates

Every release issue should state the version, release type, linked PRs, and
validation evidence.

Required for every release:

- `bash scripts/ci/run_basic_ci.sh`
- GitHub `Basic CI` green on the release commit
- `CHANGELOG.md` updated under the target version
- public docs updated for user-visible behavior
- release notes include known gaps and skipped validation
- no release-blocking issue or PR is open
- no secret, local path, generated artifact, or private data is committed

Additional gates:

| If the release touches... | Also check... |
|---|---|
| SDK facade, models, session, or transport | SDK-focused tests and public API wording |
| runtime API, policy, or memory service | runtime/extraction compatibility tests and `workspace_id` propagation |
| query, Graph-RAG, or answer synthesis | query tests, Cypher guard coverage, and `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md` |
| indexing, graph shaping, or ontology enforcement | indexing tests, examples assumptions, and ontology docs |
| GitHub automation, scripts, or repo layout | `bash scripts/ci/check-doc-contracts.sh` and root hierarchy checks |
| performance or scalability claims | live evidence with service versions, dataset, concurrency, hardware, warmup, and skipped components |
| security-sensitive behavior | private review path; do not use public Discord |

### Release Blockers

Do not publish a stable release while any blocker is true:

- basic CI is failing on the release commit
- release notes imply production readiness without live evidence
- changelog or quickstart is stale for user-visible behavior
- public API or response shape changed without tests and docs
- runtime-facing models lose `workspace_id` or policy checks
- local agent/editor state, private data, or secrets are included
- a known security issue is being discussed in public instead of the security
  path

## Community Publishing Flow

Use community surfaces to help people learn, review products, find
opportunities, and discuss graph technology. Keep the public record clear.

| Content class | Canonical artifact | Discord channel | What the Discord post should contain |
|---|---|---|---|
| SEOCHO release | GitHub Release and changelog | `#seocho-updates` | title, three bullets, link, known gap if relevant |
| SEOCHO how-to | `seocho.blog` docs or tutorial | `#seocho-project` | problem, audience, link, question for feedback |
| product review | Ghost post | `#product-reviews` | product, use case, tested version, cost/limits, link |
| weekly trend | Ghost or Knowledge OS draft | `#weekly-trends` | headline, why it matters, source link |
| GraphTalk/video | Ghost, YouTube, or event page | `#graphtalks` | topic, speaker, audience, video/materials link |
| job/opportunity | approved opportunity note | `#job-board` | role, organization, location/remote, deadline, link |
| contributor call | GitHub issue batch | `#contributor-hub` | 3-5 tasks with labels and links |

Posting rules:

- English title and English summary for durable posts.
- Korean is fine for live support, local coordination, and quick Q&A.
- Do not mirror full Ghost posts into Discord.
- Do not post raw commit spam.
- Do not mention users or roles automatically.
- Disclose vendor support, sponsorship, tested version, setup context, and cost
  when posting a product review.

## Discord Operating Model

The Discord server should feel like a place for exchange, not a reporting room.

### Current Channel Map

Configured on 2026-07-12:

| Area | Channel | Use |
|---|---|---|
| Welcome | `#introductions` | member introductions and onboarding links |
| Graph Hub | `#content-feed` | canonical content feed from Ghost, YouTube, event pages, and public resources |
| Graph Hub | `#announcements` | low-noise public announcements |
| Graph Hub | `#product-reviews` | discussion linked from Ghost product review posts and field notes |
| Graph Hub | `#graphtalks` | event follow-up, videos, speaker materials, and attendee questions |
| Networking | `#job-board` | graph, data, AI, infra, research, internship, and collaboration opportunities |
| Insights | `#weekly-trends` | GraphChef, Graph Omakase, paper, and product trend summaries |
| Project | `#seocho-project` | SEOCHO questions, usage reports, MCP work, graph memory design, and contributor discussion |
| Project | `#seocho-updates` | GitHub, CI, release, and SEOCHO project updates |
| Project | `#contributor-hub` | first-time contributor questions, study support, docs improvements, and onboarding |
| Project | `seocho-office-hours` | live study sessions, project support, and post-event follow-up |

### Channel Rules

| Channel | Good posts | Avoid |
|---|---|---|
| `#seocho-updates` | release notes, calls for testing, important docs changes | weekly trends, raw CI noise, casual chat |
| `#seocho-project` | design questions, usage reports, MCP work, graph memory discussion | private planning or decisions with no GitHub link |
| `#product-reviews` | consumer-side product experience and review links | unsourced claims or vendor marketing copy |
| `#weekly-trends` | GraphChef Daily, Graph Omakase Weekly, papers, product signals | every raw RSS item |
| `#graphtalks` | event pages, slides, recordings, recap questions | unrelated job or release posts |
| `#job-board` | approved opportunities with role, org, location, deadline, link | vague hiring posts, scraping, resume farming |
| `#contributor-hub` | beginner/intermediate/advanced tracks, first issues, docs help | release announcements |

### Moderation Baseline

Reduce spam before the community grows:

- disable `@everyone` and `@here` for default member roles
- use Discord AutoMod for mass mentions, suspicious links, and spam patterns
- keep `#mod-log` private for moderation events
- use `#incident-room` for account abuse, security, or high-severity incidents
- keep webhook payloads on `allowed_mentions: []`
- require opportunity posts to include role, organization, location or remote
  policy, expected skills, and application link or contact

## Ghost Operations

GraphUserGroup is the broader graph community surface. SEOCHO should appear as
one project track inside the graph ecosystem, not as the only center of the
community.

### Current Ghost State

As of 2026-07-12, GraphUserGroup has enough published content to support a
broader English-first operating model.

| Surface | Current state | Next action |
|---|---|---|
| Home | live at `https://www.graphusergroup.com/` | make the homepage show research, product experience, open source, jobs, and events; do not make Graph Omakase look like the whole community |
| Graph Omakase | live tag page with corrected SEO metadata | keep as weekly trend archive and route summaries to `#weekly-trends` |
| GraphTravel | live tag page with corrected SEO metadata | use for experience and review notes from trying graph products and workflows |
| GraphTalks | live page with English metadata | move future talks into smaller event recap posts |
| GraphInterview | live page with English metadata | keep as the interview archive |
| Contributor | live page with English metadata | use as contributor entry point for studies, content, reviews, and SEOCHO work |
| GraphInformation | live page with English metadata | decide whether to archive, rename, or fold into event resources |
| SEOCHO | live tag and first explainer post | route project updates from GitHub and `seocho.blog` into Ghost and Discord |
| Product Review | live tag and first review-method post | use for review templates, field notes, and product comparison methodology |
| Job | live tag and first opportunity-board post | use for approved graph, data, AI, infra, and research opportunities |

Completed cleanup:

- removed default Ghost social links for `x.com/ghost` and `facebook.com/ghost`
- fixed `GraphOmakase` and `GraphTravel` SEO metadata
- added English meta descriptions for `GraphTalks`, `GraphInterview`,
  `Contributor`, and `GraphInformation`
- removed the Gmail-hosted image reference from the public `GraphTalks` page
- published first public posts for `SEOCHO`, `Product Review`, and `Job`
- added `Reviews` and `Jobs` to public navigation after the tag pages were live

Next Ghost work:

- revise the homepage so it presents the full community, not only Graph Omakase
- turn large event pages into recap posts linked from stable archive pages
- add product review templates, job-board examples, and SEOCHO tutorials
- keep publishing as a maintainer review step until automation is proven

### Admin API

Use the Ghost Admin API only from a secure server-side relay.

| Direction | Mechanism | Use |
|---|---|---|
| GitHub or Knowledge OS to Ghost | Ghost Admin API | create draft posts for product reviews, tutorials, weekly trends, event recaps, and SEOCHO explainers |
| Ghost to Discord | Ghost webhook to relay | route published posts by tag to the right Discord channel |
| Discord to Ghost/GitHub | maintainer curation | turn useful discussion into an article, issue, or docs update |

Security rules:

- store `GHOST_ADMIN_API_URL`, `GHOST_ADMIN_API_KEY`, Discord webhook URLs, and
  relay secrets only in server-side secrets or GitHub Actions secrets
- never commit Admin API keys, JWTs, staff tokens, cookies, or webhook URLs
- use a dedicated Ghost Custom Integration for this workflow
- validate Ghost tags before posting to Discord
- log post id, tag, target Discord channel, and delivery status, but not secrets
  or full payloads

### Ghost CLI

Use Ghost CLI for self-hosted or staging operations. Do not use it as the
content automation path.

| Area | Ghost CLI use | Notes |
|---|---|---|
| install and staging | `ghost install`, `ghost install local`, `ghost setup` | local or self-hosted staging |
| runtime control | `ghost start`, `ghost stop`, `ghost restart`, `ghost ls` | controlled self-hosted operations |
| health and debugging | `ghost doctor`, `ghost log`, `ghost run` | failed starts, upgrades, webhook issues |
| upgrades and rollback | `ghost update`, `ghost update --rollback` | run with backup and maintenance window |
| backup | `ghost backup` | before updates, theme changes, migration, or major config change |

If GraphUserGroup uses managed Ghost hosting without shell access, treat Ghost
CLI as a local/staging tool and rely on Ghost Admin, Admin API, and webhooks for
production operations.

## Docs And Website Contract

The SEOCHO documentation source lives in this repository. `seocho.blog` mirrors
published docs from this source.

Keep this contract:

- edit source docs in `tteon/seocho`
- sync the website mirror after the source commit is ready
- do not edit generated mirrored docs directly
- every mirrored page should keep source path and source commit metadata
- run docs contract checks before pushing docs changes

For docs-only changes, start with:

```bash
bash scripts/ci/check-doc-contracts.sh
```

## Knowledge OS And `agy`

`agy` should use Discord as a real-time community channel, not as a database.
Knowledge OS should draft and archive higher-context summaries, then post only
selected summaries.

Expected responsibilities:

- monitor `Discord Updates` workflow health
- draft SEOCHO update posts from GitHub releases, docs, and Ghost posts
- draft GraphUserGroup weekly trend announcements for `#weekly-trends`
- draft product review announcements from Ghost review posts
- draft job-board posts from approved opportunities
- avoid grant-reporting, monthly-plan, or evidence-pack language in public
  Discord posts
- draft release notes before maintainers publish a GitHub release
- track unresolved readiness gaps as issues or private tasks

Default mode: draft first, then post after cadence and tone are reviewed.

## Cadence

| Rhythm | Activity | Owner |
|---|---|---|
| daily | GraphChef trend sensing, if useful enough to post | Knowledge OS / maintainer |
| weekly | Graph Omakase summary and `#weekly-trends` discussion prompt | content lead |
| as needed | patch releases and security-safe updates | maintainer |
| monthly | open-source backlog review and contributor task selection | maintainer group |
| quarterly | GraphTalks, product review project, or workshop cycle | event/content leads |

## Appendix A. Detailed Tag Routing

| Ghost tag | Discord channel | Message shape |
|---|---|---|
| `seocho` | `#seocho-updates` | project update, GitHub/docs link, Ghost link |
| `GraphOmakase` or `graphomakase` | `#weekly-trends` | weekly trend headline, why it matters, Ghost link |
| `weekly-trend` | `#weekly-trends` | short curated digest, Ghost link |
| `product-review` | `#product-reviews` | product, use case, tested version, cost/limits, Ghost link |
| `job` or `opportunity` | `#job-board` | role/opportunity, location/remote, deadline, link |
| `event` | `#events-workshops` or `#graphtalks` | date/time, registration or video link |
| `GraphTalks`, `graphtalks`, or `interview` | `#graphtalks` | guest, topic, video/materials link |

## Appendix B. Forum Tags And Labels

Suggested `#seocho-project` forum tags:

- `release`
- `mcp`
- `how-to`
- `integration`
- `graph-db`
- `agentic-rag`
- `beginner`
- `intermediate`
- `advanced`
- `help-wanted`

Suggested `#product-reviews` review labels:

- `neo4j`
- `neptune`
- `memgraph`
- `tigergraph`
- `arangodb`
- `graphscope`
- `rdf`
- `graph-ai`
- `cost`
- `operations`
- `beginner-friendly`

## Appendix C. Product Review Guardrails

- Do not compare every graph product at once. Pick 2-4 products per project.
- Treat comparisons as consumer-side experience reports: setup, loading, query,
  cost, operations, limits, and fit.
- Separate hands-on experience, vendor-provided information, and community
  opinion.
- For performance claims, include hardware, dataset, command, versions, and
  skipped components.
- Link to the Ghost review, official docs, GitHub issue/PR, video, or slide deck
  when a longer reference exists.

## Appendix D. Release Checklist Issue

Use GitHub's `Release checklist` issue template for every stable or pre-release
candidate. Keep the issue open until:

- release tag exists
- GitHub Release is published
- Discord announcement has been sent
- follow-up gaps are converted into issues or docs tasks

## Appendix E. Open-Source Readiness Gaps

Track these before broader community growth:

- backfill labels for old issues and PRs
- define CODEOWNERS after ownership is clear
- keep release automation manual until the process is proven
- revisit PyPI/TestPyPI publish workflow before the next package release
- audit `SECURITY.md` against actual workflows
- start stale issue handling as a report, not an auto-closing bot
- evolve Discord moderation and maintainer escalation as traffic grows
