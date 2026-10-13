import json

import pytest

from seocho.recorder.adapters.pi_agent import final_message


def test_pi_jsonl_preserves_unicode_and_ignores_private_thinking() -> None:
    message = {'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'stop',
        'content': [{'type': 'thinking', 'thinking': 'private'},
                    {'type': 'text', 'text': 'answer\u2028continued'}]}}
    assert final_message(json.dumps(message, ensure_ascii=False) + '\n') == 'answer\u2028continued'


@pytest.mark.parametrize('reason', ['error', 'aborted', 'length'])
def test_pi_incomplete_responses_are_not_success(reason: str) -> None:
    message = {'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': reason, 'content': []}}
    with pytest.raises(ValueError):
        final_message(json.dumps(message))


def test_pi_extra_turn_is_not_silently_selected() -> None:
    row = json.dumps({'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'stop', 'content': []}})
    with pytest.raises(ValueError):
        final_message(row + '\n' + row)
