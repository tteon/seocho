
### Codebase-Specific UX/A11y Learnings

*   **CLI Consistency:** The CLI has a `--json` argument that was inconsistently documented. Some commands had `help="Emit JSON"`, others `help="JSON output"`, others `help="Emit JSON output"`, and some had no help text. In SEOCHO, the standard for this flag is `help="Emit JSON"`. All instances have been standardized in `src/seocho/cli/options.py`, `src/seocho/cli/parsers.py`, `src/seocho/cli/ontology.py`, `src/seocho/cli/run.py`, `src/seocho/cli/runs.py`, `src/seocho/cli/sweep.py`, and `src/seocho/cli/traces.py`.
