# Release And Community Operations

This document defines when SEOCHO should cut a release, what evidence is needed
before publishing it, and how SEOCHO Discord channels should support real-time
open-source operations without becoming the canonical changelog.

## Operating Principle

SEOCHO is still alpha software. Releases should be boring, evidence-backed, and
easy to audit. Discord can make the project feel alive, but GitHub and docs
remain the source of truth.

Canonical surfaces:

- GitHub Releases
- `CHANGELOG.md`
- `README.md`, `QUICKSTART.md`, and relevant `docs/*`
- issue and PR history

Broadcast surfaces:

- Discord `#seocho-updates`
- Discord `#seocho-project`
- Discord `#weekly-trend`
- GraphUserGroup / Ghost newsletter posts
- docs/blog posts when a release deserves a narrative explanation

## Release Types

SEOCHO follows SemVer intent, with alpha discipline:

| Type | When to use | Examples |
|---|---|---|
| Patch | Bug fix, docs correction, packaging fix, compatibility repair | dependency metadata, broken import, small query bug |
| Minor | Additive public SDK/runtime/docs capability without breaking existing users | connector surface, run-spec feature, public example |
| Major | Breaking public API, runtime contract, persisted data contract, or migration burden | renamed SDK facade, incompatible response shape, graph schema migration |
| Pre-release | Risky capability needing user feedback before stable release | `0.6.0rc1`, connector preview, distributed runtime preview |

No release should be cut only because a commit landed. Release when there is a
user-visible reason to update.

## Release Readiness Gates

Every release issue should state the intended version, release type, linked PRs,
and exact validation evidence.

Required for every release:

- `bash scripts/ci/run_basic_ci.sh`
- GitHub `Basic CI` green on the release commit
- `CHANGELOG.md` updated under the target version
- public docs updated for user-visible behavior
- release notes draft includes known gaps and skipped validation
- no public issue or PR marked release-blocking
- no new secret, local path, generated artifact, or private data committed

Required when touched:

| Touched surface | Additional gate |
|---|---|
| SDK facade, models, session, transport | SDK-focused tests and public docs wording |
| Runtime API, policy, memory service | runtime/extraction compatibility tests and `workspace_id` propagation check |
| Query, Graph-RAG, answer synthesis | query tests, Cypher guard coverage, and `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md` review |
| Indexing, graph shaping, ontology enforcement | indexing tests, examples assumptions, ontology docs review |
| GitHub automation, scripts, repo layout | `bash scripts/ci/check-doc-contracts.sh` and `scripts/ci/check-root-hierarchy-contract.sh` |
| Performance or scalability claims | live-evidence report with service versions, dataset, concurrency, hardware/container limits, warmup, and skipped components |
| Security-sensitive behavior | private security review path; do not use public Discord for sensitive details |

## Release Blockers

Do not publish a stable release while any of these are true:

- basic CI is failing on the release commit
- release notes imply production readiness without live evidence
- `CHANGELOG.md` is missing or vague for user-visible behavior
- docs quickstart is stale for the default installation path
- a public API or response shape changed without tests and docs
- runtime-facing models lose `workspace_id` or policy checks
- release includes private local agent/editor state
- known security issue is being discussed in public instead of `SECURITY.md`

## Open-Source Readiness Gaps

Current readiness gaps to track before broader community growth:

- existing unlabeled backlog needs one-time label backfill
- CODEOWNERS is not defined yet
- release issue template exists, but release automation is still manual
- PyPI/TestPyPI publish workflow should be revisited before the next package release
- `SECURITY.md` claims need a follow-up audit against actual workflows
- contributor welcome/comment automation should wait until label quality is proven
- stale issue handling should start as a report, not an auto-closing bot
- Discord moderation and maintainer escalation rules are lightweight and should
  evolve with traffic

## SEOCHO Discord Channels

Use SEOCHO Discord channels as a real-time community surface, not as a proposal
tracker, evidence appendix, or monthly reporting board.

Recommended split:

