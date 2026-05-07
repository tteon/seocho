When logging history, traces, or metadata (such as prompt history), prefer append-only `.jsonl` format over reading and rewriting full JSON arrays to prevent O(N^2) file I/O scaling bottlenecks.
For performance optimization, regex patterns used for tokenization and normalization must be pre-compiled as module-level constants (prefixed with `_RE_`) to prevent recompilation in hot paths and loops.
