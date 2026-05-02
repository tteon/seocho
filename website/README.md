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
