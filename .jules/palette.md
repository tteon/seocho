# UX and Accessibility Maintenance Logs

## Key Learnings & Decisions
- Interactive UI elements must maintain explicit keyboard accessibility patterns.
- Specifically, `button:focus-visible` must use an explicit `outline` rather than relying solely on background changes.
- Inputs (`input`, `select`, `textarea`) must use `box-shadow` to enhance visual contrast on `:focus`.
