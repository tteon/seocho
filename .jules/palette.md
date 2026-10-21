Found an inconsistency in CLI empty states. Most empty states follow the "No <domain item> found." convention:
- "No quarantined items found."
- "No memories found."
- "No graph targets configured." (Could be "No graph targets found.", but "configured" is reasonable).
- "No semantic artifacts found."

However, `src/seocho/cli/traces.py:93` has `print("No matching spans.")` which misses the `found` verb at the end and `trace` context. I will update it to `print("No matching trace spans found.")` to match the empty state convention.
