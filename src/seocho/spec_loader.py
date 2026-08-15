"""Shared machinery for declarative YAML specs.

``run_spec``, ``agent_design``, ``indexing_design`` and ``curation_design``
each grew their own load/validate code, and none of them versioned their
format — any evolution was a hard break with no migration signal. This module
is the single home for the generic pieces (error-list exception, env
interpolation, unknown-key suggestions, ``schema_version`` checking) so the
next spec — ``seocho-policy.yaml`` in particular — starts versioned and
consistent instead of copying one of four ancestors.
"""

from __future__ import annotations

import difflib
import os
import re
from typing import Any, List, Mapping

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

SCHEMA_VERSION_KEY = "schema_version"


class SpecError(ValueError):
    """A spec failed to parse or validate.

    ``errors`` keeps individual messages so callers (the CLI) can print one
    error per line instead of a single concatenated wall.
    """

    def __init__(self, errors: List[str]) -> None:
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


def interpolate_env(value: Any, *, errors: List[str], where: str) -> Any:
    """Resolve ``${VAR}`` / ``${VAR:-default}`` in string values, recursively."""
    if isinstance(value, str):
        def _resolve(match: "re.Match[str]") -> str:
            name, default = match.group(1), match.group(2)
            resolved = os.environ.get(name)
            if resolved is not None:
                return resolved
            if default is not None:
                return default
            errors.append(
                f"at {where}: environment variable {name} is not set. "
                f"Export it or use ${{{name}:-fallback}}."
            )
            return ""
        return _ENV_PATTERN.sub(_resolve, value)
    if isinstance(value, dict):
        return {
            key: interpolate_env(item, errors=errors, where=f"{where}.{key}" if where else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            interpolate_env(item, errors=errors, where=f"{where}[{idx}]")
            for idx, item in enumerate(value)
        ]
    return value


def suggest(key: str, allowed: set) -> str:
    matches = difflib.get_close_matches(key, sorted(allowed), n=1)
    return f" Did you mean '{matches[0]}'?" if matches else ""


def check_unknown_keys(
    payload: Mapping[str, Any],
    *,
    allowed: set,
    where: str,
    errors: List[str],
) -> None:
    for key in payload:
        if key not in allowed:
            errors.append(f"at {where}: unknown key '{key}'.{suggest(str(key), allowed)}")


def check_schema_version(
    payload: Mapping[str, Any],
    *,
    supported: tuple = (1,),
    where: str,
    errors: List[str],
) -> int:
    """Validate an optional ``schema_version`` key; absent means the oldest.

    Absence is deliberate back-compat: every spec written before versioning
    existed is implicitly version ``supported[0]``. A present-but-unsupported
    version is the migration signal the old format could never give — the
    reader is too old (or too new) for the file, and says so instead of
    misparsing it.
    """
    if SCHEMA_VERSION_KEY not in payload:
        return supported[0]
    raw = payload[SCHEMA_VERSION_KEY]
    try:
        version = int(raw)
    except (TypeError, ValueError):
        errors.append(
            f"at {where}: {SCHEMA_VERSION_KEY} must be an integer, got {raw!r}."
        )
        return supported[0]
    if version not in supported:
        errors.append(
            f"at {where}: {SCHEMA_VERSION_KEY} {version} is not supported by this "
            f"seocho (supported: {', '.join(str(v) for v in supported)}). "
            f"Upgrade seocho or convert the spec."
        )
    return version


__all__ = [
    "SCHEMA_VERSION_KEY",
    "SpecError",
    "check_schema_version",
    "check_unknown_keys",
    "interpolate_env",
    "suggest",
]
