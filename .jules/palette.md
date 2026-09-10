In the SEOCHO repository, CLI empty state and error messages (e.g., printed strings in `src/seocho/cli/`) should use proper sentence case and punctuation (e.g., 'No memories found.') rather than informal lowercase strings.

The following lines in `src/seocho/cli/__init__.py` violate this convention:
- Line 982: `print("no memories found")`
- Line 997: `print("no graph targets configured")`
- Line 1011: `print("no semantic artifacts found")`

These will be fixed to:
- `print("No memories found.")`
- `print("No graph targets configured.")`
- `print("No semantic artifacts found.")`
