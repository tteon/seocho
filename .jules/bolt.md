
- When streaming JSONL files, use `with path.open("r", encoding="utf-8") as f:` and iterate over `f` to load json line by line to avoid allocating a large list of string lines from `read_text().splitlines()` in memory (e.g., in `inference_receipts.py`).
- When loading simple JSON or YAML files from disk (e.g. state or receipts), prefer `json.load(f)` or `yaml.safe_load(f)` via a file context manager `with open(..., "r") as f:` over `json.loads(p.read_text())` to avoid allocating the intermediate string in memory.
