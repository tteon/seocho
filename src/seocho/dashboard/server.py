"""Loopback-only HTTP transport for the read-only experiment catalog."""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..run_comparison import CHANGEABLE, compare_runs
from ..run_visualization import render_run_view
from ..run_outcomes import query_state
from .catalog import scan

logger = logging.getLogger(__name__)
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, roots: tuple[Path, ...], port: int = 8765) -> None:
        if not 0 <= port <= 65535:
            raise ValueError("Port must be between 0 and 65535")
        self.roots = roots
        self.catalog = scan(roots)
        self.catalog_lock = Lock()
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("Dashboard: " + format, *args)

    def send(
        self, content: bytes, kind: str, status: int = 200, *, exported: bool = False
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        # The standalone export has its own hash-based CSP.
        if not exported:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            )
        self.end_headers()
        self.wfile.write(content)

    def json(self, data: Any, status: int = 200) -> None:
        self.send(
            json.dumps(data, ensure_ascii=False, allow_nan=False).encode(),
            "application/json",
            status,
        )

    def do_GET(self) -> None:
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in hosts or (origin and origin != f"http://{host}"):
            self.json(
                {"error": "Only same-origin localhost requests are allowed."}, 403
            )
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        if path in ASSETS:
            name, kind = ASSETS[path]
            self.send(files("seocho.dashboard").joinpath(name).read_bytes(), kind)
            return
        query = parse_qs(parsed.query)
        if path == "/api/runs":
            with self.server.catalog_lock:
                if query.get("refresh") == ["1"]:
                    self.server.catalog = scan(self.server.roots)
                listing = self.server.catalog.listing()
            self.json({**listing, "changeable": CHANGEABLE})
            return
        with self.server.catalog_lock:
            catalog = self.server.catalog
        try:
            if path.startswith("/api/runs/"):
                identity = path.removeprefix("/api/runs/")
                report = catalog.reports[identity]
                self.json(
                    {
                        "report": report,
                        "summary": next(
                            row for row in catalog.rows if row["id"] == identity
                        ),
                        "query_states": [
                            query_state(q) for q in report.get("queries", [])
                        ],
                    }
                )
            elif path.startswith("/view/"):
                identity = path.removeprefix("/view/")
                self.send(
                    render_run_view(catalog.reports[identity]).encode(),
                    "text/html",
                    exported=True,
                )
            elif path == "/api/compare":
                before = query.get("baseline", [""])[0]
                after = query.get("candidate", [""])[0]
                if before == after:
                    raise ValueError("Select two different runs.")
                result = compare_runs(
                    catalog.reports[before],
                    catalog.reports[after],
                    changes=query.get("change", []),
                    hypothesis=query.get("hypothesis", [""])[0],
                )
                self.json(result)
            else:
                self.json({"error": "Not found"}, 404)
        except KeyError:
            self.json(
                {
                    "error": "Run is unavailable. Refresh the catalog and select it again."
                },
                404,
            )
        except (ValueError, TypeError, AttributeError, OverflowError):
            self.json(
                {
                    "error": "Cannot compare or render these records. Check their metadata, choose two runs, and provide a hypothesis for declared changes."
                },
                422,
            )


def serve(roots: tuple[Path, ...], port: int = 8765) -> None:
    """Serve until Ctrl+C; this CLI entrypoint never launches a browser or model."""
    with DashboardServer(roots, port) as server:
        print(
            f"SEOCHO experiment dashboard: http://127.0.0.1:{server.server_port}",
            flush=True,
        )
        print(
            f"Reading {len(server.catalog.rows)} saved runs. Refresh in the browser to rescan. Ctrl+C to stop.",
            flush=True,
        )
        with suppress(KeyboardInterrupt):
            server.serve_forever()
