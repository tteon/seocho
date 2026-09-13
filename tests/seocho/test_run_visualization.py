from __future__ import annotations

import json
from pathlib import Path

from seocho.cli import main
from seocho.run_visualization import render_run_view


def test_html_escapes_untrusted_content_and_has_no_remote_assets() -> None:
    attack = '<script>alert("unsafe")</script>'
    rendered = render_run_view(
        {
            "run": {"name": attack},
            "queries": [
                {"id": attack, "question": attack, "answer": attack, "error": attack}
            ],
            "diagnostics": [{"message": attack, "code": "bad"}],
        }
    )
    assert attack not in rendered
    assert "&lt;script&gt;" in rendered
    assert "connect-src" not in rendered  # default-src none denies network access
    assert "default-src 'none'" in rendered
    assert 'src="http' not in rendered and 'href="http' not in rendered
    assert "Architecture reference only" in rendered
    assert "Module-level timings are unavailable" in rendered


def test_view_cli_preserves_original_reports_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    source = tmp_path / "report.json"
    raw = json.dumps(
        {
            "run": {"name": "fixture"},
            "queries": [],
            "outcome": {"status": "interrupted"},
        }
    )
    source.write_text(raw)
    output = tmp_path / "view.html"
    args = ["runs", "view", str(source), "--output", str(output)]
    assert main(args) == 0
    html = output.read_bytes()
    assert b"interrupted" in html
    assert b"unavailable" in html
    assert main(args) == 1
    assert output.read_bytes() == html
    assert source.read_text() == raw


def test_view_shows_incomparable_baseline_without_invented_deltas(
    tmp_path: Path,
) -> None:
    source = tmp_path / "old.json"
    source.write_text(json.dumps({"run": {}, "queries": []}))
    output = tmp_path / "view.html"
    assert (
        main(
            [
                "runs",
                "view",
                str(source),
                "--baseline",
                str(source),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert "incomparable" in output.read_text()
    assert "legacy reports are diagnostic only" in output.read_text()
