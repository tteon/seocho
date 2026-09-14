"""Bounded isolated vLLM process ownership for prototype comparisons."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import threading
import time
from typing import Iterator

import httpx

from .inference_probe import serving_plan


@contextmanager
def serve_arm(
    *,
    target: str,
    draft: str,
    revision: str,
    arm: str,
    out: Path,
    port: int = 8000,
    max_seconds: int = 900,
    startup_seconds: int = 600,
    max_model_len: int = 8192,
) -> Iterator[str]:
    """Launch one owned process group and stop it on success, failure or deadline.

    No provider rental, downloads outside vLLM, or changes to other servers.
    Run this within an already provisioned GPU instance. The deadline also
    covers compilation/warmup; repeat arms in randomized order externally.
    """
    if (
        not 0 < startup_seconds < max_seconds
        or not 1 <= port <= 65535
        or max_model_len < 1
    ):
        raise ValueError("Invalid bounded serving configuration")
    plan = serving_plan(target, draft, revision)
    selected = next((a for a in plan["arms"] if a["name"] == arm), None)
    if selected is None:
        raise ValueError("Unknown factorial arm")
    executable = shutil.which("vllm")
    if not executable:
        raise RuntimeError("vLLM executable is unavailable in this environment")
    with socket.socket() as port_check:
        port_check.bind(("127.0.0.1", port))
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    argv = [
        executable,
        *selected["argv"][1:],
        "--port",
        str(port),
        "--max-model-len",
        str(max_model_len),
    ]
    receipt = {
        "arm": selected,
        "argv": argv,
        "max_seconds": max_seconds,
        "started_unix_s": time.time(),
        "state": "starting",
        "owned_process_stopped": False,
    }
    receipt_path = out / "serving.json"

    def save() -> None:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    save()
    with (out / "server.log").open("w") as log:
        process = subprocess.Popen(
            argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        receipt["pid"] = process.pid

        def stop_group() -> None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        def kill_group() -> None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        timer = threading.Timer(max_seconds, stop_group)
        killer = threading.Timer(max_seconds + 10, kill_group)
        timer.daemon = killer.daemon = True
        timer.start()
        killer.start()
        base_url = f"http://127.0.0.1:{port}/v1"
        try:
            deadline = time.monotonic() + startup_seconds
            with httpx.Client(timeout=3) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "vLLM exited before readiness; inspect private server.log"
                        )
                    try:
                        response = client.get(base_url + "/models")
                        if response.is_success and any(
                            m.get("id") == target
                            for m in response.json().get("data", [])
                        ):
                            break
                    except (httpx.HTTPError, ValueError):
                        pass
                    time.sleep(1)
                else:
                    raise TimeoutError("vLLM readiness deadline exceeded")
            receipt["state"] = "ready"
            save()
            yield base_url
            receipt["state"] = "probe_returned"
        except BaseException as exc:
            receipt.update(state="failed", error_class=type(exc).__name__)
            raise
        finally:
            stop_group()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                kill_group()
                process.wait(timeout=10)
            # A parent may have exited while GPU worker children remain.
            kill_group()
            timer.cancel()
            killer.cancel()
            receipt.update(
                owned_process_stopped=process.poll() is not None,
                exit_code=process.returncode,
                finished_unix_s=time.time(),
            )
            save()
