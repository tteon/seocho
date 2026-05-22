Pre-compile regex patterns as module-level constants (prefixed with _RE_) to prevent recompilation in hot paths and loops, such as in Cypher validation guards.
