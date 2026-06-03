"""SQLite span store for profiling runs.

SQLite (not the repo's usual JSONL) is justified here because the value of the
store is CROSS-RUN comparison and regression ("did candidate X's verdict flip
between SHAs?") — a relational query, not a stream. One file:
`outputs/profiling/spans.db`.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  kind TEXT,                       -- 'discovery' | 'adjudication'
  git_sha TEXT, dirty INTEGER,
  seed INTEGER, build_profile TEXT,
  host TEXT, py_version TEXT, created_utc TEXT
);
CREATE TABLE IF NOT EXISTS spans (
  run_id TEXT, stage TEXT, label TEXT,
  min_s REAL, median_s REAL, p90_s REAL, n INTEGER,
  self_time_s REAL,                -- discovery only
  marshal_pct REAL,                -- adjudication PyO3 only
  items INTEGER, note TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS verdicts (
  run_id TEXT, candidate TEXT,
  c1_wholepath_ab INTEGER, c2_marshal_ok INTEGER, c3_amdahl_share REAL,
  c3_provenance TEXT, c4_beats_incumbent INTEGER, c5_parity_determinism INTEGER,
  decision TEXT, scorecard_md TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
"""


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return ""


class SpanStore:
    def __init__(self, path: Optional[Path] = None):
        root = Path(__file__).resolve().parents[2]
        self.path = path or (root / "outputs" / "profiling" / "spans.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def start_run(self, kind: str, *, seed: int = 42, build_profile: str = "n/a") -> str:
        run_id = uuid.uuid4().hex[:16]
        self.conn.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, kind, _git("rev-parse", "--short", "HEAD"),
             1 if _git("status", "--porcelain") else 0, seed, build_profile,
             os.uname().nodename, sys.version.split()[0],
             datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return run_id

    def add_span(self, run_id: str, stage: str, label: str, *, sample: Any = None,
                 self_time_s: Optional[float] = None, marshal_pct: Optional[float] = None,
                 items: Optional[int] = None, note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO spans (run_id,stage,label,min_s,median_s,p90_s,n,self_time_s,marshal_pct,items,note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, stage, label,
             getattr(sample, "min_s", None), getattr(sample, "median_s", None),
             getattr(sample, "p90_s", None), getattr(sample, "n", None),
             self_time_s, marshal_pct, items, note),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
