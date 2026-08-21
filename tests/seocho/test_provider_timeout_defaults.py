"""Reasoning presets need more than the 120s baseline request timeout.

`create_llm_backend` hardcoded `timeout=120.0` for every provider. Single-document
extraction on a reasoning model routinely runs longer than that, and the timeout
did not surface as an error — it raised inside the extraction call, which the
default enforcement profile catches and answers with the capitalized-token
heuristic. So the observable result of too short a timeout was ontology-free
graph structure, not a timeout message.

mara is this repo's default provider and serves MiniMax-class reasoning models,
so it is the preset that needed the headroom most and the one the original fix
predates.
"""

from __future__ import annotations

import pytest

from seocho.store.llm import create_llm_backend, get_provider_spec, list_provider_specs

REASONING_PRESETS = ("mara", "kimi", "grok")


@pytest.mark.parametrize("provider", REASONING_PRESETS)
def test_reasoning_presets_get_headroom(provider):
    assert get_provider_spec(provider).default_timeout > 120.0


def test_non_reasoning_presets_keep_the_baseline():
    """The headroom is targeted, not a blanket raise that hides real hangs."""
    for name, spec in list_provider_specs().items():
        if name not in REASONING_PRESETS:
            assert spec.default_timeout == 120.0, name


@pytest.mark.parametrize("provider", REASONING_PRESETS)
def test_backend_receives_the_preset_timeout(provider):
    backend = create_llm_backend(provider=provider, api_key="test-key",
                                 model="test-model")
    assert backend._timeout == get_provider_spec(provider).default_timeout


def test_explicit_timeout_still_wins():
    backend = create_llm_backend(provider="mara", api_key="test-key",
                                 model="test-model", timeout=5.0)
    assert backend._timeout == 5.0
