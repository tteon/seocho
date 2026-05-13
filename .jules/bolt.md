When logging history, traces, or metadata (such as prompt history), prefer append-only `.jsonl` format over reading and rewriting full JSON arrays to prevent O(N^2) file I/O scaling bottlenecks.

When exporting or processing large .jsonl files, stream the data iteratively line-by-line rather than loading the entire file into memory to prevent O(N) memory scaling bottlenecks.
