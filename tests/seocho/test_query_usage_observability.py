from seocho.query.observability import LLMUsage, UsageTimer, usage_from_response


def test_usage_normalizes_openai_shape() -> None:
    usage = usage_from_response(
        {"model": "m", "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
    )
    assert usage.as_dict()["total_tokens"] == 5
    assert usage.model == "m"


def test_timer_records_latency() -> None:
    usage = LLMUsage()
    with UsageTimer(usage):
        pass
    assert usage.latency_ms >= 0
