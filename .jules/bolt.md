When logging history, traces, or metadata (such as prompt history), prefer append-only `.jsonl` format over reading and rewriting full JSON arrays to prevent O(N^2) file I/O scaling bottlenecks.

When processing JSONL trace exports in `seocho/tracing.py`, always stream rows directly from the file to `csv.DictWriter.writerow` instead of accumulating a full list of dicts in memory (e.g., via `records.append(record)`), as trace files can grow arbitrarily large resulting in O(N) memory exhaustion.
