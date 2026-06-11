"""Deterministic survivorship engine — the MDM demo's versioned "MATCH_RULE table".

Classical survivorship (most-recent / most-trusted-source) is meaningless here:
all three departments read the SAME 10-K at the same time, and an a-priori
trust ranking of LLMs would be exactly the silent preference CLAUDE.md §20
forbids. The rule family is therefore **majority/consensus with abstention**:

- ≥2 sources agree (within relative tolerance) → golden value = the
  least-rounded member of the winning group; dissents retained.
- no majority (1-vs-1, three-way split, tied groups) → **QUARANTINE**: no
  golden value is written, a steward task is created. No silent pick (§20.2).
- missing is NOT a vote against (majority over sources that reported).
- single reporter → golden with low confidence (policy-configurable).

Reproducibility (§20.7): rules live in ``config/survivorship.yaml`` whose
sha256 is pinned per version in ``survivorship.lock.json``. Editing the yaml
without bumping ``rule_set_version`` (and re-locking) fails the run — the
graph equivalent of a versioned MATCH_RULE table. Every golden record is
stamped with ``rule_set_version`` + ``rule_set_sha256``.

Run ``python lib/survivorship.py --update-lock`` after a deliberate version bump.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml

from .normalize import ParsedValue, norm_tokens, parse_value, values_agree

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
RULESET_FILE = "survivorship.yaml"
LOCK_FILE = "survivorship.lock.json"


class RuleVersionError(RuntimeError):
    """survivorship.yaml changed without a version bump + re-lock."""


@dataclass(frozen=True)
class Ruleset:
    cfg: dict
    version: str
    sha256: str

    @property
    def rel_tol(self) -> float:
        return float(self.cfg["attributes"]["numeric"]["equivalence_tolerance_rel"])

    @property
    def single_source_policy(self) -> str:
        return str(self.cfg["attributes"]["numeric"]["single_source_policy"])

    @property
    def embedding_threshold(self) -> float:
        return float(self.cfg["identity"]["embedding"]["threshold"])

    @property
    def exclude_norm_names(self) -> frozenset:
        """Generic-value suppression list (classical MDM): normalized names
        that must never become master entities (10-K boilerplate)."""
        return frozenset(self.cfg["identity"].get("exclude_norm_names") or [])

    @property
    def embedding_model(self) -> str:
        return str(self.cfg["identity"]["embedding"]["model"])

    @property
    def seed(self) -> int:
        return int(self.cfg["seed"])


def load_ruleset(config_dir: Path = CONFIG_DIR) -> Ruleset:
    """Load + verify the ruleset against its lock. Raises on drift."""
    text = (config_dir / RULESET_FILE).read_text(encoding="utf-8")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cfg = yaml.safe_load(text)
    version = str(cfg["rule_set_version"])
    lock_path = config_dir / LOCK_FILE
    if not lock_path.is_file():
        raise RuleVersionError(
            f"missing {LOCK_FILE}; run `python lib/survivorship.py --update-lock`")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get(version) != sha:
        raise RuleVersionError(
            f"survivorship.yaml (sha {sha[:12]}…) does not match the locked sha for "
            f"version {version!r} — bump rule_set_version and re-lock; never edit "
            f"rules in place (§20.7 reproducibility)")
    return Ruleset(cfg=cfg, version=version, sha256=sha)


def update_lock(config_dir: Path = CONFIG_DIR) -> str:
    text = (config_dir / RULESET_FILE).read_text(encoding="utf-8")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    version = str(yaml.safe_load(text)["rule_set_version"])
    lock_path = config_dir / LOCK_FILE
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.is_file() else {}
    lock[version] = sha
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sha


# ---------------------------------------------------------------------------
# Identity survivorship
# ---------------------------------------------------------------------------

def pick_canonical_name(names: Sequence[str]) -> str:
    """most tokens → longest string → lexicographic (fully deterministic)."""
    if not names:
        raise ValueError("pick_canonical_name: empty candidate list")
    return max(names, key=lambda n: (len(norm_tokens(n, strip_corp=False)), len(n), n))


def golden_id(rule_set_version: str, source_keys: Sequence[str]) -> str:
    """Deterministic golden-record id: same sources + same rules ⇒ same id."""
    blob = rule_set_version + "\n" + "\n".join(sorted(source_keys))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Attribute survivorship (numeric)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceFact:
    """One department's extracted figure for an (entity, metric, period, basis)."""

    source: str   # e.g. "risk/DeepSeek-V3.1"
    raw: str      # value string exactly as extracted


