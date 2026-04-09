from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "land.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_land_pipeline_success_with_passed_doctor(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    ops = bin_dir / "ops-check.sh"
    doctor = bin_dir / "gt-doctor.sh"
    guard = bin_dir / "beads-path-guard.sh"
    gt_land = bin_dir / "gt-land.sh"

    _write_executable(ops, "#!/usr/bin/env bash\nset -euo pipefail\necho OPS_OK\n")
    _write_executable(
        doctor,
        """#!/usr/bin/env bash
set -euo pipefail
cat <<'JSON'
{"checks":[{"name":"misclassified-wisps","status":"pass"},{"name":"single-beads-path","status":"pass"},{"name":"runtime-file-isolation","status":"pass"},{"name":"embedded-git-clones","status":"pass"}]}
JSON
""",
    )
    _write_executable(guard, "#!/usr/bin/env bash\nset -euo pipefail\necho GUARD_OK\n")
    _write_executable(gt_land, "#!/usr/bin/env bash\nset -euo pipefail\necho LAND_OK\n")

    env = dict(os.environ)
    env["OPS_CHECK_CMD"] = str(ops)
    env["DOCTOR_CMD"] = str(doctor)
    env["PATH_GUARD_CMD"] = str(guard)
    env["GT_LAND_CMD"] = str(gt_land)

    result = subprocess.run(
        [str(SCRIPT_PATH), "--task-id", "hq-land-test", "--dry-run"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "[land] completed successfully." in result.stdout


def test_land_pipeline_applies_fix_and_recovers(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter_file = tmp_path / "doctor_calls.txt"
    guard_marker = tmp_path / "guard_called.txt"

    ops = bin_dir / "ops-check.sh"
    doctor = bin_dir / "gt-doctor.sh"
    guard = bin_dir / "beads-path-guard.sh"
    gt_land = bin_dir / "gt-land.sh"

    _write_executable(ops, "#!/usr/bin/env bash\nset -euo pipefail\necho OPS_OK\n")
    _write_executable(
        doctor,
        f"""#!/usr/bin/env bash
set -euo pipefail
counter="{counter_file}"
if [[ ! -f "$counter" ]]; then
  echo 1 > "$counter"
  cat <<'JSON'
{{"checks":[{{"name":"single-beads-path","status":"fail"}},{{"name":"misclassified-wisps","status":"pass"}}]}}
JSON
  exit 0
fi
cat <<'JSON'
{{"checks":[{{"name":"single-beads-path","status":"pass"}},{{"name":"misclassified-wisps","status":"pass"}},{{"name":"runtime-file-isolation","status":"pass"}},{{"name":"embedded-git-clones","status":"pass"}}]}}
JSON
""",
    )
    _write_executable(
        guard,
        f"""#!/usr/bin/env bash
set -euo pipefail
echo called > "{guard_marker}"
""",
    )
    _write_executable(gt_land, "#!/usr/bin/env bash\nset -euo pipefail\necho LAND_OK\n")

    env = dict(os.environ)
    env["OPS_CHECK_CMD"] = str(ops)
    env["DOCTOR_CMD"] = str(doctor)
    env["PATH_GUARD_CMD"] = str(guard)
    env["GT_LAND_CMD"] = str(gt_land)

    result = subprocess.run(
        [str(SCRIPT_PATH), "--task-id", "hq-land-fix", "--fix", "--dry-run"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "applying auto-fix" in result.stdout
    assert guard_marker.exists()
    assert "[land] completed successfully." in result.stdout
