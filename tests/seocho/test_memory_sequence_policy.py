import pytest

from seocho.memory import (
    CausalFrontier,
    CausalPosition,
    SequenceMode,
    SequencePolicy,
)


def test_shard_routing_is_stable_and_bounded() -> None:
    policy = SequencePolicy(mode=SequenceMode.SHARDED_DOMAIN, shards=16, lease_size=128)

    first = policy.shard_for("wallet:alice")

    assert first == policy.shard_for("wallet:alice")
    assert 0 <= first < 16


def test_strict_policy_cannot_silently_enable_leasing_or_sharding() -> None:
    with pytest.raises(ValueError, match="strict_workspace"):
        SequencePolicy(mode=SequenceMode.STRICT_WORKSPACE, shards=4)
    with pytest.raises(ValueError, match="strict_workspace"):
        SequencePolicy(mode=SequenceMode.STRICT_WORKSPACE, lease_size=2)


def test_frontier_requires_every_referenced_shard_watermark() -> None:
    frontier = CausalFrontier.for_workspace(
        "workspace-1",
        CausalPosition("transaction", 2, 14),
        CausalPosition("policy", 0, 7),
    )

    assert frontier.satisfied_by({("policy", 0): 7, ("transaction", 2): 14})
    assert not frontier.satisfied_by({("policy", 0): 7})
    assert not frontier.satisfied_by({("policy", 0): 7, ("transaction", 2): 13})


def test_frontier_merge_keeps_maximum_position_per_shard() -> None:
    left = CausalFrontier.for_workspace(
        "workspace-1", CausalPosition("transaction", 1, 8)
    )
    right = CausalFrontier.for_workspace(
        "workspace-1",
        CausalPosition("transaction", 1, 10),
        CausalPosition("transaction", 3, 4),
    )

    merged = left.merge(right)

    assert merged.positions == (
        CausalPosition("transaction", 1, 10),
        CausalPosition("transaction", 3, 4),
    )
    assert merged.serialize().startswith("memory.v2:")


def test_frontier_rejects_duplicate_positions_and_cross_workspace_merge() -> None:
    position = CausalPosition("transaction", 1, 8)
    with pytest.raises(ValueError, match="one position"):
        CausalFrontier.for_workspace("workspace-1", position, position)

    other = CausalFrontier.for_workspace("workspace-2", position)
    with pytest.raises(ValueError, match="different workspaces"):
        CausalFrontier.for_workspace("workspace-1", position).merge(other)
