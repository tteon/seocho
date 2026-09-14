"""Contract tests; synthetic inputs are not serving-performance evidence."""

from __future__ import annotations

import json

import httpx
import pytest

from seocho.eval.inference_dashboard import write_dashboard
from seocho.eval.inference_finops import alerts, engine_window, summarize
from seocho.eval.inference_probe import probe, serving_plan, read_metric
from seocho.eval.inference_receipts import import_receipts
from seocho.eval.inference_spine import (
    allocate_run_cost,
    apply_cost_ledger,
    join_engine_spans,
)


def row(key: str = "r1", **changes: object) -> dict:
    return {
        "request_id": key,
        "model": "m",
        "tenant": "t",
        "route": "graph",
        "workspace_id": "w",
        "configuration_id": "c",
        "workload_id": "q",
        "status": "ok",
        "input_tokens": 10,
        "output_tokens": 2,
        **changes,
    }


def test_accounting_requires_complete_cost_and_counts_failures() -> None:
    a = row(cost_usd=0.01, cost_basis="invoice")
    with pytest.raises(ValueError, match="Duplicate"):
        summarize([a, a])
    b = row("r2", status="error")
    result = summarize([a, b])["groups"][0]
    assert result["cost_usd"] is None and result["cost_per_1m_total_tokens"] is None
    assert result["known_cost_usd"] == 0.01 and result["errors"] == 1
    result = summarize(
        apply_cost_ledger(
            [a, b], [{"request_id": "r2", "cost_usd": 0.02, "cost_basis": "invoice"}]
        )
    )["groups"][0]
    assert result["cost_per_1m_output_tokens"] == pytest.approx(7500)
    with pytest.raises(ValueError, match="already priced"):
        apply_cost_ledger([a], [{"request_id": "r1", "cost_usd": 0.02}])


@pytest.mark.parametrize(
    "changes",
    [
        {"itl_ms": 2, "itl_source": "sse"},
        {"output_tokens": 1.5},
        {"cost_usd": 0},
        {"output_tokens": -1},
    ],
)
def test_invalid_measurement_rejected(changes: dict) -> None:
    with pytest.raises(ValueError):
        summarize([row(**changes)])


def test_allocation_conserves_total_and_separates_idle() -> None:
    rows, allocation = allocate_run_cost(
        [row(), row("r2", status="error")],
        total_usd=9,
        unallocated_usd=3,
        basis="provider hourly estimate",
    )
    assert sum(r["cost_usd"] for r in rows) + allocation["unallocated_cost_usd"] == 9
    assert all(r["cost_usd"] == 3 for r in rows)
    with pytest.raises(ValueError):
        allocate_run_cost(
            [row(output_tokens=None)], total_usd=9, unallocated_usd=3, basis="estimate"
        )


def test_engine_counters_and_dcgm_keep_scope_and_denominator() -> None:
    before = "\n".join(
        f'vllm:{name}{{model="m"}} 0'
        for name in [
            "spec_decode_num_draft_tokens_total",
            "spec_decode_num_accepted_tokens_total",
            "spec_decode_num_drafts_total",
        ]
    )
    after = (
        before.replace(
            'draft_tokens_total{model="m"} 0', 'draft_tokens_total{model="m"} 20'
        )
        .replace(
            'accepted_tokens_total{model="m"} 0', 'accepted_tokens_total{model="m"} 12'
        )
        .replace('drafts_total{model="m"} 0', 'drafts_total{model="m"} 4')
    )
    result = engine_window(
        before,
        after,
        'DCGM_FI_DEV_GPU_UTIL{gpu="0"} 60\nDCGM_FI_DEV_GPU_UTIL{gpu="1"} 9223372036854775794',
    )
    assert (
        result["accepted_token_rate"] == 0.6 and result["accepted_per_draft_step"] == 3
    )
    assert result["scope"] == "engine_window_not_tenant" and len(result["gpu"]) == 1
    assert result["itl_ms"] is None
    assert engine_window(after, before)["accepted_token_rate"] is None
    assert (
        engine_window(before, after.replace('model="m"', 'model="new"'))[
            "accepted_token_rate"
        ]
        is None
    )


def test_drift_matches_cohort_and_observed_coverage() -> None:
    old = summarize(
        [
            row(str(i), ttft_ms=10, cost_usd=0.01, cost_basis="invoice")
            for i in range(20)
        ]
    )
    current = summarize(
        [
            row(str(i), ttft_ms=50, cost_usd=0.04, cost_basis="invoice")
            for i in range(20)
        ]
    )
    assert {a["kind"] for a in alerts(current, old)} == {"cost_spike", "ttft_drift"}
    current["groups"][0]["configuration_id"] = "other"
    assert not alerts(current, old)
    current["groups"][0]["configuration_id"] = "c"
    current["groups"][0]["ttft_ms_coverage"] = 1
    assert {a["kind"] for a in alerts(current, old)} == {"cost_spike"}
    varied = summarize(
        [
            row(input_digest="x", output_digest="y"),
            row("r2", input_digest="x", output_digest="z"),
        ]
    )
    assert alerts(varied)[0]["kind"] == "output_variation_not_quality_verdict"