| Channel | Type | Purpose |
|---|---|---|
| `#seocho-updates` | text | Low-noise release notes, curated announcements, and calls for testing |
| `#seocho-project` | forum | Design questions, MCP work, contributor questions, and user feedback |
| `seocho-office-hours` | voice | Live discussion, study sessions, office hours, and post-event follow-up |

Good posts:

- release announcements and pre-release calls for testing
- manually curated project summaries
- product review discussions that help users choose tools
- `help wanted` and `good first issue` highlights
- job, collaboration, internship, and research opportunities
- design or usage questions that need fast community feedback
- links to canonical GitHub issues, PRs, releases, and docs

Avoid:

- raw commit spam
- unverified performance claims
- sensitive security details
- private customer/user data
- decisions that exist only in Discord
- bot messages that mention users automatically

Tone:

- English-first for all durable posts, announcements, and product reviews
- Korean summaries are optional when they help local participants act quickly
- keep posts short, link to GitHub for details
- for product reviews, disclose version, usage context, cost, limits, and any
  vendor support or sponsorship

## GraphUserGroup And Ghost

GraphUserGroup is the broader graph community surface. SEOCHO should appear
there as one project track inside the graph ecosystem, not as the only center of
the community.

Use GraphUserGroup / Ghost for:

- weekly graph trend posts and newsletter delivery
- Graph Omakase, GraphTalk, interview, and seminar archives
- narrative SEOCHO explainers when a release, experiment, or case study needs
  more context than a Discord post
- contributor and event pages that should be discoverable outside Discord
- English versions of posts that are intended for LinkedIn, X, Reddit,
  Substack, GitHub, or conference follow-up

Do not add an empty Ghost tag to public navigation. Ghost may return 404 for a
tag page until at least one public post exists for that tag. For new surfaces
such as product reviews, job-board posts, or opportunities, create the first
public post or a dedicated page before promoting the section in site
navigation.

Use Discord for:

- real-time discussion after a Ghost post or seminar
- short links back to the canonical Ghost post, GitHub release, issue, PR, or
  docs page
- community Q&A and contributor onboarding
- product review follow-up discussions
- job-board and collaboration opportunities

Use GitHub for:

- issues, PRs, release notes, ADRs, and durable project decisions
- evidence links behind any SEOCHO technical claim

Do not treat Ghost posts or Discord threads as the canonical changelog. If a
Ghost post announces SEOCHO behavior, link to the matching GitHub release, PR,
issue, or docs page.

## Current GraphUserGroup Ghost Audit

As of 2026-07-12, GraphUserGroup has enough published content to support a
broader English-first community operating model, but the site should be cleaned
up before adding more public navigation.

Current public surfaces:

| Surface | Current state | Action |
|---|---|---|
| Home | live at `https://www.graphusergroup.com/` | revise site description to emphasize graph research, product experience, open source, and industry practice |
| Graph Omakase | live tag page with corrected SEO metadata | keep as weekly trend archive and route summaries to Discord |
| GraphTravel | live tag page with corrected SEO metadata | keep as scenario-based graph use-case archive |
| GraphTalks | live page, but currently combines many event records in one page | move future talks into event recap posts with `GraphTalks` tag |
| GraphInterview | live page, missing meta description | add concise English meta description |
| Contributor | live page, missing meta description | clarify how contributors join studies, content, reviews, and SEOCHO work |
| GraphInformation | live page but not in navigation | decide whether to archive, rename, or fold into event resources |
| SEOCHO | public tag exists in Ghost admin, but tag page is 404 until a post exists | publish a first SEOCHO explainer before adding tag page navigation |
| Product Review | public tag exists in Ghost admin, but tag page is 404 until a post exists | publish first product review/template before navigation |
| Job | public tag exists in Ghost admin, but tag page is 404 until a post exists | publish job-board guidelines before navigation |

Immediate production cleanup already completed:

- removed default Ghost social links for `x.com/ghost` and `facebook.com/ghost`
- fixed `GraphOmakase` SEO description and canonical tag page
- fixed `GraphTravel` title, SEO description, and canonical tag page
- kept empty tags out of public navigation until first content exists