@dataclass
class Survived:
    status: str                      # "golden" | "quarantine" | "empty"
    rule: str                        # winning rule name (or quarantine reason)
    value: Optional[float] = None    # normalized base-unit value (golden only)
    value_raw: Optional[str] = None  # survivor's raw string (figure as written)
    source: Optional[str] = None     # survivor's source
    agreement_count: int = 0
    sources_reporting: int = 0
    panel_size: int = 0
    confidence: float = 0.0          # agreement_count / panel_size
    dissents: List[Dict] = field(default_factory=list)


def survive_numeric(
    facts: Sequence[SourceFact],
    *,
    panel_size: int,
    ruleset: Ruleset,
) -> Survived:
    """Majority-with-abstention vote over one attribute's reported values."""
    reporting = [f for f in facts if str(f.raw or "").strip()]
    if not reporting:
        return Survived(status="empty", rule="no_source_reported",
                        panel_size=panel_size)

    parsed: List[tuple[SourceFact, Optional[ParsedValue]]] = [
        (f, parse_value(f.raw)) for f in reporting
    ]
    votes = [(f, p) for f, p in parsed if p is not None]
    unparseable = [{"source": f.source, "raw": f.raw, "note": "unparseable"}
                   for f, p in parsed if p is None]

    base = Survived(status="quarantine", rule="", panel_size=panel_size,
                    sources_reporting=len(reporting), dissents=list(unparseable))

    if not votes:
        base.rule = "all_values_unparseable"
        return base

    # Union-find agreement groups under relative tolerance.
    parent = list(range(len(votes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(votes)):
        for j in range(i + 1, len(votes)):
            if values_agree(votes[i][1], votes[j][1], rel_tol=ruleset.rel_tol):
                parent[find(i)] = find(j)

    groups: Dict[int, List[int]] = {}
    for i in range(len(votes)):
        groups.setdefault(find(i), []).append(i)
    sizes = sorted((len(g) for g in groups.values()), reverse=True)
    best_size = sizes[0]
    tied = sizes.count(best_size) > 1
    # Deterministic winner among same-size groups doesn't matter when tied —
    # ties quarantine — so pick any best group for reporting.
    best = max(groups.values(), key=len)

    def _dissents(winning: List[int]) -> List[Dict]:
        win = set(winning)
        return list(base.dissents) + [
            {"source": votes[k][0].source, "raw": votes[k][0].raw,
             "value": votes[k][1].value}
            for k in range(len(votes)) if k not in win
        ]

    # Single reporter: no corroboration possible — policy decides.
    if len(votes) == 1:
        f, p = votes[0]
        if ruleset.single_source_policy == "survive_low_confidence":
            return Survived(status="golden", rule="single_source", value=p.value,
                            value_raw=f.raw, source=f.source, agreement_count=1,
                            sources_reporting=len(reporting), panel_size=panel_size,
                            confidence=round(1.0 / panel_size, 3),
                            dissents=list(base.dissents))
        base.rule = "single_source_quarantined"
        base.dissents = _dissents([])
        return base

    # Majority: strictly more than half the votes, ≥2, and not tied.
    if not tied and best_size >= 2 and best_size * 2 > len(votes):
        # Survivor = least-rounded member (most significant digits as written);
        # tie → lexicographic (raw, source) for determinism.
        k = max(best, key=lambda i: (votes[i][1].sig_digits,
                                     votes[i][0].raw, votes[i][0].source))
        f, p = votes[k]
        return Survived(status="golden", rule="majority", value=p.value,
                        value_raw=f.raw, source=f.source, agreement_count=best_size,
                        sources_reporting=len(reporting), panel_size=panel_size,
                        confidence=round(best_size / panel_size, 3),
                        dissents=_dissents(best))

    base.rule = "tied_groups" if tied else "no_majority"
    base.agreement_count = best_size
    base.confidence = 0.0
    base.dissents = _dissents([])
    return base


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Survivorship ruleset utilities")
    ap.add_argument("--update-lock", action="store_true",
                    help="re-pin survivorship.lock.json after a DELIBERATE version bump")
    args = ap.parse_args()
    if args.update_lock:
        sha = update_lock()
        print(f"locked {RULESET_FILE} → {sha[:12]}…")
    else:
        rs = load_ruleset()
        print(f"ruleset v{rs.version} OK (sha {rs.sha256[:12]}…, rel_tol={rs.rel_tol})")
