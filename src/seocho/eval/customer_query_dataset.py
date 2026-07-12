"""English customer-query corpus grounded in exchange support workflows."""

from __future__ import annotations

import hashlib
import random
import re
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


@dataclass(frozen=True, slots=True)
class CustomerQueryBoundaryDecision:
    action: str
    intents: Tuple[str, ...]
    reason: str


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

_TEMPLATES: dict[str, tuple[str, ...]] = {
    "order_status": (
        "Where does my {pair} {order_type} order stand right now?",
        "Has my {pair} order been filled, cancelled, or left open?",
        "Check the filled and remaining size of my {pair} order from {time_ref}.",
        "My {pair} order still appears active; what state does the exchange report?",
        "Give me the current lifecycle state and provenance for this {order_type} order.",
    ),
    "partial_fill": (
        "Why did my {pair} {order_type} order execute only partially?",
        "What prevented the remaining quantity of my {pair} order from filling?",
        "Compare my limit price with the market when this partial fill occurred {time_ref}.",
        "Only some of my {asset} was bought; was liquidity unavailable at my price?",
        "Explain the filled and unfilled portions of this {pair} limit order.",
    ),
    "slippage": (
        "Why was the average fill price for {pair} worse than the quote I saw?",
        "Compare the displayed {pair} price with my execution price {time_ref}.",
        "How much of this {pair} price difference came from slippage versus fees?",
        "The market moved while my {order_type} order executed; explain the final price.",
        "Reconstruct the order-book time and average fill price for my {asset} trade.",
    ),
    "withdrawal_pending": (
        "Why is my {asset} withdrawal over {network} still pending?",
        "How many confirmations does my {network} withdrawal have and require?",
        "Check whether my {asset} withdrawal from {time_ref} has been broadcast.",
        "Is the delay in exchange processing or {network} confirmation?",
        "My withdrawal has not completed; show its state without guessing an ETA.",
    ),
    "recipient_missing": (
        "I sent {asset} over {network}, but the recipient cannot see it; what evidence is available?",
        "Was my transfer broadcast to the destination I supplied, and how many confirmations exist?",
        "The receiving wallet has not credited my {asset} transfer from {time_ref}; investigate.",
        "Check the network state and destination match for this missing recipient transfer.",
        "Can you prove delivery status without inferring who owns the destination wallet?",
    ),
    "transfer_history": (
        "List my earlier {asset} transfers to this same destination.",
        "What states and timestamps exist for my previous transfers over {network}?",
        "Show the provenance of transfers to this counterparty during {time_ref}.",
        "Have I previously sent funds to this destination, and what happened to them?",
        "Retrieve only my transfer history linked to this counterparty, not their identity.",
    ),
    "account_history": (
        "Show my {asset} deposits and withdrawals for {time_ref}.",
        "Export the funding events on my account during {time_ref}.",
        "Which deposits and withdrawals changed my {asset} balance {time_ref}?",
        "Give me a timestamped account funding history for {asset}.",
        "Reconcile incoming and outgoing {asset} movements in {time_ref}.",
    ),
    "historical_order": (
        "What state did my {pair} order have when I contacted support {time_ref}?",
        "Answer from the historical revision, not the current state of this order.",
        "Reproduce the order answer recorded {time_ref}, including its prompt version.",
        "Which causal sequence and provenance supported the earlier {pair} order state?",
        "Has this order changed since the answer I received {time_ref}?",
    ),
    "reorg_explanation": (
        "Why did my confirmed {asset} settlement become reversed after a chain reorganization?",
        "Show the orphaned and replacement blocks for this {network} transaction.",
        "How did the canonical state change between the old block and the new chain?",
        "A previously confirmed transfer is now reversed; distinguish historical from current state.",
        "Explain this reorg using block provenance rather than treating it as a new payment.",
    ),
    "relevant_memory": (
        "Which prior revisions directly caused this {action} decision?",
        "Select only memories relevant to the current {pair} {action}, within the token budget.",
        "Why were older events excluded from the context used for this {action}?",
        "Trace the causal memory path behind the agent's latest {action}.",
        "Return the minimal provenance-backed context for this decision, excluding superseded state.",
    ),
}