Next Ghost changes:

- add meta descriptions for `GraphInterview`, `GraphTalks`, `Contributor`, and
  `GraphInformation`
- replace any Gmail-hosted images in public pages with Ghost-hosted images
- create first posts for `SEOCHO`, `Product Review`, and `Job`
- convert large event pages into smaller recap posts linked from a stable page
- use Ghost Admin API for draft creation, but keep publishing as a maintainer
  review step until the workflow is proven

## GitHub, Ghost, And Discord Operating Model

Run the three surfaces together, with different jobs:

| Surface | Primary job | Best content | Avoid |
|---|---|---|---|
| SEOCHO GitHub | Source of truth for the open-source project | code, issues, PRs, releases, docs, ADRs, examples, reproducible commands | long community essays, casual discussion, untracked decisions |
| GraphUserGroup / Ghost | Public editorial and archive surface | product reviews, graph trend articles, GraphTalk recaps, SEOCHO explainers, tutorials, newsletter posts | raw discussion, unfinished decisions, private coordination |
| Discord | Real-time community surface | Q&A, product-review discussion, job-board posts, event follow-up, study support, quick links to GitHub and Ghost | grant reporting, monthly plans, evidence packs, full article mirrors |

Default content flow:

1. GitHub publishes the durable project artifact: release, issue, PR, docs, or
   example.
2. Ghost turns selected artifacts into readable public context: tutorial,
   product review, event recap, or newsletter post.
3. Discord distributes the short link, hosts discussion, and collects questions
   or opportunities.
4. Useful Discord discussion is distilled back into a GitHub issue/docs update
   or a Ghost article when it becomes generally useful.

Default routing rules:

- Technical change or release: GitHub first, then `#seocho-updates`, then Ghost
  only when narrative context is useful.
- Product review: Ghost first, then `#product-reviews` for discussion.
- Weekly trend: Ghost or Knowledge OS draft first, then `#weekly-trend`.
- Event or YouTube recap: Ghost or event page first, then `#graphtalks` or
  `#events-workshops`.
- Job or collaboration opportunity: approved note first, then `#job-board`.
- User question that reveals a product gap: Discord first, then GitHub issue if
  it affects SEOCHO behavior or documentation.

Keep English titles and summaries consistent across all three surfaces so posts
can move from Discord to Ghost, GitHub, LinkedIn, X, Reddit, Substack, or
conference follow-up without rewriting the core message.

## Ghost Admin API Integration

Ghost Admin API can support this operating model, but only from a secure
server-side relay. Do not call the Admin API from Discord, browser-side code, or
public client code.

Recommended use:

| Direction | Mechanism | Use |
|---|---|---|
| GitHub or Knowledge OS -> Ghost | Ghost Admin API | create draft posts for product reviews, tutorials, weekly trends, event recaps, and SEOCHO explainers |
| Ghost -> Discord | Ghost webhook to a small relay | route published posts to `#weekly-trend`, `#product-reviews`, `#graphtalks`, `#events-workshops`, or `#seocho-updates` |
| Discord -> Ghost/GitHub | maintainer curation | turn useful community discussion into a Ghost article or GitHub issue/docs update |

Draft-first policy:

- The relay may create Ghost drafts with tags, title, excerpt, canonical links,
  and source references.
- Publishing should remain a maintainer action until the cadence and formatting
  are proven.
- Auto-publishing is allowed only for low-risk recurring posts after review,
  such as a curated weekly trend summary.
- Product reviews should disclose tested version, setup context, cost, limits,
  and whether vendor support or sponsorship was involved.

Security rules:

- Store `GHOST_ADMIN_API_URL`, `GHOST_ADMIN_API_KEY`, Discord webhook URLs, and
  relay secrets only in a server-side secret store or GitHub Actions secrets.
- Never commit Ghost Admin API keys, generated JWTs, staff tokens, cookies, or
  webhook URLs.
- Prefer a dedicated Ghost Custom Integration for this workflow.
- The relay should validate Ghost tags before posting to Discord.
- Discord webhook payloads should use `allowed_mentions: []`.
- Logs should record post id, tag, target Discord channel, and delivery status,
  but not secrets or full API payloads.

