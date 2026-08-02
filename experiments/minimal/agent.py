"""The answering agent, and the option not to call one.

Default is `offline`: the harness runs end to end with no model call, so the
retrieval and verification numbers can be reproduced for nothing. A model is
only reached when a run explicitly asks for it, and when it is, the exact
prompt and the exact evidence string are written into the trace before the call
goes out.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Sequence

SYSTEM = (
    "Answer the question using only the supplied graph evidence. "
    "Output JSON only, first character '{'. "
    "If the evidence does not support a field, write \"unsupported\". "
    "If two evidence items conflict, report the conflict rather than choosing. "
    "Keys: answer, used_evidence_ids, conflicts_reported, missing."
)


@dataclass(frozen=True)
class Answer:
    text: str
    parsed: dict[str, Any] | None
    model: str
    prompt_chars: int
    evidence_ids: tuple[str, ...]
    called_model: bool

    def as_dict(self) -> dict[str, Any]:
        return {"model": self.model, "called_model": self.called_model,
                "prompt_chars": self.prompt_chars,
                "evidence_ids": list(self.evidence_ids),
                "parsed": self.parsed, "text": self.text[:2000]}


def serialize_evidence(facts: Sequence, budget_chars: int) -> tuple[str, tuple[str, ...]]:
    """Compact, sorted, stable ids. Units are never split across the budget."""
    items, ids, used = [], [], 0
    for i, fact in enumerate(facts, 1):
        eid = f"E{i}"
        unit = json.dumps({"evidence_id": eid, "view": fact.view, "slot": fact.key,
                           "value": fact.raw, "unit": fact.unit,
                           "period": fact.period},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if used + len(unit) + 1 > budget_chars:
            break
        items.append(unit)
        ids.append(eid)
        used += len(unit) + 1
    return "[" + ",".join(items) + "]", tuple(ids)


def answer(question: str, evidence: str, evidence_ids: Sequence[str],
           model: str, run: Any, offline: bool = True,
           max_tokens: int = 700, base_url: str | None = None) -> Answer:
    """Produce an answer, or an explicit offline placeholder.

    The gateway is OpenAI-compatible and returns no internal telemetry, so what
    can be traced is exactly what crosses the boundary: the resolved endpoint
    and model, the full system and user prompts, the decoding parameters, the
    response text, token usage, latency, and the finish reason. Everything the
    gateway does behind that boundary is not observable from here, and the trace
    should not imply otherwise.
    """
    prompt = json.dumps({"question": question, "evidence": evidence},
                        ensure_ascii=False)
    run.log(f"    system prompt ({len(SYSTEM)} chars): {SYSTEM[:160]}...")
    run.log(f"    user prompt ({len(prompt)} chars, {len(evidence_ids)} evidence items)")

    if offline:
        run.log("    offline: no model call issued")
        return Answer(text="", parsed=None, model=model, prompt_chars=len(prompt),
                      evidence_ids=tuple(evidence_ids), called_model=False)

    key = os.environ.get("MARA_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("no API key present but offline=false was requested")
    endpoint = base_url or os.environ.get("MARA_BASE_URL")

    request = {"model": model, "temperature": 0, "max_tokens": max_tokens,
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": prompt}]}
    # Write the request before issuing it, so a call that never returns still
    # leaves behind exactly what was sent.
    run.log(f"    endpoint={endpoint or 'default openai'} model={model} "
            f"temperature=0 max_tokens={max_tokens}")
    run.record_llm_request(endpoint=endpoint, request=request)

    from openai import OpenAI
    client = OpenAI(api_key=key, base_url=endpoint, max_retries=0)
    started = time.perf_counter()
    completion = client.chat.completions.create(**request)
    latency = time.perf_counter() - started
    text = completion.choices[0].message.content or ""
    usage = getattr(completion, "usage", None)
    run.record_llm_response(
        model=getattr(completion, "model", model),
        text=text,
        finish_reason=completion.choices[0].finish_reason,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        latency_s=round(latency, 4))
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
        run.log("    model returned non-JSON; recorded as a schema failure")
    return Answer(text=text, parsed=parsed, model=model, prompt_chars=len(prompt),
                  evidence_ids=tuple(evidence_ids), called_model=True)
