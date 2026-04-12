## Summary

<!-- 1-3 bullet points: what changed and why -->

## Test plan

- [ ] `python -m pytest seocho/tests/ -q` passes
- [ ] New/changed behavior has test coverage
- [ ] No hardcoded credentials or hostnames

## Checklist

- [ ] Commit message uses conventional prefix (`feat:`, `fix:`, `refactor:`, etc.)
- [ ] No direct `bolt://neo4j:7687` in runtime code (Docker-only in compose)
- [ ] `extraction_prompt` naming (not `prompt_template`) at public API boundaries
