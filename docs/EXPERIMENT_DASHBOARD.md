# Local experiment dashboard

Browse saved SEOCHO experiments, find failures and inspect the evidence behind
a change. The dashboard reads the `report.json` files produced by `seocho run`
and `seocho sweep`. It needs no frontend build, model key or running graph service.

```bash
# From an installed SEOCHO environment, or prefix with uv run in a dev checkout:
seocho runs dashboard ./runs
# Open http://127.0.0.1:8765

# Multiple explicit roots; port 0 prints an available local port:
seocho runs dashboard ./runs ./archived-runs --port 0
```

With no directories, the default is `./runs` relative to the current directory.
The server binds only to `127.0.0.1`. Stop it with Ctrl+C. It does not launch runs,
change graph data, upload artifacts or modify source reports. Browser refresh
and **Refresh runs** reload the UI; **Refresh runs** also rescans the directories.
New checkpoints remain invisible until that explicit rescan.

## Explore and inspect

- **Experiments:** search by name, workspace, model or report path. Filter by
  status, workspace and model. Catalog counts describe recorded executions;
  answered questions are nonempty responses, not correctness measurements.
- **Run detail:** inspect question answers, expected references, support,
  coverage, missing slots, graph evidence and full question records. Use the
  Diagnostics tab for failures/actions and Conditions for input fingerprints.
- **Compare runs:** choose two distinct runs, declare intentional changes and
  provide a hypothesis. The server delegates to `seocho runs compare`'s existing
  implementation. Incompatible inputs restrict aggregate deltas while keeping
  individual answers and failures visible. Missing usage/costs stay unavailable.
- **Open report:** open the existing standalone saved-run visualization in a
  new tab, including its module map. To save a portable HTML artifact, use
  `seocho runs view RUN_DIRECTORY --output view.html`.

The workflow takes inspiration from [Opik](https://www.comet.com/site/products/opik/).
This implementation uses SEOCHO's existing evidence and vendor-neutral JSONL/OTLP
contracts; it does not integrate the Opik SDK. Phase durations are observations,
not reconstructed trace spans. Request-level GPU/token cost reports use the
separate [inference cost workbench](INFERENCE_WORKBENCH.md).

## File and serving boundaries

A catalog is a private in-memory snapshot, limited to 500 runs, 8 MiB per report,
64 MiB of report bytes and 5,000 visited directories. Use narrower roots if a
limit is reached. Scan details list selected roots and notices for malformed,
unreadable or oversized reports. Symlinked reports/directories are excluded;
`.git`, `.venv`, `node_modules`, `__pycache__` and `cache` are not traversed.
Overlapping roots deduplicate reports. Legacy reports can be inspected; missing
outcomes remain unknown and missing fingerprints cannot support comparison.

Only selected reports and packaged assets are served. URLs use catalog IDs,
never arbitrary filesystem paths. Host/origin checks, restrictive content policy
and text-only rendering protect the local browser boundary. The dashboard has
no team authentication or remote serving mode; it is intended for a trusted
local machine. Select directories whose contents you intend to inspect.

## Troubleshooting

An empty catalog may mean the root does not exist, reports use a different
schema, or a scan limit was reached. Open **Data sources & scan details** to see
what was selected or omitted. Generic benchmark JSON and inference-cost reports
are not automatically interpreted as E2E receipts. Capture a supported run using
the [experiment guide](EXPERIMENT_PLATFORM.md), then rescan.

If the port is occupied, choose another with `--port`, or use `--port 0` and open
the printed URL. Use that localhost URL directly; reverse proxies and remote
hostnames are not supported. No live database compatibility, answer quality or
throughput claim follows from the dashboard's local HTTP/browser tests.

Architecture: [ADR-0235](decisions/ADR-0235-local-experiment-dashboard.md).
