Switched prompt_history logging from reading/writing full JSON arrays to append-only JSONL format to prevent O(N^2) file I/O overhead.
