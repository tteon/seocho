import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.benchmarks import customer_query_mara_live as module
from seocho.eval.customer_query_dataset import generate_customer_queries


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Backend:
    def __init__(self, **kwargs):
        pass

    async def acomplete(self, *, user, **kwargs):
        evidence = json.loads(user)
        return _Response(
            {
                "support_status": evidence["authoritative_support_status"],
                "missing_sources": evidence["missing_sources"],
                "answer": "bounded",
            }
        )


def _args(tmp_path: Path) -> argparse.Namespace:
    dataset = tmp_path / "queries.jsonl"
    dataset.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in generate_customer_queries(count=10, seed=31)
        )
    )
    bulk = tmp_path / "bulk.json"
    bulk.write_text(
        json.dumps(
            {
                "source_details": {
                    "market_api": {"available": True},
                    "blockchain_api": {"available": True},
                    "order_api": {"available": False},
                    "transfer_api": {"available": False},
                    "withdrawal_api": {"available": False},
                }
            }
        )
    )
    return argparse.Namespace(
        dataset=dataset,
        bulk_report=bulk,
        per_intent=1,
        model="test",
        concurrency=2,
        request_timeout=1.0,
        checkpoint=tmp_path / "checkpoint.jsonl",
        progress_every=0,
    )


def test_checkpoint_is_durable_and_resumable(tmp_path, monkeypatch) -> None:
    args = _args(tmp_path)
    monkeypatch.setattr(module, "MaraBackend", _Backend)
    first = asyncio.run(module.run(args))
    assert first["queries"] == 10
    assert first["executed_queries"] == 10
    assert first["resumed_queries"] == 0
    with args.checkpoint.open(encoding="utf-8") as f:
        assert sum(1 for _ in f) == 10

    class _MustNotRun(_Backend):
        async def acomplete(self, **kwargs):
            raise AssertionError("completed checkpoint rows must not be called again")

    monkeypatch.setattr(module, "MaraBackend", _MustNotRun)
    resumed = asyncio.run(module.run(args))
    assert resumed["queries"] == 10
    assert resumed["executed_queries"] == 0
    assert resumed["resumed_queries"] == 10
