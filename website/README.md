# SEOCHO Website

This directory hosts the official documentation and landing page for the
[SEOCHO](https://github.com/tteon/seocho) project inside the main repository.

## Tech Stack
- **Framework**: [Astro](https://astro.build)
- **Docs Theme**: [Starlight](https://starlight.astro.build)
- **Styling**: Tailwind CSS + Custom Dark Theme
- **Deployment**: Automatic via GitHub Actions to GitHub Pages.

## Source Of Truth

This directory is the website presentation layer.

- public domain: `https://seocho.blog`
- source of truth: repo-root `README.md` and `docs/*`

Current site-doc policy is build-time generation inside this repository:

- `scripts/generate-docs.mjs` materializes selected `/docs/*` and `/blog/*`
  pages into `src/content/docs/` before `dev` and `build`
- generated pages should say `Source mirrored from ...`
- generated pages under `src/content/docs/docs/` are derived artifacts;
  edit the repo-root source docs instead

## GitHub Pages Boundaries

`seocho.blog` is a static GitHub Pages site. Treat it as a fast public docs and
product surface, not as a hosted SEOCHO runtime.

Good fits:

- Starlight docs, static search, sidebars, and content collections
- build-time generated pages from repo-root docs
- static examples, diagrams, screenshots, and release notes
- links to GitHub issues, PRs, releases, actions, and Discord announcements

Avoid:

- server-side secrets, OAuth callbacks, webhooks, or private context graph data
- runtime demos that imply a live backend exists on the docs host
- auto-posting or community workflows that should live in GitHub Actions or
  Knowledge OS
- docs pages that are only useful to maintainers on a first read

Docs UX policy:

- `/docs/` should answer "what should I read next?" before it lists files
- sidebar groups should follow user jobs: start, build, operate, contribute
- the first code path should be the lightest useful path (`Seocho.local(...)`)
- internal migration and governance docs should stay discoverable but out of
  the first-read path

Read the repo-root [AGENTS.md](../AGENTS.md) before making doc or site changes.

## Local Development

1. Install dependencies:
   ```bash
   npm ci
   ```
2. Generate site docs:
   ```bash
   npm run docs:generate
   ```
3. Start the dev server:
   ```bash
   npm run dev
   ```
4. Build for production:
   ```bash
   npm run build
   ```
5. Run doc quality and built-link checks:
   ```bash
   npm run check:docs
   bash scripts/check-built-links.sh
   ```