_ASSETS = ("BTC", "ETH", "USDT", "SOL", "XRP", "DOGE", "ADA", "LTC")
_PAIRS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "BTC-USDC", "ETH-BTC", "LTC-USDT")
_NETWORKS = ("Bitcoin", "Ethereum", "Arbitrum", "Optimism", "Solana", "Tron", "Polygon", "Lightning")
_ORDER_TYPES = ("limit", "market", "post-only", "IOC", "stop-limit")
_TIME_REFS = ("today", "yesterday", "the last 24 hours", "the last seven days", "the previous month", "before the latest login", "at 14:30 UTC", "during the last support session")
_ACTIONS = ("cancellation", "replacement", "settlement", "retry", "transfer approval")
_WRAPPERS = (
    "{body}",
    "Please verify the evidence first. {body}",
    "Do not guess if a source is unavailable. {body}",
    "I need an auditable answer: {body}",
    "Use the state valid at the requested time. {body}",
)
_CONTEXT_SUFFIXES: dict[str, tuple[str, ...]] = {
    "order_status": ("This concerns the {pair} market.", "The relevant event occurred {time_ref}.", "The order instruction was {order_type}."),
    "partial_fill": ("This concerns the {pair} market.", "The relevant event occurred {time_ref}.", "The order instruction was {order_type}."),
    "slippage": ("This concerns the {pair} market.", "The relevant event occurred {time_ref}.", "The order instruction was {order_type}."),
    "withdrawal_pending": ("The asset involved is {asset}.", "The selected network was {network}.", "The relevant event occurred {time_ref}."),
    "recipient_missing": ("The asset involved is {asset}.", "The selected network was {network}.", "The relevant event occurred {time_ref}."),
    "transfer_history": ("The asset involved is {asset}.", "The selected network was {network}.", "The relevant period is {time_ref}."),
    "account_history": ("The asset involved is {asset}.", "The relevant period is {time_ref}.", "Use account funding records for {asset}."),
    "historical_order": ("This concerns the {pair} market.", "The requested historical point is {time_ref}.", "The order instruction was {order_type}."),
    "reorg_explanation": ("The asset involved is {asset}.", "The selected network was {network}.", "The prior confirmation was observed {time_ref}."),
    "relevant_memory": ("This concerns the {pair} workflow.", "The decision type is {action}.", "The requested historical window is {time_ref}."),
}
_CHALLENGE_CONTEXT_SUFFIXES = (
    "The asset mentioned is {asset}.",
    "The user selected {network}.",
    "The message refers to {time_ref}.",
)


def generate_customer_queries(*, count: int, seed: int = 20260712) -> Iterator[dict[str, Any]]:
    if count < 1:
        raise ValueError("count must be positive")
    rng = random.Random(seed)
    seen: set[str] = set()
    for index in range(count):
        item = SEEDS[index % len(SEEDS)]
        templates = _TEMPLATES[item.intent]
        attempts = 0
        while True:
            family = rng.randrange(len(templates))
            values = {
                "asset": rng.choice(_ASSETS), "pair": rng.choice(_PAIRS),
                "network": rng.choice(_NETWORKS), "order_type": rng.choice(_ORDER_TYPES),
                "time_ref": rng.choice(_TIME_REFS), "action": rng.choice(_ACTIONS),
            }
            body = templates[family].format(**values)
            body = f"{body} {rng.choice(_CONTEXT_SUFFIXES[item.intent]).format(**values)}"
            question = rng.choice(_WRAPPERS).format(body=body)
            if question not in seen:
                seen.add(question)
                break
            attempts += 1
            if attempts > 10_000:
                raise RuntimeError(f"query diversity exhausted for {item.intent}")
        query_id = hashlib.sha256(f"{seed}:{index}:{question}".encode()).hexdigest()[:20]
        yield {
            "query_id": query_id,
            "question": question,
            "gold": {key: value for key, value in asdict(item).items() if key != "question"},
            "language": "en",
            "synthetic_paraphrase": True,
            "template_family": f"{item.intent}.f{family}",
            "split": "held_out" if family == len(templates) - 1 else "evaluation",
        }


_INTENT_TERMS: dict[str, tuple[tuple[str, ...], ...]] = {
    "order_status": (("order", "state"), ("order", "status"), ("filled", "remaining"), ("filled", "cancelled", "open"), ("lifecycle", "order")),
    "partial_fill": (("partial", "fill"), ("partially", "execute"), ("remaining", "quantity", "fill"), ("filled", "unfilled"), ("liquidity", "price")),
    "slippage": (("slippage",), ("average", "fill", "price"), ("displayed", "execution", "price"), ("quote", "worse"), ("order", "book", "price")),
    "withdrawal_pending": (("withdrawal", "pending"), ("withdrawal", "confirmations"), ("withdrawal", "broadcast"), ("exchange", "processing", "confirmation"), ("withdrawal", "completed")),
    "recipient_missing": (("recipient", "cannot", "see"), ("receiving", "wallet", "credited"), ("missing", "recipient", "transfer"), ("destination", "match", "transfer"), ("prove", "delivery", "wallet")),
    "transfer_history": (("earlier", "transfers"), ("previous", "transfers"), ("transfer", "history"), ("previously", "sent", "destination"), ("transfers", "counterparty")),
    "account_history": (("deposits", "withdrawals"), ("funding", "events"), ("account", "funding", "history"), ("balance", "deposits"), ("incoming", "outgoing", "movements")),
    "historical_order": (("historical", "revision"), ("state", "contacted", "support"), ("earlier", "order", "state"), ("causal", "sequence", "order"), ("order", "changed", "answer")),
    "reorg_explanation": (("reorg",), ("chain", "reorganization"), ("orphaned", "replacement", "blocks"), ("canonical", "old", "block"), ("confirmed", "reversed", "chain")),
    "relevant_memory": (("prior", "revisions", "caused"), ("memories", "token", "budget"), ("events", "excluded", "context"), ("causal", "memory", "path"), ("minimal", "context", "superseded")),
}

