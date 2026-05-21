## Performance Optimization Learnings

- **N+1 Query Elimination in Store Implementations**: When implementing aggregate or counting methods in `GraphStore` backends (e.g., `LadybugGraphStore.count_by_source`), iterating sequentially over declared table/schema lists and issuing separate `_locked_execute` queries per item introduces severe N+1 latency. We eliminated this by batching requests using `UNION ALL` statements over chunked label sets (e.g., chunks of 50). This drastically reduces backend round trips.
