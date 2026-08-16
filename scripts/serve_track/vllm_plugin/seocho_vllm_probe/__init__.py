"""A vLLM stat-logger plugin that exports per-request KV-cache facts.

Why this exists. vLLM's KV-event stream carries no request id — a `BlockStored`
frame is `block_hashes + parent_block_hash + block_size + medium` and nothing
that names the caller — so the serve-track rig attributes blocks to a RAG stage
by containment in a wall-clock window, which is only sound while calls are
serialized. `FinishedRequestStats` already carries everything the join was
missing: `request_id`, `num_cached_tokens`, `num_prompt_tokens`, and a
prefill/queue/decode time breakdown.

Why a plugin rather than a patch. `EngineCoreProc` runs in its own process, so
monkey-patching `vllm.v1.core.kv_cache_coordinator` from the caller's process
reaches nothing. `vllm.stat_logger_plugins` is loaded inside every engine
process (`v1/engine/core.py` calls `load_general_plugins`), and unlike
`vllm.v1.core` it is a documented extension point — vLLM's compatibility
statement covers documented plugin interfaces, not engine internals.

Stability caveat, quoted from `StatLoggerBase` itself rather than the docs:
"note that the `SchedulerStats` and `IterationStats` classes are not considered
stable interfaces and may change in future versions." Pin the vLLM version this
is run against; it is written for 0.27.1.

Only the SERVER path loads this. `load_stat_logger_plugin_factories()` is called
in exactly one place in vLLM 0.27.1 — `vllm/v1/engine/async_llm.py`, inside
`AsyncLLM.__init__`. The offline `LLM(...)` batch API never calls it, so a probe
run through `LLM.generate()` produces an empty output file and no error: the
entry point resolves, the class is valid, and nothing is ever instantiated.
Verified on an RTX 3070 against vLLM 0.27.1 — `LLM()` wrote zero records while
`vllm serve` wrote the engine-initialized line plus one row per request.
`scripts/serve_track/smoke_plugin.py` is the check; run it after any vLLM bump.

Once it is loaded, `log_stats` turns itself on: AsyncLLM sets
`self.log_stats = log_stats or has_custom_loggers`, so `--disable-log-stats`
does not silence this plugin.

Install into the *vLLM* environment (not the SDK's) and enable by name:

    VIRTUAL_ENV=~/.venvs/vllm-serve uv pip install -e scripts/serve_track/vllm_plugin
    SEOCHO_PROBE_OUT=outputs/serve_track/<run>/request_stats.jsonl \\
      VLLM_PLUGINS=seocho_probe scripts/serve_track/launch_vllm.sh
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from vllm.v1.metrics.loggers import StatLoggerBase

PROBE_SCHEMA_VERSION = 1
_ENV_OUT = "SEOCHO_PROBE_OUT"
_DEFAULT_OUT = "outputs/serve_track/request_stats.jsonl"


def _finish_reason(value: Any) -> str:
    return getattr(value, "name", None) or str(value)


class SeochoRequestStatsLogger(StatLoggerBase):
    """Append one JSONL record per finished request.

    Deliberately does nothing per *iteration*: iteration stats are hot-path and
    high-volume, and every question this rig asks is answerable from the
    per-request record. Cheap logging keeps the instrument from perturbing the
    thing it measures.
    """

    # One engine writes from one thread, but data-parallel runs give each engine
    # its own logger in its own process appending to the same file. O_APPEND
    # keeps whole lines intact; the lock only guards this process.
    _lock = threading.Lock()

    def __init__(self, vllm_config: Any, engine_index: int = 0) -> None:
        self.engine_index = engine_index
        self.path = Path(os.environ.get(_ENV_OUT, _DEFAULT_OUT))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        model = getattr(getattr(vllm_config, "model_config", None), "model", "")
        self.model = str(model or "")

    def record(
        self,
        scheduler_stats: Any = None,
        iteration_stats: Any = None,
        mm_cache_stats: Any = None,
        engine_idx: int = 0,
    ) -> None:
        finished = getattr(iteration_stats, "finished_requests", None)
        if not finished:
            return
        rows = []
        for stats in finished:
            row = asdict(stats) if is_dataclass(stats) else dict(vars(stats))
            row["finish_reason"] = _finish_reason(row.get("finish_reason"))
            row["probe_schema"] = PROBE_SCHEMA_VERSION
            row["engine_idx"] = engine_idx or self.engine_index
            row["model"] = self.model
            rows.append(row)
        self._append(rows)

    def _append(self, rows: list[dict]) -> None:
        # A logging failure must never take down an engine step.
        try:
            payload = "".join(
                json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows
            )
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
        except Exception:
            pass

    def log_engine_initialized(self) -> None:
        self._append(
            [
                {
                    "probe_schema": PROBE_SCHEMA_VERSION,
                    "event": "engine_initialized",
                    "engine_idx": self.engine_index,
                    "model": self.model,
                }
            ]
        )


def read_request_stats(path: str | os.PathLike[str]) -> list[dict]:
    """Read the probe output, refusing to mix schema generations."""
    records: list[dict] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("probe_schema") != PROBE_SCHEMA_VERSION:
                raise ValueError(
                    f"probe_schema {record.get('probe_schema')} != "
                    f"{PROBE_SCHEMA_VERSION}; refusing to silently mix generations"
                )
            records.append(record)
    return records


__all__ = ["SeochoRequestStatsLogger", "read_request_stats", "PROBE_SCHEMA_VERSION"]