## Ghost CLI Operations

Use Ghost CLI for Ghost site operations when GraphUserGroup runs a self-hosted
Ghost instance or a self-hosted staging instance. Do not use Ghost CLI as the
content publishing automation path; use the Admin API for draft creation and
webhooks for publish-time routing.

Ghost CLI role:

| Area | Ghost CLI use | Notes |
|---|---|---|
| Install and staging | `ghost install`, `ghost install local`, `ghost setup` | useful for local or self-hosted staging environments |
| Runtime control | `ghost start`, `ghost stop`, `ghost restart`, `ghost ls` | useful for controlled self-hosted operations |
| Health and debugging | `ghost doctor`, `ghost log`, `ghost run` | use before or after upgrades, failed starts, or webhook issues |
| Upgrades and rollback | `ghost update`, `ghost update --rollback` | run with backup and maintenance-window discipline |
| Backup | `ghost backup` | create backups before updates, major changes, theme changes, or migration work |
| Configuration | `ghost config`, `ghost setup nginx ssl`, `ghost setup systemd` | only for maintainers with server access |

Operating split:

- Ghost Admin API: create or update editorial drafts, attach tags, upload images,
  and prepare Ghost posts from GitHub or Knowledge OS material.
- Ghost webhooks: notify a server-side relay after a post is published.
- Ghost CLI: operate the Ghost installation, inspect logs, run health checks,
  update Ghost, and create backups.

Safety rules:

- Use Ghost CLI only from the Ghost server or an approved maintenance shell.
- Keep CLI output that includes paths, usernames, hostnames, or config values out
  of public Discord.
- Run `ghost doctor` before risky install, setup, start, or update work.
- Run `ghost backup` before Ghost updates, major version upgrades, theme changes,
  or site migration.
- Prefer a staging Ghost instance for theme, webhook, and relay testing.
- If GraphUserGroup uses managed Ghost hosting without shell access, treat Ghost
  CLI as a local/staging tool and rely on Ghost Admin, Admin API, and webhooks
  for production operations.

## `#weekly-trend` Discord Channel

Use `#weekly-trend` for GraphUserGroup and Knowledge OS trend sensing.

Good posts:

- Graph Omakase post announcements
- Knowledge OS tech-sensing summaries
- notable graph database, GraphRAG, GNN, ontology, retrieval, and agent-memory
  links
- seminar follow-up links when the topic is trend-oriented rather than
  SEOCHO-specific

Avoid:

- every raw RSS item or unscreened link
- SEOCHO release announcements that belong in `#seocho-updates`
- benchmark claims without source links or caveats
- duplicate posts for the same Ghost article

Tone:

- English-first for durable summaries and international sharing
- Korean discussion is welcome, but posts that become public references should
  have an English title and English summary
- summarize why the item matters in one or two bullets
- link to the Ghost post when a longer explanation exists

## Discord Operating Plan

The Discord server should help people find useful graph information, review
products from a user perspective, discover opportunities, and continue
discussion after Ghost posts or events.

Proposal timelines, monthly operating plans, grant reporting, and evidence packs
belong in proposal documents, GitHub, Ghost, or internal notes. They should not
be posted as Discord operating content.

Operating language:

- Use English-first channel names, channel topics, event summaries, product
  reviews, and opportunity posts.
- Korean can be used for live Q&A, local coordination, and participant support.
- Any Discord post that will later be reused in GitHub, Ghost, LinkedIn, X,
  Reddit, Substack, or conference materials should have an English title and
  an English summary.
- When both languages are useful, use English first and add Korean below it.

Current GraphUserGroup Discord state, observed 2026-07-12:

