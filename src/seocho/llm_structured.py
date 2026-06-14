"""Provider/model-aware structured-output layer (seocho-ub5).

Empirically motivated: across the FinDER / OntoClean MARA runs, DeepSeek-V3.1
returned clean JSON, but MiniMax-M2.x and gpt-oss-120b emit chain-of-thought and
broke naive JSON parsing (~16-20% failures on MiniMax-M2.7), and gpt-oss returned
empty output at a low ``max_tokens``. A single prompt + ``response_format`` is not
portable across providers.

This layer sits ABOVE the backend (``store/llm.py``, which already translates
``response_format`` into vLLM guided decoding per ADR-0098). For a target model it:

  1. looks up a capability profile (reasoning preamble? json_object? guided JSON?
     max_tokens floor? temperature clamp?),
  2. chooses the best structured-output strategy the model supports
     (json_schema/guided → json_object → prompt-injected),
  3. raises the ``max_tokens`` floor and applies any temperature clamp,
  4. appends a "emit ONLY the final JSON" instruction for reasoning models,
  5. parses the result robustly (strips ``<think>`` blocks / code fences, then
     picks the largest balanced JSON object).

It is offline/data-plane and composes with any object exposing the SEOCHO
``LLMBackend.complete(system=, user=, temperature=, max_tokens=, response_format=,
task_hint=)`` contract; the result may expose ``.text`` and/or ``.json()``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


class StructuredOutputError(ValueError):
    """Raised when no JSON object can be recovered from a model response."""


@dataclass(frozen=True)
class ModelCapability:
    family: str
    emits_reasoning: bool = False        # prepends chain-of-thought before the answer
    supports_json_object: bool = True    # response_format={"type":"json_object"}
    supports_guided_json: bool = False   # json_schema / guided decoding
    max_tokens_floor: int = 2048         # reasoning models need headroom
    temperature_clamp: Optional[float] = None  # force this exact temperature if set


# Ordered, first-match-wins. Keyed by MODEL name (one provider serves many models:
# MARA serves DeepSeek-V3.1, MiniMax-M2.x, gpt-oss-120b). Conservative on
# guided_json for OpenAI-compatible cloud models we haven't verified (MARA): rely
# on json_object + robust parse, which is proven to work for them.
_REGISTRY: list[Tuple[re.Pattern, ModelCapability]] = [
    (re.compile(r"minimax|m2\.\d", re.I),
     ModelCapability("minimax", emits_reasoning=True, supports_json_object=True, max_tokens_floor=4096)),
    (re.compile(r"gpt-?oss", re.I),
     ModelCapability("gpt-oss", emits_reasoning=True, supports_json_object=True, max_tokens_floor=4096)),
    (re.compile(r"deepseek.*(r1|reason)|deepseek-r", re.I),
     ModelCapability("deepseek-reasoner", emits_reasoning=True, supports_json_object=True, max_tokens_floor=4096)),
    (re.compile(r"deepseek", re.I),
     ModelCapability("deepseek", emits_reasoning=False, supports_json_object=True, max_tokens_floor=1024)),
    (re.compile(r"kimi|moonshot", re.I),
     ModelCapability("kimi", emits_reasoning=False, supports_json_object=True, max_tokens_floor=1024, temperature_clamp=1.0)),
    (re.compile(r"gpt-4|gpt-3|gpt-5|^o[134]|openai", re.I),
     ModelCapability("openai", emits_reasoning=False, supports_json_object=True, supports_guided_json=True, max_tokens_floor=1024)),
    (re.compile(r"qwen", re.I),
     ModelCapability("qwen", emits_reasoning=False, supports_json_object=True, max_tokens_floor=1024)),
    (re.compile(r"grok", re.I),
     ModelCapability("grok", emits_reasoning=True, supports_json_object=True, max_tokens_floor=2048)),
    (re.compile(r"vllm", re.I),
     ModelCapability("vllm", emits_reasoning=False, supports_json_object=True, supports_guided_json=True, max_tokens_floor=1024)),
]

_DEFAULT = ModelCapability("unknown", emits_reasoning=False, supports_json_object=True, max_tokens_floor=2048)

_REASONING_SUFFIX = (
    "\n\nIMPORTANT: after any reasoning, output ONLY the final JSON object and "
    "nothing else — no explanation, no markdown fences."
)


def capability_for(model: Optional[str]) -> ModelCapability:
    """Resolve a model name to its capability profile (first registry match).
    Unknown models that *look* like reasoning models (``-r1``, ``think``,
    ``reason``) get a reasoning profile; otherwise the conservative default."""
    name = str(model or "").strip()
    for pattern, cap in _REGISTRY:
        if pattern.search(name):
            return cap
    if re.search(r"think|reason|-r\d|:r\d", name, re.I):
        return ModelCapability("unknown-reasoner", emits_reasoning=True, supports_json_object=True, max_tokens_floor=4096)
    return _DEFAULT


# ---------------------------------------------------------------------------
# Robust JSON extraction (reasoning preambles, <think> blocks, code fences)
# ---------------------------------------------------------------------------

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def extract_json_object(text: str) -> Dict[str, Any]:
    """Recover a JSON object from a possibly-messy model response. Strips
    ``<think>`` reasoning blocks and code fences, tries a direct parse, then
    falls back to the LARGEST balanced ``{...}`` block that parses (so a small
    echoed example object never wins over the real payload)."""
    if text is None:
        raise StructuredOutputError("empty response")
    s = _THINK_RE.sub(" ", str(text)).strip()
    if s.startswith("```"):
        s = "\n".join(l for l in s.split("\n") if not l.strip().startswith("```"))
    s = s.strip()
    if not s:
        raise StructuredOutputError("empty response after stripping reasoning/fences")
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # collect all balanced top-level {...} spans, return the largest that parses
    best: Optional[Dict[str, Any]] = None
    best_len = -1
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                span = s[start:i + 1]
                try:
                    obj = json.loads(span)
                    if isinstance(obj, dict) and len(span) > best_len:
                        best, best_len = obj, len(span)
                except Exception:
                    pass
                start = -1
    if best is not None:
        return best
    raise StructuredOutputError(f"no JSON object found in response (head: {s[:120]!r})")


def _parse_response(resp: Any) -> Dict[str, Any]:
    """Prefer the raw ``.text`` (robust extraction); fall back to ``.json()`` for
    backends/fakes that pre-parse."""
    text = getattr(resp, "text", "") or ""
    if text.strip():
        return extract_json_object(text)
    if hasattr(resp, "json"):
        try:
            obj = resp.json()
            if isinstance(obj, dict):
                return obj
        except Exception as exc:
            raise StructuredOutputError(f"backend .json() failed: {exc}") from exc
    raise StructuredOutputError("response had neither parseable .text nor .json()")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def structured_complete(
    backend: Any,
    *,
    system: str,
    user: str,
    schema: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    task_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a structured-output completion robustly across providers, returning a
    parsed dict. Picks the strategy from the model's capability profile and parses
    the result tolerantly. Raises :class:`StructuredOutputError` if no JSON object
    can be recovered.

    ``schema`` (a JSON Schema) is used for guided decoding only when the model
    supports it; otherwise ``json_object`` + robust extraction is used."""
    cap = capability_for(model or getattr(backend, "model", ""))
    mt = max(int(max_tokens or 0), cap.max_tokens_floor)
    temp = temperature if cap.temperature_clamp is None else cap.temperature_clamp

    response_format: Optional[Dict[str, Any]] = None
    if schema is not None and cap.supports_guided_json:
        response_format = {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}}
    elif cap.supports_json_object:
        response_format = {"type": "json_object"}

    system_prompt = system + _REASONING_SUFFIX if cap.emits_reasoning else system

    resp = backend.complete(
        system=system_prompt, user=user, temperature=temp, max_tokens=mt,
        response_format=response_format, task_hint=task_hint,
    )
    return _parse_response(resp)
