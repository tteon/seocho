Found several CLI empty states in `src/seocho/cli/__init__.py` (lines 982, 997, 1011) that use informal lowercase strings instead of proper sentence case and punctuation as required by SEOCHO UX guidelines.
- `print("no memories found")` -> `print("No memories found.")`
- `print("no graph targets configured")` -> `print("No graph targets configured.")`
- `print("no semantic artifacts found")` -> `print("No semantic artifacts found.")`
