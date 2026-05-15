When logging history, traces, or metadata (such as prompt history), prefer append-only `.jsonl` format over reading and rewriting full JSON arrays to prevent O(N^2) file I/O scaling bottlenecks.
Optimized JSONL parsing in JSONLResponseCache by seeking to _last_offset and maintaining an in-memory index dictionary to prevent O(N^2) file I/O overhead on repeated lookups.
