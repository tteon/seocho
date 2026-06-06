"""Deterministic per-instance runtime layout derivation.

Worktree-isolated runtime boot (seocho-6q9.3) follows the SEOCHO
"single Neo4j instance, multi-database" model: a *shared* graph backend is
reached by every worktree, while each worktree drives its own ephemeral
logical database plus its own app-tier containers on offset ports.

This module is the canonical, side-effect-free derivation: an instance id maps
deterministically to a compose project name, an ephemeral database name (which
is validated against the same ``DATABASE_NAME_PATTERN`` the runtime enforces),
and an app-tier port slot. It performs no I/O so it is trivially unit-testable
and reproducible across processes (``hashlib`` rather than the salted builtin
``hash``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict

from .exceptions import SeochoError
from .runtime_contract import DATABASE_NAME_PATTERN

# App-tier port bands. The shared neo4j keeps the default 7474/7687; only the
# per-instance app containers are offset. Bands sit clear of the monolithic
# stack's 8001/8501/8888 defaults AND the opik profile ports (8080/8000/8123/
# 9000/9001/9090/...). 200 contiguous slots keep collisions rare at realistic
# worktree counts: P(>=1 collision) ~1% at 2 tenants, ~14% at 8 (vs ~5% / ~50%
# at the original 40 slots — see scripts/experiments/isolation_experiment.py).
# Residual collisions are detectable via InstanceLayout.collides_with(...).
INSTANCE_API_PORT_BASE = 8800
INSTANCE_UI_PORT_BASE = 9100
INSTANCE_PORT_STEP = 1
INSTANCE_PORT_SLOTS = 200

_DATABASE_NAME_RE = re.compile(DATABASE_NAME_PATTERN)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _digest(instance_id: str) -> int:
    """Stable, process-independent integer digest of an instance id."""
    return int(hashlib.sha1(instance_id.encode("utf-8")).hexdigest(), 16)


def _slug(instance_id: str) -> str:
    slug = _SLUG_RE.sub("-", instance_id.strip().lower()).strip("-")
    return slug


@dataclass(frozen=True, slots=True)
class InstanceLayout:
    """Resolved, collision-checkable layout for one worktree instance."""

    instance_id: str
    slug: str
    project_name: str
    database: str
    api_port: int
    ui_port: int
    slot: int

    def env_overrides(self) -> Dict[str, str]:
        """Environment overrides applied to the per-instance app compose run.

        The shared neo4j is reached over its published bolt port, so app
        containers in a separate compose project resolve it via the host
        gateway rather than compose-internal DNS.
        """
        return {
            "COMPOSE_PROJECT_NAME": self.project_name,
            "EXTRACTION_API_PORT": str(self.api_port),
            "CHAT_INTERFACE_PORT": str(self.ui_port),
            "SEOCHO_DATABASE": self.database,
            "NEO4J_DATABASE": self.database,
        }

    def collides_with(self, other: "InstanceLayout") -> bool:
        """True if two layouts would contend for a port or logical database."""
        if self.instance_id == other.instance_id:
            return False
        return (
            self.api_port == other.api_port
            or self.ui_port == other.ui_port
            or self.database == other.database
            or self.project_name == other.project_name
        )


def derive_instance(instance_id: str) -> InstanceLayout:
    """Derive the deterministic runtime layout for ``instance_id``.

    Raises ``SeochoError`` for an empty id or if the derived database name does
    not satisfy the runtime database-name contract (a defensive check; the
    derivation is constructed to always satisfy it).
    """
    if not instance_id or not instance_id.strip():
        raise SeochoError("instance id must be a non-empty string")

    slug = _slug(instance_id)
    if not slug:
        raise SeochoError(
            f"instance id {instance_id!r} has no [a-z0-9] characters to derive a layout from"
        )

    digest = _digest(instance_id)
    slot = digest % INSTANCE_PORT_SLOTS
    # Database name must match ^[a-z][a-z0-9]{2,62}$ — derive from the hex
    # digest (which is already [0-9a-f]) under a leading letter prefix.
    database = "wt" + hashlib.sha1(instance_id.encode("utf-8")).hexdigest()[:12]
    project_name = f"seocho-{slug}"

    layout = InstanceLayout(
        instance_id=instance_id,
        slug=slug,
        project_name=project_name,
        database=database,
        api_port=INSTANCE_API_PORT_BASE + slot * INSTANCE_PORT_STEP,
        ui_port=INSTANCE_UI_PORT_BASE + slot * INSTANCE_PORT_STEP,
        slot=slot,
    )

    if not _DATABASE_NAME_RE.match(layout.database):
        raise SeochoError(
            f"derived database name {layout.database!r} violates the runtime "
            f"contract {DATABASE_NAME_PATTERN!r}"
        )
    return layout
