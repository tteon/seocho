Codebase-specific UX/a11y learnings:
- In `src/seocho/cli/__init__.py`, `_print_search_results` prints "No memories found." when results is empty. This is changed to "No search results found." to avoid incorrect domain terminology.
- In `src/seocho/cli/traces.py`, prints "No matching spans." when spans is empty. This is changed to "No matching trace spans found." to maintain sentence case and proper domain terminology, consistent with CLI empty state and error messages.
