- Ensure all interactive elements like select, input, textarea have 'box-shadow: 0 0 0 1px var(--accent-blue);' on :focus and buttons have 'button:focus-visible { outline: 2px solid var(--accent-blue); outline-offset: 2px; }' for proper keyboard accessibility.
- All form controls (<input>, <select>, <textarea>) in the evaluation UI must have explicit `aria-label` attributes to satisfy strict accessibility requirements, even when implicitly wrapped inside a `<label>` element.

- In the evaluation UI, avoid using inline styles and inline JavaScript event handlers (e.g., `onmouseover`, `onmouseout`). Use standard CSS rules and pseudo-classes (`:hover`, `:focus-visible`) in `evaluation/static/styles.css` for better maintainability and keyboard accessibility.
