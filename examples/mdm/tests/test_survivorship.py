"""Unit tests for the MDM survivorship engine — pure Python, $0, no DB/API."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

MDM_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MDM_ROOT))

from lib.normalize import (  # noqa: E402
    names_match, norm_key, norm_tokens, parse_value, values_agree,
)
from lib.survivorship import (  # noqa: E402
    RuleVersionError, SourceFact, golden_id, load_ruleset,
    pick_canonical_name, survive_numeric, update_lock,
)


@pytest.fixture(scope="module")
def ruleset():
    # The repo's real config must load — guards yaml/lock drift in CI.
    return load_ruleset()


# ---------------------------------------------------------------------------
# Numeric normalization
# ---------------------------------------------------------------------------

def test_rounding_is_agreement():
    a = parse_value("$242.3B")
    b = parse_value("$242,290 million")
    assert a is not None and b is not None
    assert a.value == pytest.approx(242.3e9)
    assert b.value == pytest.approx(242.29e9)
    assert values_agree(a, b, rel_tol=0.005)


def test_real_discrepancy_is_not_agreement():
    a = parse_value("$242.3B")
    b = parse_value("$249B")
    assert not values_agree(a, b, rel_tol=0.005)


def test_parse_shapes():
    assert parse_value("242,290").value == 242290
    assert parse_value("(1,234) thousand").value == pytest.approx(-1.234e6)
    assert parse_value("-3.5%").is_pct and parse_value("-3.5%").value == pytest.approx(-3.5)
    assert parse_value("") is None
    assert parse_value("not disclosed") is None


def test_pct_never_equals_scalar():
    assert not values_agree(parse_value("5%"), parse_value("5"), rel_tol=0.5)


def test_sig_digits_as_written():
    assert parse_value("$242.3B").sig_digits == 4
    assert parse_value("$242,290 million").sig_digits == 6


# ---------------------------------------------------------------------------
# Name normalization / identity
# ---------------------------------------------------------------------------

def test_corp_suffix_blocking_key():
    assert norm_key("Costco Wholesale Corporation") == "costco wholesale"
    assert norm_key("Costco Wholesale Corp.") == "costco wholesale"
    assert norm_key("The Microsoft Company") == "microsoft"


def test_token_prefix_precision():
    assert names_match("Jacob", "Jacob Palme")
    assert names_match("Microsoft", "Microsoft Corporation")
    assert not names_match("Alan", "Friend of Alan")
    # Prefix is intentionally directional: short-form 'Delta' folds into the
    # fuller name, but a non-prefix substring never matches.
    assert names_match("Delta", "Delta Air Lines")
    assert not names_match("Air Lines", "Delta Air Lines")
    assert norm_tokens("Delta Air Lines") == ["delta", "air", "lines"]


def test_canonical_name_pick():
    names = ["Microsoft", "Microsoft Corp.", "Microsoft Corporation"]
    assert pick_canonical_name(names) == "Microsoft Corporation"


# ---------------------------------------------------------------------------
# Attribute survivorship
# ---------------------------------------------------------------------------

def _f(source, raw):
    return SourceFact(source=source, raw=raw)


def test_majority_2_of_3(ruleset):
    out = survive_numeric(
        [_f("risk/deepseek", "$242.3B"),
         _f("research/gptoss", "$242,290 million"),
         _f("compliance/minimax", "$249B")],
        panel_size=3, ruleset=ruleset)
    assert out.status == "golden" and out.rule == "majority"
    # Least-rounded member of the winning group survives.
    assert out.value_raw == "$242,290 million" and out.source == "research/gptoss"
    assert out.agreement_count == 2 and out.sources_reporting == 3
    assert out.confidence == pytest.approx(2 / 3, abs=1e-3)
    assert [d["source"] for d in out.dissents] == ["compliance/minimax"]


def test_three_way_split_quarantines(ruleset):
    out = survive_numeric(
        [_f("risk/deepseek", "$100M"),
         _f("research/gptoss", "$200M"),
         _f("compliance/minimax", "$300M")],
        panel_size=3, ruleset=ruleset)
    assert out.status == "quarantine" and out.rule == "tied_groups"
    assert out.value is None and len(out.dissents) == 3


def test_one_vs_one_quarantines(ruleset):
    out = survive_numeric(
        [_f("risk/deepseek", "$100M"), _f("research/gptoss", "$200M")],
        panel_size=3, ruleset=ruleset)
    assert out.status == "quarantine"
    assert out.sources_reporting == 2 and out.panel_size == 3


def test_missing_is_not_a_vote(ruleset):
    # Third source missing entirely: 2 reporters agree → golden at 2/3 panel.
    out = survive_numeric(
        [_f("risk/deepseek", "$242.3B"), _f("research/gptoss", "$242,290 million")],
        panel_size=3, ruleset=ruleset)
    assert out.status == "golden" and out.agreement_count == 2
    assert out.sources_reporting == 2 and out.confidence == pytest.approx(2 / 3, abs=1e-3)


def test_single_source_survives_low_confidence(ruleset):
    out = survive_numeric([_f("risk/deepseek", "$1.5B")], panel_size=3, ruleset=ruleset)
    assert out.status == "golden" and out.rule == "single_source"
    assert out.confidence == pytest.approx(1 / 3, abs=1e-3)


def test_unparseable_only_quarantines(ruleset):
    out = survive_numeric([_f("risk/deepseek", "not disclosed")],
                          panel_size=3, ruleset=ruleset)
    assert out.status == "quarantine" and out.rule == "all_values_unparseable"


def test_empty_when_nothing_reported(ruleset):
    out = survive_numeric([], panel_size=3, ruleset=ruleset)
    assert out.status == "empty"


def test_deterministic(ruleset):
    facts = [_f("a", "$10M"), _f("b", "$10.0M"), _f("c", "$99M")]
    first = survive_numeric(facts, panel_size=3, ruleset=ruleset)
    for _ in range(5):
        again = survive_numeric(list(reversed(facts)), panel_size=3, ruleset=ruleset)
        assert (again.value, again.value_raw, again.source) == (
            first.value, first.value_raw, first.source)


# ---------------------------------------------------------------------------
# Golden id + ruleset lock
# ---------------------------------------------------------------------------

def test_golden_id_deterministic_and_order_free():
    a = golden_id("1.0.0", ["db1:e1", "db2:e7"])
    b = golden_id("1.0.0", ["db2:e7", "db1:e1"])
    assert a == b and len(a) == 24
    assert golden_id("1.0.1", ["db1:e1", "db2:e7"]) != a


def test_repo_ruleset_loads(ruleset):
    assert ruleset.version == "1.1.2"
    assert ruleset.rel_tol == 0.005
    assert ruleset.seed == 42
    # v1.1.0: identity threshold = the deduplicator's 0.92, not the linker's
    # relatedness 0.72 (which merged distinct airlines).
    assert ruleset.embedding_threshold == 0.92
    assert "consolidated" in ruleset.exclude_norm_names
    assert "december" in ruleset.exclude_norm_names


def test_edited_yaml_without_version_bump_fails(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    src = MDM_ROOT / "config" / "survivorship.yaml"
    (cfg_dir / "survivorship.yaml").write_text(src.read_text())
    update_lock(cfg_dir)
    load_ruleset(cfg_dir)  # sanity: freshly locked config loads

    # Tamper with a threshold without bumping the version → must fail.
    tampered = src.read_text().replace(
        "equivalence_tolerance_rel: 0.005", "equivalence_tolerance_rel: 0.05")
    assert tampered != src.read_text()
    (cfg_dir / "survivorship.yaml").write_text(tampered)
    with pytest.raises(RuleVersionError):
        load_ruleset(cfg_dir)


def test_lock_file_tracks_versions(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    src = (MDM_ROOT / "config" / "survivorship.yaml").read_text()
    (cfg_dir / "survivorship.yaml").write_text(src)
    update_lock(cfg_dir)
    bumped = src.replace('rule_set_version: "1.1.2"', 'rule_set_version: "1.2.0"')
    assert bumped != src
    (cfg_dir / "survivorship.yaml").write_text(bumped)
    update_lock(cfg_dir)
    with open(cfg_dir / "survivorship.lock.json", "r", encoding="utf-8") as f:
        lock = json.load(f)
    assert set(lock) == {"1.1.2", "1.2.0"}
    assert load_ruleset(cfg_dir).version == "1.2.0"
