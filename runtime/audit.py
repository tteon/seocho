"""Append-only audit log of runtime access decisions.

Vendor-neutral and **opt-in**, mirroring the tracing contract: events are
written as JSON Lines to the path in ``SEOCHO_AUDIT_LOG_PATH``; when that env
var is unset (the default), :func:`record_access` is a no-op, so existing
deployments are unaffected.

Each event captures *who* (principal subject/role/authenticated), *what*
(action), *where* (workspace_id), the *decision* (allow/deny) and *reason*, and
the correlating ``request_id`` — the who-did-what trail enterprises require for
regulated data.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from runtime.identity import Principal, get_principal

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()


def audit_log_path() -> Optional[str]:
    """Return the configured audit log path, or None when auditing is disabled."""
    path = os.getenv("SEOCHO_AUDIT_LOG_PATH", "").strip()
    return path or None


def build_event(
    *,
    principal: Principal,
    action: str,
    workspace_id: str,
    allowed: bool,
    reason: str,
    request_id: str,
    ts: float,
) -> Dict[str, Any]:
    """Build the audit event record (pure — separated for testability)."""
    return {
        "ts": ts,
        "request_id": request_id,
        "subject": principal.subject,
        "role": principal.role,
        "authenticated": principal.authenticated,
        "workspace_id": workspace_id,
        "action": action,
        "decision": "allow" if allowed else "deny",
        "reason": reason,
    }


def record_access(
    *,
    action: str,
    workspace_id: str,
    allowed: bool,
    reason: str = "",
    principal: Optional[Principal] = None,
    request_id: Optional[str] = None,
    path: Optional[str] = None,
    now: Optional[float] = None,
) -> None:
    """Append one access-decision event to the audit log, if auditing is enabled.

    No-op when ``SEOCHO_AUDIT_LOG_PATH`` is unset (and no explicit ``path`` is
    given). A write failure is logged, never raised — auditing must not take
    down the request path (the caller still enforces the decision separately).
    """
    path = path if path is not None else audit_log_path()
    if not path:
        return
    principal = principal if principal is not None else get_principal()
    if request_id is None:
        try:
            from runtime.middleware import get_request_id

            request_id = get_request_id()
        except Exception:  # noqa: BLE001 — request_id is best-effort context
            request_id = ""
    event = build_event(
        principal=principal,
        action=action,
        workspace_id=workspace_id,
        allowed=allowed,
        reason=reason,
        request_id=request_id or "",
        ts=now if now is not None else time.time(),
    )
    line = json.dumps(event, separators=(",", ":"), sort_keys=True)
    try:
        with _write_lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError as exc:
        logger.warning("audit log write failed (%s): %s", path, exc)
