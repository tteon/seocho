"""Prometheus exporter for DozerDB CE, polled over Bolt.

DozerDB CE 5.26 registers no ``org.neo4j`` MBeans and carries no metrics
subsystem (measured 2026-08-15: ``dbms.queryJmx("*:*")`` returns JVM beans
only; ``SHOW SETTINGS`` has zero ``metrics`` entries), so the usual JMX
javaagent route has nothing server-specific to read. What the server *does*
answer over Bolt is enough for the saturation signal:

- ``java.lang:type=Memory`` / ``GarbageCollector`` — heap pressure and GC
- ``java.nio BufferPool`` (direct, mapped) — the memory the page cache and
  memory-mapped store files actually occupy; a *size* proxy, not a hit ratio
- ``SHOW TRANSACTIONS`` — active transactions, the concurrency signal

Page-cache *hit/fault* is not exported by CE at all. Its observable shadow is
device reads: a miss materializes as container block I/O, which cAdvisor
reports per container (``container_blkio_device_usage_total``). Pair this
exporter with the cadvisor service in the observability overlay.

Requires the pinned driver (ADR-0111): ``pip install neo4j-rust-ext``.

Usage:
  DOZERDB_BOLT_URI=bolt://localhost:7687 DOZERDB_PASSWORD=... \
      python examples/observability/dozerdb_bolt_exporter.py
"""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Tuple


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_prometheus(samples: List[Tuple[str, Dict[str, str], float]]) -> str:
    """Render (name, labels, value) samples as Prometheus text format."""
    lines: List[str] = []
    for name, labels, value in samples:
        if labels:
            body = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
            lines.append(f"{name}{{{body}}} {value}")
        else:
            lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


def samples_from_beans(beans: List[Dict[str, Any]]) -> List[Tuple[str, Dict[str, str], float]]:
    """Extract saturation samples from ``dbms.queryJmx`` bean rows."""
    samples: List[Tuple[str, Dict[str, str], float]] = []
    for bean in beans:
        name = str(bean.get("name", ""))
        attrs = bean.get("attributes") or {}

        def value_of(attribute: str) -> Any:
            entry = attrs.get(attribute) or {}
            return entry.get("value") if isinstance(entry, dict) else None

        if name == "java.lang:type=Memory":
            usage = value_of("HeapMemoryUsage") or {}
            props = usage.get("properties", {}) if isinstance(usage, dict) else {}
            for key in ("used", "committed", "max"):
                if isinstance(props.get(key), (int, float)):
                    samples.append((f"dozerdb_jvm_heap_{key}_bytes", {}, float(props[key])))
        elif "type=GarbageCollector" in name:
            collector = name.split("name=")[-1].split(",")[0]
            count = value_of("CollectionCount")
            elapsed = value_of("CollectionTime")
            if isinstance(count, (int, float)):
                samples.append(
                    ("dozerdb_jvm_gc_collection_total", {"collector": collector}, float(count))
                )
            if isinstance(elapsed, (int, float)):
                samples.append(
                    ("dozerdb_jvm_gc_time_ms_total", {"collector": collector}, float(elapsed))
                )
        elif "type=BufferPool" in name:
            pool = name.split("name=")[-1].split(",")[0]
            for attribute, suffix in (
                ("MemoryUsed", "used_bytes"),
                ("TotalCapacity", "capacity_bytes"),
                ("Count", "count"),
            ):
                value = value_of(attribute)
                if isinstance(value, (int, float)) and value >= 0:
                    samples.append(
                        (f"dozerdb_jvm_buffer_pool_{suffix}", {"pool": pool}, float(value))
                    )
    return samples


class Collector:
    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def collect(self) -> List[Tuple[str, Dict[str, str], float]]:
        samples: List[Tuple[str, Dict[str, str], float]] = []
        with self._driver.session() as session:
            beans = session.run(
                'CALL dbms.queryJmx("java.lang:*") YIELD name, attributes '
                "RETURN name, attributes"
            ).data()
            beans += session.run(
                'CALL dbms.queryJmx("java.nio:*") YIELD name, attributes '
                "RETURN name, attributes"
            ).data()
            samples.extend(samples_from_beans(beans))
            transactions = session.run("SHOW TRANSACTIONS YIELD transactionId").data()
            samples.append(("dozerdb_active_transactions", {}, float(len(transactions))))
        samples.append(("dozerdb_exporter_up", {}, 1.0))
        return samples


def main() -> None:  # pragma: no cover - wiring, exercised live
    uri = os.getenv("DOZERDB_BOLT_URI", "bolt://localhost:7687")
    user = os.getenv("DOZERDB_USER", "neo4j")
    password = os.environ["DOZERDB_PASSWORD"]
    port = int(os.getenv("EXPORTER_PORT", "9141"))
    interval = float(os.getenv("POLL_INTERVAL_S", "15"))

    collector = Collector(uri, user, password)
    latest = {"body": render_prometheus([("dozerdb_exporter_up", {}, 0.0)])}

    def poll() -> None:
        while True:
            try:
                latest["body"] = render_prometheus(collector.collect())
            except Exception:
                latest["body"] = render_prometheus([("dozerdb_exporter_up", {}, 0.0)])
            time.sleep(interval)

    threading.Thread(target=poll, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            body = latest["body"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