| Area | Existing channel | Recommended use or cleanup |
|---|---|---|
| Welcome | `#introductions` | keep for member introductions and onboarding links |
| Graph Hub | `#communitycontent` | rename to `#content-feed` or keep as the canonical content feed from Ghost |
| Graph Hub | `#announcements` | keep for low-noise public announcements |
| Networking | `#jobboard` | rename to `#job-board` for English readability |
| Insights | `#weeklytrends` | rename to `#weekly-trends` and route GraphChef/Omakase summaries here |
| Project | two `#seocho` text channels | split into `#seocho-updates` for bot/release posts and `#seocho-dev` for discussion |
| Project | `seocho` voice | rename to `seocho-office-hours` |

Recommended additions before wider public promotion:

- `#product-reviews`: discussion linked from Ghost product review posts
- `#graphtalks`: event follow-up, videos, speaker materials, and questions
- `#contributor-hub`: first-time contributor questions, issues, docs, and study
  support

Recommended channel and forum shape:

| Surface | Type | Purpose |
|---|---|---|
| `#seocho-updates` | text | SEOCHO release notes, curated announcements, and calls for testing |
| `#seocho-project` | forum | SEOCHO usage, design questions, MCP work, and contributor discussion |
| `seocho-office-hours` | voice | live study sessions, project support, office hours, and post-event follow-up |
| `#weekly-trend` | text | GraphChef Daily, Graph Omakase Weekly, paper/product trend summaries |
| `#product-reviews` | forum | Neo4j, Neptune, Memgraph, TigerGraph, ArangoDB, GraphScope, RDF stores, and graph AI product reviews |
| `#job-board` | forum or text | graph, data, AI, infra, research, internship, and collaboration opportunities |
| `#graphtalks` | forum or text | seminar announcements, speaker materials, YouTube links, and attendee discussion |
| `#study-onboarding` | forum or text | beginner/intermediate/advanced study tracks and newcomer questions |
| `#events-workshops` | forum or text | KGC/CDL follow-up, local meetups, vendor workshops, and community sessions |
| `#contributor-hub` | forum | PR/issue/doc tasks, volunteer roles, maintainer requests |
| `#mod-log` | private text | AutoMod, moderation, workflow, and incident logs |
| `#incident-room` | private text | security, account, abuse, or high-severity coordination |

Recommended `#seocho-project` forum tags:

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

Recommended `#product-reviews` forum tags:

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

Recommended `#job-board` rules:

- Require role, organization, location or remote policy, expected skills, and
  application link or contact.
- Mark unpaid, volunteer, research, internship, or sponsor-driven opportunities
  clearly.
- Do not allow recruiter spam, vague hiring posts, scraping, or resume farming.
- Keep discussion in-thread; close stale posts after the opportunity expires.

Recommended `#weekly-trend` rhythm:

- daily: GraphChef Daily automated or semi-automated trend links
- weekly: Graph Omakase summary and discussion prompt
- as needed: English recap linking back to Ghost posts and notable Discord
  discussions

Recommended `#graphtalks` rhythm:

- before event: event page, speaker, abstract, target audience
- after event: English recap, slides, recording, YouTube link, and follow-up
  discussion prompt
- when a video is published: post the YouTube link with the topic, audience,
  and one question for discussion

Recommended `#study-onboarding` tracks:

- beginner: undergraduate-friendly setup, graph concepts, first SEOCHO example
- intermediate: graduate students and practitioners, GraphRAG, ontology, graph DB
  integration
- advanced: practitioner customization, MCP, Kubernetes, observability, deployment

Product review guardrails:

- Do not compare every graph product at once. Pick 2-4 products per project.
- Treat product comparisons as consumer-side experience reports: setup,
  loading, query, cost, operations, limitations, and fit.
- Separate hands-on experience, vendor-provided information, and community
  opinion.
- For performance or scalability claims, include hardware, dataset, command,
  versions, and skipped components.
- Link to the Ghost review, official product docs, GitHub issue/PR, video, or
  slide deck when a longer reference exists.
- Keep English summaries ready for international reuse even when the original
  session or Q&A happened in Korean.

## Discord Message Classes

