"""secp256k1 wallet generation, address derivation, and JSON persistence."""

import json
import os
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from ecdsa import (  # type: ignore[import-untyped]
    MalformedPointError,
    SECP256k1,
    SigningKey,
    VerifyingKey,
)

from pychain.core.hashing import sha256_hex

if TYPE_CHECKING:
    from pychain.core.transaction import Transaction

ADDRESS_LENGTH = 40
PUBLIC_KEY_LENGTH = 128
PRIVATE_KEY_LENGTH = 64


class WalletError(ValueError):
    """Raised when wallet data is malformed or its keys do not match."""


def address_from_public_key(public_key_hex: str) -> str:
    """Derive an address from the UTF-8 representation of a secp256k1 public key."""
    try:
        if not isinstance(public_key_hex, str):
            raise ValueError("public key must be text")
        public_key_bytes = bytes.fromhex(public_key_hex)
        if len(public_key_hex) != PUBLIC_KEY_LENGTH:
            raise ValueError("unexpected public key length")
        VerifyingKey.from_string(public_key_bytes, curve=SECP256k1, hashfunc=sha256)
    except (ValueError, TypeError, MalformedPointError) as error:
        raise WalletError("public_key must be a valid secp256k1 public key in hex") from error
    return sha256_hex(public_key_hex)[:ADDRESS_LENGTH]


def is_valid_address(address: str) -> bool:
    """Return whether an address is exactly 40 lowercase hexadecimal characters."""
    return (
        isinstance(address, str)
        and len(address) == ADDRESS_LENGTH
        and all(character in "0123456789abcdef" for character in address)
    )


@dataclass(frozen=True)
class Wallet:
    """A secp256k1 key pair stored as hexadecimal strings for the local demo."""

    private_key: str
    public_key: str
    address: str

    def __post_init__(self) -> None:
        try:
            private_key_bytes = bytes.fromhex(self.private_key)
            if len(self.private_key) != PRIVATE_KEY_LENGTH:
                raise ValueError("unexpected private key length")
            signing_key = SigningKey.from_string(
                private_key_bytes,
                curve=SECP256k1,
                hashfunc=sha256,
            )
            derived_public_key = signing_key.verifying_key.to_string().hex()
        except (ValueError, TypeError, MalformedPointError) as error:
            raise WalletError("private_key must be a valid secp256k1 private key in hex") from error

        if self.public_key != derived_public_key:
            raise WalletError("public_key does not match private_key")
        if self.address != address_from_public_key(self.public_key):
            raise WalletError("address does not match public_key")

    @classmethod
    def generate(cls) -> "Wallet":
        """Generate a new secp256k1 wallet."""
        signing_key = SigningKey.generate(curve=SECP256k1, hashfunc=sha256)
        private_key = signing_key.to_string().hex()
        public_key = signing_key.verifying_key.to_string().hex()
        return cls(private_key, public_key, address_from_public_key(public_key))

    def to_dict(self) -> dict[str, str]:
        """Return the wallet fields persisted in the demo's plain JSON file."""
        return {
            "private_key": self.private_key,
            "public_key": self.public_key,
            "address": self.address,
        }

    def save(self, path: str | Path) -> None:
        """Atomically save this wallet as UTF-8 JSON at the requested path."""
        wallet_path = Path(path)
        wallet_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=wallet_path.parent,
                prefix=f".{wallet_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(self.to_dict(), temporary_file, sort_keys=True, separators=(",", ":"))
                temporary_file.write("\n")
            os.replace(temporary_path, wallet_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: str | Path) -> "Wallet":
        """Load wallet JSON and reject malformed keys or inconsistent identity fields."""
        wallet_path = Path(path)
        try:
            payload = json.loads(wallet_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WalletError(f"could not read wallet file {wallet_path}: {error}") from error
        if not isinstance(payload, dict) or set(payload) != {
            "private_key",
            "public_key",
            "address",
        }:
            raise WalletError("wallet file must contain private_key, public_key, and address")
        if not all(isinstance(payload[key], str) for key in payload):
            raise WalletError("wallet fields must be strings")
        return cls(
            private_key=payload["private_key"],
            public_key=payload["public_key"],
            address=payload["address"],
        )

    def create_transaction(
        self,
        recipient: str,
        amount: int,
        nonce: int,
        timestamp: int | None = None,
    ) -> "Transaction":
        """Create and sign a transfer from this wallet."""
        from pychain.core.transaction import Transaction

        return Transaction.create(
            self,
            recipient=recipient,
            amount=amount,
            nonce=nonce,
            timestamp=timestamp,
        )
