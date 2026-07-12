"""English customer-query corpus grounded in exchange support workflows."""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass
from typing import Any, Iterator, Tuple


@dataclass(frozen=True, slots=True)
class CustomerQuerySeed:
    intent: str
    relationship: str
    question: str
    required_slots: Tuple[str, ...]
    live_sources: Tuple[str, ...]
    memory_sources: Tuple[str, ...]
    denied_inferences: Tuple[str, ...]
    max_hops: int
    source_url: str


SEEDS = (
    CustomerQuerySeed("order_status", "user_to_self", "What is the current status of my BTC-USDT order?", ("order_state", "filled_size", "remaining_size", "provenance"), ("order_api",), ("postgresql_revision", "graph_projection"), (), 2, "https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-management"),
    CustomerQuerySeed("partial_fill", "user_to_market", "Why was only part of my limit order filled?", ("filled_size", "remaining_size", "limit_price", "market_timestamp"), ("order_api", "market_api"), ("order_history",), (), 2, "https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-management"),
    CustomerQuerySeed("slippage", "user_to_market", "Why did my market order fill above the price I saw?", ("displayed_price", "average_fill_price", "order_book_time", "fees"), ("market_api", "order_api"), ("fill_history",), (), 2, "https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-management"),
    CustomerQuerySeed("withdrawal_pending", "user_to_network", "Why is my withdrawal still pending?", ("withdrawal_state", "network", "confirmation_count", "required_confirmations"), ("withdrawal_api", "blockchain_api"), ("withdrawal_history",), (), 2, "https://www.okx.com/help/why-has-not-my-withdrawal-arrived-in-the-account"),
    CustomerQuerySeed("recipient_missing", "user_to_counterparty", "I sent crypto, but the recipient has not received it. What happened?", ("transfer_state", "network", "confirmation_count", "destination_match"), ("transfer_api", "blockchain_api"), ("counterparty_history",), ("counterparty_real_identity", "wallet_ownership"), 2, "https://help.coinbase.com/en/coinbase/trading-and-funding/cryptocurrency-trading-pairs/send-and-receive-troubleshooting"),
    CustomerQuerySeed("transfer_history", "user_to_counterparty", "Show my previous transfers to this counterparty.", ("transfers", "states", "timestamps", "provenance"), (), ("postgresql_revision", "graph_projection"), ("counterparty_real_identity", "wallet_ownership"), 3, "https://www.okx.com/en-gb/help/how-do-i-check-my-deposit-or-withdrawal-history"),
    CustomerQuerySeed("account_history", "user_to_self", "Show my deposits and withdrawals from the last 90 days.", ("deposits", "withdrawals", "asset", "timestamp"), (), ("funding_history",), (), 1, "https://www.okx.com/en-us/help/how-do-i-download-my-statements"),
    CustomerQuerySeed("historical_order", "self_to_prior_self", "What was this order's state when I contacted support yesterday?", ("historical_state", "sequence", "prompt_version", "provenance"), (), ("postgresql_revision", "answer_receipt"), (), 2, "https://www.okx.com/en-us/help/how-do-i-download-my-statements"),
    CustomerQuerySeed("reorg_explanation", "user_to_network", "Why did a confirmed settlement become reversed?", ("orphaned_block", "replacement_block", "historical_state", "current_state"), ("blockchain_api",), ("postgresql_revision", "graph_projection"), (), 3, "https://help.coinbase.com/en-gb/coinbase/trading-and-funding/sending-or-receiving-cryptocurrency/why-is-my-transaction-pending"),
    CustomerQuerySeed("relevant_memory", "self_to_prior_self", "Show only the prior memories that directly influenced this cancellation.", ("selected_revisions", "exclusion_reasons", "provenance", "token_budget"), (), ("context_graph", "postgresql_revision"), (), 3, "https://www.okx.com/en-us/help/how-do-i-download-my-statements"),
)

_FORMS = (
    "{question}", "Can you explain this: {question}", "I need help. {question}",
    "Please check the evidence and answer: {question}",
    "Use the latest available data. {question}",
    "Do not guess; tell me if data is stale. {question}",
)


def generate_customer_queries(*, count: int, seed: int = 20260712) -> Iterator[dict[str, Any]]:
    if count < 1:
        raise ValueError("count must be positive")
    rng = random.Random(seed)
    for index in range(count):
        item = SEEDS[index % len(SEEDS)]
        question = rng.choice(_FORMS).format(question=item.question)
        query_id = hashlib.sha256(f"{seed}:{index}:{question}".encode()).hexdigest()[:20]
        yield {
            "query_id": query_id,
            "question": question,
            "gold": {key: value for key, value in asdict(item).items() if key != "question"},
            "language": "en",
            "synthetic_paraphrase": question != item.question,
        }


__all__ = ["CustomerQuerySeed", "SEEDS", "generate_customer_queries"]
