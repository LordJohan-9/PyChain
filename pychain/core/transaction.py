"""Versioned, network-bound transfer and coinbase transaction structures."""

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pychain.config import (
    BLOCK_REWARD,
    CHAIN_ID,
    COINBASE_TYPE,
    SECP256K1_SCHEME,
    SERIALIZATION_VERSION,
    TRANSFER_TYPE,
)
from pychain.core.hashing import canonical_json, sha256_hex
from pychain.core.signatures import get_signature_scheme
from pychain.core.wallet import Wallet, WalletError, is_valid_address

if TYPE_CHECKING:
    from collections.abc import Mapping

COINBASE = "COINBASE"


@dataclass
class Transaction:
    """A transaction whose canonical ID includes its network, type, and version."""

    sender_public_key: str
    recipient: str
    amount: int
    nonce: int
    timestamp: int
    signature: str = ""
    tx_id: str = ""
    version: int = SERIALIZATION_VERSION
    chain_id: str = CHAIN_ID
    transaction_type: str = TRANSFER_TYPE
    signature_scheme: str = SECP256K1_SCHEME

    def __post_init__(self) -> None:
        if not self.tx_id:
            self.tx_id = self.calculate_tx_id()

    @property
    def is_coinbase(self) -> bool:
        return self.transaction_type == COINBASE_TYPE

    @property
    def sender_address(self) -> str:
        try:
            return get_signature_scheme(self.signature_scheme).derive_address(
                self.sender_public_key
            )
        except ValueError as error:
            raise WalletError(str(error)) from error

    def unsigned_payload(self) -> dict[str, int | str]:
        """Return exactly the fields hashed to create the transaction ID."""
        return {
            "version": self.version,
            "chain_id": self.chain_id,
            "type": self.transaction_type,
            "signature_scheme": self.signature_scheme,
            "sender_public_key": self.sender_public_key,
            "recipient": self.recipient,
            "amount": self.amount,
            "nonce": self.nonce,
            "timestamp": self.timestamp,
        }

    def calculate_tx_id(self) -> str:
        return sha256_hex(canonical_json(self.unsigned_payload()))

    def _has_valid_fields(self) -> bool:
        if not isinstance(self.signature_scheme, str) or not isinstance(self.transaction_type, str):
            return False
        try:
            get_signature_scheme(self.signature_scheme)
        except ValueError:
            return False
        return (
            isinstance(self.version, int)
            and not isinstance(self.version, bool)
            and self.version == SERIALIZATION_VERSION
            and isinstance(self.chain_id, str)
            and bool(self.chain_id)
            and self.transaction_type in {TRANSFER_TYPE, COINBASE_TYPE}
            and isinstance(self.sender_public_key, str)
            and isinstance(self.recipient, str)
            and is_valid_address(self.recipient)
            and isinstance(self.amount, int)
            and not isinstance(self.amount, bool)
            and self.amount > 0
            and isinstance(self.nonce, int)
            and not isinstance(self.nonce, bool)
            and self.nonce >= 0
            and isinstance(self.timestamp, int)
            and not isinstance(self.timestamp, bool)
            and self.timestamp >= 0
        )

    def sign(self, wallet: Wallet) -> None:
        """Sign this transfer with the wallet matching its public key and scheme."""
        if self.is_coinbase or not self._has_valid_fields():
            raise ValueError("transaction contains invalid transfer fields")
        if self.sender_public_key != wallet.public_key:
            raise ValueError("wallet public key does not match transaction sender")
        if self.signature_scheme != wallet.signature_scheme:
            raise ValueError("wallet signature scheme does not match transaction")
        self.tx_id = self.calculate_tx_id()
        self.signature = get_signature_scheme(self.signature_scheme).sign(
            wallet.private_key,
            self.tx_id.encode("ascii"),
        )

    def verify(
        self,
        expected_sender_address: str | None = None,
        *,
        expected_chain_id: str | None = None,
    ) -> bool:
        """Verify transfer fields, canonical ID, sender identity, scheme, and signature."""
        if self.is_coinbase or not self._has_valid_fields():
            return False
        if (
            (expected_chain_id is not None and self.chain_id != expected_chain_id)
            or not isinstance(self.tx_id, str)
            or not isinstance(self.signature, str)
            or self.tx_id != self.calculate_tx_id()
            or not self.signature
        ):
            return False
        try:
            derived_address = self.sender_address
            if expected_sender_address is not None and derived_address != expected_sender_address:
                return False
            return get_signature_scheme(self.signature_scheme).verify(
                self.sender_public_key,
                self.tx_id.encode("ascii"),
                self.signature,
            )
        except (ValueError, TypeError, WalletError):
            return False

    @classmethod
    def create(
        cls,
        wallet: Wallet,
        recipient: str,
        amount: int,
        nonce: int,
        timestamp: int | None = None,
        chain_id: str = CHAIN_ID,
    ) -> "Transaction":
        """Construct and sign a transfer for the selected chain."""
        transaction = cls(
            sender_public_key=wallet.public_key,
            recipient=recipient,
            amount=amount,
            nonce=nonce,
            timestamp=int(time.time()) if timestamp is None else timestamp,
            chain_id=chain_id,
            signature_scheme=wallet.signature_scheme,
        )
        transaction.sign(wallet)
        return transaction

    def to_dict(self) -> dict[str, object]:
        """Return all versioned transaction fields in wire/storage representation."""
        return {
            **self.unsigned_payload(),
            "signature": self.signature,
            "tx_id": self.tx_id,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: "Mapping[str, Any]") -> "Transaction":
        """Parse transaction fields, rejecting missing, extra, and wrongly typed values."""
        required_fields = {
            "version",
            "chain_id",
            "type",
            "signature_scheme",
            "sender_public_key",
            "recipient",
            "amount",
            "nonce",
            "timestamp",
            "signature",
            "tx_id",
        }
        if set(payload) != required_fields:
            raise ValueError("transaction JSON has missing or unexpected fields")
        string_fields = (
            "chain_id",
            "type",
            "signature_scheme",
            "sender_public_key",
            "recipient",
            "signature",
            "tx_id",
        )
        if not all(isinstance(payload[field], str) for field in string_fields):
            raise ValueError("transaction string fields must be strings")
        integer_fields = ("version", "amount", "nonce", "timestamp")
        if not all(
            isinstance(payload[field], int) and not isinstance(payload[field], bool)
            for field in integer_fields
        ):
            raise ValueError("transaction version, amount, nonce, and timestamp must be integers")
        supplied_tx_id = payload["tx_id"]
        transaction = cls(
            sender_public_key=payload["sender_public_key"],
            recipient=payload["recipient"],
            amount=payload["amount"],
            nonce=payload["nonce"],
            timestamp=payload["timestamp"],
            signature=payload["signature"],
            tx_id=supplied_tx_id,
            version=payload["version"],
            chain_id=payload["chain_id"],
            transaction_type=payload["type"],
            signature_scheme=payload["signature_scheme"],
        )
        if supplied_tx_id != transaction.calculate_tx_id():
            raise ValueError("transaction ID does not match its unsigned fields")
        if not transaction._has_valid_fields():
            raise ValueError("transaction fields contain an unsupported version, type, or scheme")
        return transaction

    @classmethod
    def from_json(cls, value: str) -> "Transaction":
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValueError("transaction JSON must contain an object")
        return cls.from_dict(payload)


def build_coinbase_transaction(
    recipient: str,
    timestamp: int | None = None,
    chain_id: str = CHAIN_ID,
) -> Transaction:
    """Build the unsigned, versioned block-reward transaction."""
    if not is_valid_address(recipient):
        raise ValueError("recipient must be a 40-character lowercase hexadecimal address")
    if timestamp is not None and (
        not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0
    ):
        raise ValueError("timestamp must be a nonnegative integer")
    return Transaction(
        sender_public_key=COINBASE,
        recipient=recipient,
        amount=BLOCK_REWARD,
        nonce=0,
        timestamp=int(time.time()) if timestamp is None else timestamp,
        chain_id=chain_id,
        transaction_type=COINBASE_TYPE,
    )
