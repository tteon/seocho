from __future__ import annotations

from typing import Any


def extract_usage(value: dict[str, Any]) -> dict[str, Any]:
    usage = value.get('usage') or (value.get('response') or {}).get('usage') or {}
    details = usage.get('completion_tokens_details') or usage.get('output_tokens_details') or {}
    return {'raw': usage, 'input_tokens': usage.get('prompt_tokens', usage.get('input_tokens')),
            'output_tokens': usage.get('completion_tokens', usage.get('output_tokens')),
            'reasoning_tokens': details.get('reasoning_tokens', usage.get('reasoning_tokens'))}
