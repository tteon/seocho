"""The row encoding a graph tool sends into the model's context.

`encode_rows` is pure and SDK-free, so most of this file runs without openai-agents
installed; the one test that touches `make_graph_tool` skips itself when the SDK is absent,
matching the module's own optional-dependency stance.

What is being protected: the two encodings must carry the same four facts — rows, count,
cap, truncation — because the CSV option was measured (AIsummit26, 117 episodes per arm) to
be safe only as an *encoding* change. A CSV that dropped the truncation trailer would
silently reopen the failure the `truncated` field exists to close (71 of 117 episodes
answered wrongly off a cut view without saying so when the signal was withheld).
"""
from __future__ import annotations

import json

import pytest

from seocho.integrations.openai_agents import ROW_FORMATS, encode_rows

ROWS = [
    {"acct_no": 4881, "amount": 152000.5, "channel_risk": 0.82},
    {"acct_no": 1207, "amount": None, "channel_risk": 0.31},
]


def test_json_is_the_historical_payload():
    doc = json.loads(encode_rows(ROWS, row_cap=50, truncated=True, row_format="json"))
    assert doc == {"rows": ROWS, "row_count": 2, "truncated": True, "row_cap": 50}


def test_csv_carries_the_same_four_facts():
    text = encode_rows(ROWS, row_cap=50, truncated=True, row_format="csv")
    lines = text.splitlines()
    assert lines[0] == "acct_no,amount,channel_risk"          # keys paid for once
    assert lines[1] == "4881,152000.5,0.82"
    assert lines[2] == "1207,,0.31"                           # None -> empty cell
    assert lines[3] == "# row_count=2 row_cap=50 truncated=true"


def test_csv_trailer_survives_an_empty_page():
    # The empty page is how a paging agent learns it has reached the end; the trailer must
    # still be there to say so.
    text = encode_rows([], row_cap=50, truncated=False, row_format="csv")
    assert text == "# row_count=0 row_cap=50 truncated=false"


def test_default_format_is_json():
    assert json.loads(encode_rows([], row_cap=1, truncated=False))["rows"] == []


def test_unknown_format_is_refused_by_name():
    with pytest.raises(ValueError, match="row_format"):
        encode_rows(ROWS, row_cap=50, truncated=False, row_format="yaml")
    assert ROW_FORMATS == ("json", "csv")


def test_make_graph_tool_validates_row_format_before_touching_the_store():
    pytest.importorskip("agents")
    from seocho.integrations.openai_agents import make_graph_tool

    with pytest.raises(ValueError, match="row_format"):
        make_graph_tool(object(), database="db", row_format="parquet")
