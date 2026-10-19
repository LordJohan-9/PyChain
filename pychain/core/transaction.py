"""Signed account-based transfers and coinbase transaction construction."""

import json
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from ecdsa import (  # type: ignore[import-untyped]
    BadSignatureError,
    MalformedPointError,
    SECP256k1,
    SigningKey,
    VerifyingKey,
)
from ecdsa.util import sigdecode_string, sigencode_string  # type: ignore[import-untyped]

from pychain.config import BLOCK_REWARD
from pychain.core.hashing import canonical_json, sha256_hex
from pychain.core.wallet import Wallet, WalletError, address_from_public_key, is_valid_address

if TYPE_CHECKING:
    from collections.abc import Mapping

COINBASE = "COINBASE"


@dataclass
class Transaction:
    """A transfer whose ID commits to its unsigned fields and whose signature commits to its ID."""

    sender_public_key: str
    recipient: str
    amount: int
    nonce: int
    timestamp: int
    signature: str = ""
    tx_id: str = ""

    def __post_init__(self) -> None:
        if not self.tx_id:
            self.tx_id = self.calculate_tx_id()

    @property
    def is_coinbase(self) -> bool:
        """Whether this transaction is the protocol's unsigned mining reward."""
        return self.sender_public_key == COINBASE

    @property
    def sender_address(self) -> str:
        """Derive the sender address from the included public key."""
        return address_from_public_key(self.sender_public_key)

    def unsigned_payload(self) -> dict[str, int | str]:
        """Return exactly the fields hashed to create the transaction ID."""
        return {
            "sender_public_key": self.sender_public_key,
            "recipient": self.recipient,
            "amount": self.amount,
            "nonce": self.nonce,
            "timestamp": self.timestamp,
        }

    def calculate_tx_id(self) -> str:
        """Recompute the transaction ID from canonical unsigned fields."""
        return sha256_hex(canonical_json(self.unsigned_payload()))

    def _has_valid_fields(self) -> bool:
        return (
            isinstance(self.sender_public_key, str)
            and self.sender_public_key != COINBASE
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
        """Sign this transfer with the wallet matching its sender public key."""
        if not self._has_valid_fields():
            raise ValueError("transaction contains invalid transfer fields")
        if self.sender_public_key != wallet.public_key:
            raise ValueError("wallet public key does not match transaction sender")
        self.tx_id = self.calculate_tx_id()
        signing_key = SigningKey.from_string(
            bytes.fromhex(wallet.private_key),
            curve=SECP256k1,
            hashfunc=sha256,
        )
        self.signature = signing_key.sign_deterministic(
            self.tx_id.encode("ascii"),
            hashfunc=sha256,
            sigencode=sigencode_string,
        ).hex()

    def verify(self, expected_sender_address: str | None = None) -> bool:
        """Verify fields, transaction ID, sender identity, and ECDSA signature."""
        if not self._has_valid_fields():
            return False
        if (
            not isinstance(self.tx_id, str)
            or not isinstance(self.signature, str)
            or self.tx_id != self.calculate_tx_id()
            or not self.signature
        ):
            return False
        try:
            derived_address = address_from_public_key(self.sender_public_key)
            if expected_sender_address is not None and derived_address != expected_sender_address:
                return False
            verifying_key = VerifyingKey.from_string(
                bytes.fromhex(self.sender_public_key),
                curve=SECP256k1,
                hashfunc=sha256,
            )
            verifying_key.verify(
                bytes.fromhex(self.signature),
                self.tx_id.encode("ascii"),
                hashfunc=sha256,
                sigdecode=sigdecode_string,
            )
        except (BadSignatureError, ValueError, TypeError, WalletError, MalformedPointError):
            return False
        return True

    @classmethod
    def create(
        cls,
        wallet: Wallet,
        recipient: str,
        amount: int,
        nonce: int,
        timestamp: int | None = None,
    ) -> "Transaction":
        """Construct and sign a transfer, using the current UTC epoch second by default."""
        transaction = cls(
            sender_public_key=wallet.public_key,
            recipient=recipient,
            amount=amount,
            nonce=nonce,
            timestamp=int(time.time()) if timestamp is None else timestamp,
        )
        transaction.sign(wallet)
        return transaction

    def to_dict(self) -> dict[str, object]:
        """Return all signed transaction fields in the wire/storage representation."""
        return {
            **self.unsigned_payload(),
            "signature": self.signature,
            "tx_id": self.tx_id,
        }

    def to_json(self) -> str:
        """Serialize this transaction as canonical JSON."""
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: "Mapping[str, Any]") -> "Transaction":
        """Parse transaction fields, rejecting missing, extra, or wrongly typed values."""
        required_fields = {
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
        if not all(
            isinstance(payload[field], str)
            for field in ("sender_public_key", "recipient", "signature", "tx_id")
        ):
            raise ValueError("transaction string fields must be strings")
        if not all(
            isinstance(payload[field], int) and not isinstance(payload[field], bool)
            for field in ("amount", "nonce", "timestamp")
        ):
            raise ValueError("transaction amount, nonce, and timestamp must be integers")
        supplied_tx_id = payload["tx_id"]
        transaction = cls(
            sender_public_key=payload["sender_public_key"],
            recipient=payload["recipient"],
            amount=payload["amount"],
            nonce=payload["nonce"],
            timestamp=payload["timestamp"],
            signature=payload["signature"],
            tx_id=supplied_tx_id,
        )
        if supplied_tx_id != transaction.calculate_tx_id():
            raise ValueError("transaction ID does not match its unsigned fields")
        return transaction

    @classmethod
    def from_json(cls, value: str) -> "Transaction":
        """Parse JSON and validate its top-level shape before constructing a transaction."""
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValueError("transaction JSON must contain an object")
        return cls.from_dict(payload)


def build_coinbase_transaction(recipient: str, timestamp: int | None = None) -> Transaction:
    """Build the unsigned block-reward transaction for the configured reward amount."""
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
    )