def test_span_join_uses_identity_and_preserves_client_ttft() -> None:
    a = row(provider_response_id="resp", trace_id="trace", ttft_ms=200)
    span = {
        "name": "llm_request",
        "service_name": "vllm",
        "trace_id": "trace",
        "span_id": "s",
        "attributes": {
            "gen_ai.request.id": "resp",
            "gen_ai.latency.time_in_queue": 0.01,
            "gen_ai.latency.time_to_first_token": 0.1,
        },
    }
    result = join_engine_spans([a], [span])[0]
    assert (
        result["queue_ms"] == 10
        and result["engine_ttft_ms"] == 100
        and result["ttft_ms"] == 200
    )
    ambiguous = {**span, "span_id": "s2"}
    assert (
        join_engine_spans([a], [span, ambiguous])[0]["engine_span_match"] == "ambiguous"
    )


def test_response_identity_mismatch_cannot_borrow_another_span() -> None:
    request = row(provider_response_id="expected", trace_id="trace")
    wrong = {
        "service_name": "vllm",
        "name": "llm_request",
        "trace_id": "trace",
        "span_id": "s",
        "attributes": {"gen_ai.request.id": "other", "gen_ai.latency.time_in_queue": 1},
    }
    assert join_engine_spans([request], [wrong])[0]["engine_span_match"] == "missing"


def test_metrics_do_not_receive_inference_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, text="metric 1")

    with httpx.Client(
        headers={"Authorization": "Bearer private"},
        transport=httpx.MockTransport(handler),
    ) as client:
        assert read_metric(client, "http://metrics/metrics") == ("metric 1", None)


def test_receipt_import_preserves_empty_final_and_prompt_identity(tmp_path) -> None:
    for name in ("a", "b"):
        p = tmp_path / name
        p.mkdir()
        (p / "http").mkdir()
        (p / "receipt.json").write_text(
            json.dumps(
                {
                    "workspace_id": "w",
                    "arm": "graph",
                    "status": "final",
                    "answer": "",
                    "llm": [
                        {
                            "call": 1,
                            "status": "ok",
                            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                        }
                    ],
                }
            )
        )
        (p / "http/01-request.json").write_text(
            json.dumps(
                {"started": name, "payload": {"model": "m", "messages": ["same"]}}
            )
        )
        (p / "http/01-response.json").write_text(
            json.dumps(
                {"body": {"model": "m", "id": name, "choices": [{"content": "sample"}]}}
            )
        )
    result = import_receipts(
        tmp_path, tenant="t", configuration_id="c", workload_id="q"
    )
    assert result[0]["input_digest"] == result[1]["input_digest"]
    assert not result[0]["final_answer_present"] and result[0]["cost_usd"] is None
    assert (
        len(
            [
                a
                for a in alerts(summarize(result))
                if a["kind"] == "case_incomplete_or_empty_answer"
            ]
        )
        == 2
    )


def test_factorial_plan_and_dashboard_escape(tmp_path) -> None:
    plan = serving_plan("target", "/models/draft/snapshot", "sha")
    assert len(plan["arms"]) == 8
    assert sum(a["speculative"] for a in plan["arms"]) == 4
    for a in plan["arms"]:
        assert ("--speculative-config" in a["argv"]) == a["speculative"]
        assert ("--enable-prefix-caching" in a["argv"]) == a["prefix_caching"]
    out = tmp_path / "report.html"
    write_dashboard(out, summarize([row(tenant="</script><script>alert(1)</script>")]))
    assert "</script><script>alert(1)" not in out.read_text()


def test_stream_chunks_are_not_tokens_and_probe_writes_correlated_span(
    tmp_path, monkeypatch
) -> None:
    original = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["traceparent"].startswith("00-")
        body = [
            {"id": "resp", "choices": [{"delta": {"content": "many tokens at once"}}]},
            {"usage": {"prompt_tokens": 12, "completion_tokens": 5}, "choices": []},
        ]
        content = (
            "".join("data: " + json.dumps(x) + "\n\n" for x in body)
            + "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=content)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    out = tmp_path / "probe"
    probe(
        base_url="http://test/v1",
        model="m",
        prompts=[{"messages": [{"role": "user", "content": "q"}]}],
        out=out,
        tenant="t",
        workspace_id="w",
        route="r",
        configuration_id="c",
        max_calls=1,
    )
    request = json.loads((out / "requests.jsonl").read_text())
    span = json.loads((out / "client-spans.jsonl").read_text())
    assert request["output_tokens"] == 5 and request["output_chunk_count"] == 1
    assert request["itl_ms"] is None and request["cost_usd"] is None
    assert (
        request["trace_id"] == span["trace_id"]
        and request["provider_response_id"] == "resp"
    )
