"""PackStream codec golden-parity anchor (ADR-0111, §21.1(5)).

Whichever codec is installed (pure-python or neo4j-rust-ext), packing and
unpacking the golden value set must round-trip to IDENTICAL Python values.
This is the CI tripwire for the rust-ext adoption: a codec that changes any
value representation fails here before it can corrupt a graph read.

No DB, no network — the codec is exercised in memory.
"""
from __future__ import annotations

import logging

import pytest

neo4j = pytest.importorskip("neo4j")

from neo4j._codec.packstream import RUST_AVAILABLE  # noqa: E402
from neo4j._codec.packstream.v1 import (  # noqa: E402
    PackableBuffer, Packer, UnpackableBuffer, Unpacker,
)

# The golden set covers every PackStream scalar/collection family the
# federation read path actually carries (names, property maps, elementIds,
# figure strings, nested lists/maps, unicode, int-width boundaries, floats).
GOLDEN = [
    None, True, False,
    0, 1, -1, 127, 128, -128, -129, 32767, 2**31 - 1, -(2**31), 2**63 - 1,
    0.0, 1.5, -2.25, 1e308,
    "", "Pfizer Inc.", "엔티티", "$242,290 million", "4:abc:42",
    b"\x00\x01\xff",
    [1, "two", 3.0, None, [True]],
    {"name": "Enphase Energy, Inc.", "value": "$46,273", "period": "FY2023",
     "nested": {"models": ["DeepSeek-V3.1", "gpt-oss-120b"], "n": 2}},
]


def _roundtrip(value):
    # Plain scalar/collection values need no graph-type hydration hooks —
    # this exercises exactly the codec layer rust-ext replaces.
    out = PackableBuffer()
    Packer(out).pack(value)
    return Unpacker(UnpackableBuffer(bytes(out.data))).unpack()


def test_codec_liveness_is_reportable():
    # The §21.2 liveness flag must exist and be a bool — the store's startup
    # log and the A/B bench both depend on it.
    assert isinstance(RUST_AVAILABLE, bool)


def test_golden_roundtrip_parity():
    for value in GOLDEN:
        got = _roundtrip(value)
        assert got == value, f"codec changed value: {value!r} -> {got!r} " \
                             f"(RUST_AVAILABLE={RUST_AVAILABLE})"
        # Int must stay int, float stay float (no silent widening).
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        assert type(got) is type(value), f"type drift: {value!r} -> {type(got)}"


def test_store_logs_codec_once(caplog):
    from seocho.store import graph as graph_mod

    graph_mod._packstream_codec_logged = False
    with caplog.at_level(logging.INFO, logger="seocho.store.graph"):
        graph_mod._log_packstream_codec_once()
        graph_mod._log_packstream_codec_once()   # second call must be silent
    msgs = [r.message for r in caplog.records if "packstream codec" in r.message]
    assert len(msgs) == 1
    assert ("rust-ext" in msgs[0]) == RUST_AVAILABLE