_CHALLENGE_SEEDS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("Where is my transfer?", "ambiguous", ("withdrawal_pending", "recipient_missing"), "clarify"),
    ("Why is the price on my order different?", "ambiguous", ("partial_fill", "slippage"), "clarify"),
    ("Show me the old status of this order.", "ambiguous", ("order_status", "historical_order"), "clarify"),
    ("Find my previous account transactions.", "ambiguous", ("account_history", "transfer_history"), "clarify"),
    ("Why is my withdrawal pending, and has the recipient received the transfer?", "multi_intent", ("withdrawal_pending", "recipient_missing"), "decompose"),
    ("Show the current order status and explain why it only partially filled.", "multi_intent", ("order_status", "partial_fill"), "decompose"),
    ("Compare my fill price and then retrieve the historical support answer.", "multi_intent", ("slippage", "historical_order"), "decompose"),
    ("Explain the reorg and list the memories that changed the settlement decision.", "multi_intent", ("reorg_explanation", "relevant_memory"), "decompose"),
    ("Write a poem about Bitcoin.", "out_of_scope", (), "reject"),
    ("Predict the BTC price next Friday.", "out_of_scope", (), "reject"),
    ("Who owns this wallet in real life?", "out_of_scope", (), "reject"),
    ("Give me another customer's withdrawal history.", "out_of_scope", (), "reject"),
)


def generate_customer_query_challenges(
    *, count: int = 300, seed: int = 20260713
) -> Iterator[dict[str, Any]]:
    """Generate boundary cases that must clarify, decompose, or reject."""

    if count < 1:
        raise ValueError("count must be positive")
    rng = random.Random(seed)
    seen: set[str] = set()
    for index in range(count):
        base, kind, acceptable, action = _CHALLENGE_SEEDS[index % len(_CHALLENGE_SEEDS)]
        attempts = 0
        while True:
            values = {
                "asset": rng.choice(_ASSETS), "pair": rng.choice(_PAIRS),
                "network": rng.choice(_NETWORKS), "order_type": rng.choice(_ORDER_TYPES),
                "time_ref": rng.choice(_TIME_REFS), "action": rng.choice(_ACTIONS),
            }
            question = f"{rng.choice(_WRAPPERS).format(body=base)} {rng.choice(_CHALLENGE_CONTEXT_SUFFIXES).format(**values)}"
            if question not in seen:
                seen.add(question)
                break
            attempts += 1
            if attempts > 10_000:
                raise RuntimeError(f"challenge diversity exhausted for {kind}")
        yield {
            "query_id": hashlib.sha256(f"challenge:{seed}:{index}:{question}".encode()).hexdigest()[:20],
            "question": question,
            "gold": {
                "kind": kind,
                "acceptable_intents": acceptable,
                "expected_action": action,
            },
            "language": "en",
            "synthetic_paraphrase": True,
            "template_family": f"challenge.{index % len(_CHALLENGE_SEEDS)}",
            "split": "challenge",
        }


def classify_customer_query(question: str) -> CustomerQuerySeed | None:
    """Route an English customer question by stable support-workflow vocabulary."""

    tokens = set(re.findall(r"[a-z0-9]+", question.lower()))
    scored: list[tuple[float, CustomerQuerySeed]] = []
    for item in SEEDS:
        patterns = _INTENT_TERMS[item.intent]
        matches = [sum(term in tokens for term in pattern) / len(pattern) for pattern in patterns]
        full_matches = sum(value == 1 for value in matches)
        vocabulary_hits = sum(term in tokens for pattern in patterns for term in pattern)
        score = max(matches) + 0.05 * full_matches + 0.002 * vocabulary_hits
        scored.append((score, item))
    scored.sort(key=lambda value: value[0], reverse=True)
    if not scored or scored[0][0] < 0.66:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.001:
        return None
    return scored[0][1]


def detect_customer_query_boundary(
    question: str,
) -> CustomerQueryBoundaryDecision | None:
    """Deterministically stop known ontology-boundary questions from over-routing."""

    normalized = " ".join(re.findall(r"[a-z0-9]+", question.lower()))
    rules = (
        (("where is my transfer",), ("recipient_missing", "withdrawal_pending"), "transfer direction is unspecified"),
        (("price on my order different",), ("partial_fill", "slippage"), "price difference does not identify fill completeness"),
        (("old status of this order",), ("historical_order", "order_status"), "requested revision point is unspecified"),
        (("previous account transactions",), ("account_history", "transfer_history"), "account funding versus destination transfer is unspecified"),
    )
    for phrases, intents, reason in rules:
        if all(phrase in normalized for phrase in phrases):
            return CustomerQueryBoundaryDecision("clarify", intents, reason)
    return None


__all__ = [
    "CustomerQuerySeed",
    "CustomerQueryBoundaryDecision",
    "SEEDS",
    "classify_customer_query",
    "detect_customer_query_boundary",
    "generate_customer_queries",
    "generate_customer_query_challenges",
]
