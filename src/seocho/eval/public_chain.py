"""Public-data adapters for blockchain long-term-memory evaluation."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote
from xml.etree import ElementTree

from ..memory import TransactionEvent, opaque_ref


DEFAULT_OFAC_SDN_XML_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
DEFAULT_ESPLORA_API_URL = "https://blockstream.info/api"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_ofac_xbt_addresses(xml_text: str) -> tuple[str, ...]:
    """Extract current XBT digital-currency identifiers from OFAC SDN XML."""

    root = ElementTree.fromstring(xml_text)
    addresses: set[str] = set()
    for element in root.iter():
        if _local_name(element.tag) != "id":
            continue
        fields = {
            _local_name(child.tag): (child.text or "").strip()
            for child in list(element)
        }
        identifier_type = fields.get("idType", "").upper()
        identifier = fields.get("idNumber", "").strip()
        if "DIGITAL CURRENCY ADDRESS" in identifier_type and "XBT" in identifier_type:
            if identifier:
                addresses.add(identifier)
    return tuple(sorted(addresses))


class PublicDataHTTPClient:
    """Small HTTP client with explicit source URLs and bounded timeouts."""

    def __init__(self, *, timeout_seconds: float = 20.0, session: Any = None) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._timeout = timeout_seconds

    def text(self, url: str) -> str:
        response = self._session.get(url, timeout=self._timeout)
        response.raise_for_status()
        return str(response.text)

    def json(self, url: str) -> Any:
        response = self._session.get(url, timeout=self._timeout)
        response.raise_for_status()
        return response.json()


class EsploraPublicClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_ESPLORA_API_URL,
        http: PublicDataHTTPClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http or PublicDataHTTPClient()

    def address_transactions(
        self, address: str, *, max_pages: int = 1
    ) -> tuple[Mapping[str, Any], ...]:
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        encoded_address = quote(address.strip(), safe="")
        transactions: list[Mapping[str, Any]] = []
        last_seen = ""
        for _ in range(max_pages):
            suffix = f"/{last_seen}" if last_seen else ""
            url = f"{self.base_url}/address/{encoded_address}/txs/chain{suffix}"
            page = self.http.json(url)
            if not isinstance(page, list):
                raise ValueError("Esplora address response must be a list")
            transactions.extend(dict(item) for item in page)
            if len(page) < 25:
                break
            last_seen = str(page[-1].get("txid", ""))
            if not last_seen:
                break
        return tuple(transactions)


def _addresses_from_inputs(transaction: Mapping[str, Any]) -> set[str]:
    addresses: set[str] = set()
    for item in transaction.get("vin", ()) or ():
        previous = item.get("prevout") or {}
        address = str(previous.get("scriptpubkey_address", "")).strip()
        if address:
            addresses.add(address)
    return addresses


def _outputs(transaction: Mapping[str, Any]) -> list[tuple[str, int]]:
    outputs: list[tuple[str, int]] = []
    for item in transaction.get("vout", ()) or ():
        address = str(item.get("scriptpubkey_address", "")).strip()
        if address:
            outputs.append((address, int(item.get("value", 0))))
    return outputs


def esplora_transactions_to_events(
    *,
    workspace_id: str,
    risk_address: str,
    transactions: Sequence[Mapping[str, Any]],
    label_source: str = "ofac_sdn",
) -> tuple[TransactionEvent, ...]:
    """Map confirmed transactions touching a labelled address to safe events.

    This is an address-interaction benchmark, not wallet-owner attribution.
    Raw addresses are converted to opaque references before memory ingestion.
    """

    events: list[TransactionEvent] = []
    risk_ref = opaque_ref(risk_address, namespace="wallet")
    for transaction in transactions:
        status = dict(transaction.get("status") or {})
        if not status.get("confirmed"):
            continue
        block_hash = str(status.get("block_hash", "")).strip()
        tx_hash = str(transaction.get("txid", "")).strip()
        if not block_hash or not tx_hash or status.get("block_height") is None:
            continue
        input_addresses = _addresses_from_inputs(transaction)
        outputs = _outputs(transaction)
        output_addresses = {address for address, _ in outputs}
        counterparties: dict[str, tuple[str, int]] = {}
        if risk_address in input_addresses:
            for address, value in outputs:
                if address != risk_address:
                    direction, amount = counterparties.get(address, ("sent_from_label", 0))
                    counterparties[address] = (direction, amount + value)
        if risk_address in output_addresses:
            received = sum(value for address, value in outputs if address == risk_address)
            for address in input_addresses:
                if address != risk_address:
                    counterparties.setdefault(address, ("sent_to_label", received))

        occurred_at = datetime.fromtimestamp(
            int(status.get("block_time", 0)), tz=timezone.utc
        ).isoformat()
        for event_index, (address, (direction, satoshis)) in enumerate(
            sorted(counterparties.items())
        ):
            events.append(
                TransactionEvent(
                    workspace_id=workspace_id,
                    chain_id="bitcoin-mainnet",
                    block_height=int(status["block_height"]),
                    block_hash=block_hash,
                    tx_hash=tx_hash,
                    event_index=event_index,
                    customer_ref=opaque_ref(address, namespace="wallet"),
                    counterparty_ref=risk_ref,
                    provenance_id=f"esplora:{tx_hash}:{event_index}",
                    occurred_at=occurred_at,
                    asset="BTC",
                    amount=str(satoshis),
                    risk_reason_codes=("direct_public_sanctions_label_interaction",),
                    metadata={
                        "amount_unit": "satoshi",
                        "direction": direction,
                        "label_source": label_source,
                        "attribution_scope": "address_interaction_only",
                    },
                )
            )
    return tuple(events)


def group_events_by_block(
    events: Iterable[TransactionEvent],
) -> tuple[tuple[tuple[int, str], tuple[TransactionEvent, ...]], ...]:
    grouped: dict[tuple[int, str], list[TransactionEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.block_height, event.block_hash)].append(event)
    return tuple(
        (key, tuple(sorted(value, key=lambda item: (item.tx_hash, item.event_index))))
        for key, value in sorted(grouped.items())
    )