| Class | Trigger | Owner | Discord shape |
|---|---|---|---|
| Release | GitHub release is published | maintainer + GitHub Actions | release title, bullets, link |
| Manual update | Maintainer dispatches `Discord Updates` | maintainer | curated one-off update |
| Product review | Ghost product-review post or maintainer draft | GraphUserGroup / maintainer | title, product, use case, cost/limits, link |
| Job-board | approved opportunity | moderator or trusted member | role, organization, location/remote, link, deadline |
| Weekly trend | Ghost post or Knowledge OS draft | GraphUserGroup / `agy` | short trend summary, Ghost link |
| GraphTalks/video | event page or YouTube upload | event organizer | topic, speaker, audience, video/material link |
| Incident/security | private report or high-severity issue | maintainer | public acknowledgement only after safe disclosure |
| Call for contributors | maintainer-curated issue batch | maintainer or `agy` draft | 3-5 tasks with labels and links |

## Ghost To Discord Routing

Ghost can publish webhook events such as `post.published`, but direct public
posting should start conservative: draft first, then post only after cadence and
tone are proven.

For managed publishing, use the Ghost Admin API to create or update drafts, then
use Ghost webhooks only after a post is published. The webhook should notify a
small relay, not Discord directly, so tag validation, formatting, and
`allowed_mentions` controls can be applied.

Recommended tag routing:

| Ghost tag | Discord channel | Message shape |
|---|---|---|
| `seocho` | `#seocho-updates` | curated project update, GitHub/docs link, Ghost link |
| `GraphOmakase` or `graphomakase` | `#weekly-trend` | weekly trend headline, why it matters, Ghost link |
| `weekly-trend` | `#weekly-trend` | short curated digest, Ghost link |
| `product-review` | `#product-reviews` | product, use case, tested version, cost/limits, Ghost link |
| `job` or `opportunity` | `#job-board` | role/opportunity, location/remote, deadline, link |
| `event` | `#events-workshops` or `#graphtalks` | date/time, registration or video link |
| `GraphTalks`, `graphtalks`, or `interview` | `#graphtalks` | guest, topic, video/materials link |

Guardrails:

- keep Discord webhook URLs out of Git
- keep Ghost Admin API keys server-side only
- do not create API keys, webhooks, or persistent access without maintainer
  review
- prefer a small relay or Knowledge OS command that validates tags before
  posting
- set `allowed_mentions` to empty on Discord webhook payloads
- do not mirror full Ghost posts into Discord; post a short summary and link
- route posts by tag so product reviews, jobs, events, and weekly trends land
  in different spaces

## Knowledge OS And `agy`

`agy` should use Discord as a real-time community channel, but not as a database.
The Knowledge OS side should draft and archive higher-context summaries in
Obsidian, then optionally post selected summaries.

Expected Knowledge OS responsibilities:

- monitor `Discord Updates` workflow health
- draft short SEOCHO update posts from GitHub releases, docs, and Ghost posts
- draft GraphUserGroup/Ghost-backed weekly trend announcements for
  `#weekly-trend`
- draft product review announcements from Ghost product-review posts
- draft job-board posts from approved opportunity notes
- avoid grant-reporting, monthly operating-plan, or evidence-pack language in
  public Discord posts
- draft release notes before maintainers publish a GitHub release
- track unresolved readiness gaps as issues or Obsidian tasks

Default mode for `agy`: draft first, then post only when the cadence and tone
are reviewed.

## Release Checklist Issue

Use GitHub's `Release checklist` issue template for every stable or pre-release
candidate. The issue should remain open until:

- release tag exists
- GitHub Release is published
- Discord announcement has been sent
- follow-up gaps are converted into issues or docs tasks

## Suggested Cadence

- Patch: as needed when a bug materially blocks users
- Minor: no more than weekly while alpha unless there is a clear adoption reason
- Pre-release: for connector/runtime/distributed features before broad docs claims
- Weekly Discord digest: start as draft-only; enable posting after two useful drafts

## Maintainer TODO

- backfill labels for old issues and PRs
- create a release-blocking label if release issues begin to accumulate
- decide whether package publishing should use manual `uv build`/`twine` or a
  GitHub Actions release workflow
- add CODEOWNERS after ownership is clear
- audit `SECURITY.md` and align it with actual workflows
