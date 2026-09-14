"""Exercise real receipt files and local HTTP, without model or database calls."""

from __future__ import annotations

import copy
import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from seocho.cli import build_parser
from seocho.dashboard import catalog
from seocho.dashboard.server import DashboardServer
from seocho.run_comparison import REQUIRED, compare_runs
from seocho.run_evidence import SCHEMA


def receipt(name: str = "Synthetic baseline") -> dict[str, Any]:
    return {
        "run": {
            "name": name,
            "workspace_id": "synthetic",
            "models": {"query": "fixture"},
            "question_count": 2,
            "durations": {"query_s": 2.5},
        },
        "outcome": {"status": "completed"},
        "reproducibility": {
            "schema_version": SCHEMA,
            "conditions": {k: "a" * 64 for k in REQUIRED},
            "gaps": [],
        },
        "queries": [
            {
                "id": "q1",
                "question": "Synthetic question",
                "answer": "Synthetic answer",
                "latency_s": 1.2,
            },
            {
                "id": "q2",
                "question": "Failure path",
                "error": "Synthetic failure",
                "answer": "",
            },
        ],
    }


def save(root: Path, data: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "report.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_catalog_snapshot_refresh_and_unknown_values(tmp_path: Path) -> None:
    data = receipt()
    del data["outcome"]
    del data["run"]["durations"]
    path = save(tmp_path / "run", data)
    before = path.read_bytes()
    snapshot = catalog.scan((tmp_path, tmp_path / "run"))
    assert len(snapshot.rows) == 1  # overlapping roots never duplicate a receipt
    row = snapshot.rows[0]
    assert row["status"] == "unknown"
    assert row["query_seconds"] is None
    assert (row["answered"], row["errors"]) == (1, 1)
    assert path.read_bytes() == before
    data["run"]["name"] = "Updated"
    save(path.parent, data)
    updated = catalog.scan((tmp_path,))
    assert snapshot.reports[row["id"]]["run"]["name"] == "Synthetic baseline"
    assert updated.rows[0]["id"] == row["id"]
    assert updated.rows[0]["name"] == "Updated"


def test_catalog_rejects_escapes_and_malformed_receipts(tmp_path: Path) -> None:
    external = save(tmp_path / "outside", receipt())
    root = tmp_path / "selected"
    (root / "link-file").mkdir(parents=True)
    (root / "link-file" / "report.json").symlink_to(external)
    (root / "link-dir").symlink_to(external.parent, target_is_directory=True)
    invalid = save(root / "invalid", {"run": {}, "queries": ["bad"]})
    save(root / "infinity", {"run": {}, "value": float("inf")})
    before = invalid.read_bytes()
    snapshot = catalog.scan((root, tmp_path / "missing"))
    assert not snapshot.rows
    assert len(snapshot.warnings) == 4
    assert invalid.read_bytes() == before


@pytest.mark.parametrize("budget", ["MAX_RUNS", "MAX_TOTAL_BYTES", "MAX_DIRECTORIES"])
def test_catalog_surfaces_omitted_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    save(tmp_path / "run", receipt())
    monkeypatch.setattr(catalog, budget, 0)
    snapshot = catalog.scan((tmp_path,))
    assert not snapshot.rows
    assert any("limit reached" in w["reason"] for w in snapshot.warnings)


def test_catalog_bounds_report_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = save(tmp_path, receipt())
    monkeypatch.setattr(catalog, "MAX_REPORT_BYTES", 10)
    assert not catalog.scan((tmp_path,)).rows
    assert path.is_file()


def test_http_snapshot_comparison_assets_and_read_boundary(tmp_path: Path) -> None:
    before, after = receipt(), receipt("Synthetic candidate")
    after["reproducibility"]["conditions"]["models"] = "b" * 64
    after["queries"][0]["answer"] = "<script>window.injected=true</script>"
    left = save(tmp_path / "baseline", before)
    right = save(tmp_path / "candidate", after)
    original = (left.read_bytes(), right.read_bytes())
    with DashboardServer((tmp_path,), 0) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def request(
            path: str, *, headers: dict[str, str] | None = None, method: str = "GET"
        ) -> tuple[int, bytes, dict[str, str]]:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request(method, path, headers=headers or {})
                response = connection.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally:
                connection.close()

        try:
            code, body, headers = request("/")
            assert code == 200 and b"/app.js" in body
            assert "script-src 'self'" in headers["Content-Security-Policy"]
            assert "Access-Control-Allow-Origin" not in headers
            for asset in ("/app.js", "/style.css"):
                assert request(asset)[0] == 200
            listing = json.loads(request("/api/runs")[1])
            ids = {r["name"]: r["id"] for r in listing["runs"]}
            bid, cid = ids["Synthetic baseline"], ids["Synthetic candidate"]
            assert json.loads(request(f"/api/runs/{cid}")[1])["report"] == after
            query = urlencode({"baseline": bid, "candidate": cid})
            result = json.loads(request("/api/compare?" + query)[1])
            assert result == compare_runs(before, after)
            assert not result["comparable"]
            matched = json.loads(
                request("/api/compare?" + query + "&change=models&hypothesis=Test")[1]
            )
            assert matched["comparable"]
            assert matched["metrics"]["usage.cost_usd"]["delta"] is None
            assert request("/api/compare?" + query + "&change=models")[0] == 422
            assert request(f"/api/compare?baseline={bid}&candidate={bid}")[0] == 422
            export = request(f"/view/{cid}")
            assert export[0] == 200 and b"<script>window.injected" not in export[1]
            for path in (
                "/../report.json",
                "/api/runs/../../outside",
                "/view/unknown",
                "/.env",
            ):
                assert request(path)[0] == 404
            assert request("/api/runs", headers={"Host": "attacker.example"})[0] == 403
            assert (
                request("/api/runs", headers={"Origin": "https://attacker.example"})[0]
                == 403
            )
            assert request("/api/runs", method="POST")[0] == 501
            changed = copy.deepcopy(after)
            changed["run"]["name"] = "Refreshed"
            save(right.parent, changed)
            assert json.loads(request(f"/api/runs/{cid}")[1])["report"] == after
            assert len(json.loads(request("/api/runs?refresh=1")[1])["runs"]) == 2
            assert json.loads(request(f"/api/runs/{cid}")[1])["report"] == changed
            save(right.parent, after)
        finally:
            server.shutdown()
            worker.join(timeout=5)
    assert (left.read_bytes(), right.read_bytes()) == original


def test_dashboard_cli_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from seocho.cli.runs import handle
    from seocho.dashboard import server

    calls: list[tuple[tuple[Path, ...], int]] = []
    monkeypatch.setattr(
        server, "serve", lambda roots, port: calls.append((roots, port))
    )
    parser = build_parser()
    assert handle(parser.parse_args(["runs", "dashboard"])) == 0
    assert calls.pop() == ((Path("runs"),), 8765)
    assert (
        handle(parser.parse_args(["runs", "dashboard", str(tmp_path), "--port", "0"]))
        == 0
    )
    assert calls.pop() == ((tmp_path,), 0)
    with pytest.raises(ValueError, match="Port"):
        server.DashboardServer((tmp_path,), -1)
