# Performance Learnings

- When reading `.jsonl` or `.env` files in scripts or evaluations, avoid using `Path.read_text().splitlines()`. This reads the entire file into memory as a string and duplicates it into a list of lines, doubling the memory footprint. This is especially problematic for large telemetry logs or traces. Instead, use a lazy file iterator with a context manager: `with path.open('r', encoding='utf-8') as f:` and process lines lazily.
