UX/a11y learnings:
- Search empty states in `src/seocho/cli/__init__.py` were displaying 'No memories found.' which does not align with the broader 'search' domain terminology. Changed it to 'No search results found.' to reduce confusion for operators querying arbitrary objects via the CLI.
