- Optimized memory usage for large JSONL files by replacing Path.read_text().splitlines() with lazy, line-by-line iteration using a context manager (with open(...) as f: for line in f:).

- Replaced multiple instances of `path.read_text().splitlines()` with lazy file iteration `with path.open() as f: for raw in f:` in scripts.
- Replaced multiple instances of `json.loads(path.read_text())` with `with path.open() as f: json.load(f)` to optimize memory usage by eliminating large intermediate string allocations, aligning with the "avoidable file I/O or JSONL overhead" priority.
- Preserved exact original crash/exception behavior when applying file context managers, rather than silently catching JSONDecodeError.
