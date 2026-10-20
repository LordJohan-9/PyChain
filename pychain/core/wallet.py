"""secp256k1 wallet generation, address derivation, and JSON persistence."""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pychain.config import CHAIN_ID, SECP256K1_SCHEME, SERIALIZATION_VERSION
from pychain.core.signatures import get_signature_scheme

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
        return get_signature_scheme(SECP256K1_SCHEME).derive_address(public_key_hex)
    except (ValueError, TypeError) as error:
        raise WalletError("public_key must be a valid secp256k1 public key in hex") from error


def is_valid_address(address: str) -> bool:
    """Return whether an address is exactly 40 lowercase hexadecimal characters."""
    return (
        isinstance(address, str)
        and len(address) == ADDRESS_LENGTH
        and all(character in "0123456789abcdef" for character in address)
    )


@dataclass(frozen=True)
class Wallet:
    """A versioned signing-scheme key pair for the local demo."""

    private_key: str
    public_key: str
    address: str
    signature_scheme: str = SECP256K1_SCHEME

    def __post_init__(self) -> None:
        try:
            scheme = get_signature_scheme(self.signature_scheme)
            derived_public_key = scheme.public_key_from_private(self.private_key)
            derived_address = scheme.derive_address(self.public_key)
        except (ValueError, TypeError) as error:
            raise WalletError(f"invalid {self.signature_scheme} wallet key data") from error

        if self.public_key != derived_public_key:
            raise WalletError("public_key does not match private_key")
        if self.address != derived_address:
            raise WalletError("address does not match public_key")

    @classmethod
    def generate(cls, signature_scheme: str = SECP256K1_SCHEME) -> "Wallet":
        """Generate a key pair using the requested registered signature scheme."""
        scheme = get_signature_scheme(signature_scheme)
        private_key, public_key = scheme.generate_keypair()
        return cls(
            private_key,
            public_key,
            scheme.derive_address(public_key),
            signature_scheme=signature_scheme,
        )

    def to_dict(self) -> dict[str, str | int]:
        """Return the versioned wallet fields persisted in the demo's JSON file."""
        return {
            "version": SERIALIZATION_VERSION,
            "signature_scheme": self.signature_scheme,
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
            "version",
            "signature_scheme",
            "private_key",
            "public_key",
            "address",
        }:
            raise WalletError(
                "wallet file must contain version, signature_scheme, private_key, public_key, "
                "and address"
            )
        if type(payload["version"]) is not int or payload["version"] != SERIALIZATION_VERSION:
            raise WalletError(f"unsupported wallet version {payload['version']!r}")
        if not all(
            isinstance(payload[key], str)
            for key in ("signature_scheme", "private_key", "public_key", "address")
        ):
            raise WalletError("wallet string fields must be strings")
        return cls(
            private_key=payload["private_key"],
            public_key=payload["public_key"],
            address=payload["address"],
            signature_scheme=payload["signature_scheme"],
        )

    def create_transaction(
        self,
        recipient: str,
        amount: int,
        nonce: int,
        timestamp: int | None = None,
        chain_id: str | None = None,
    ) -> "Transaction":
        """Create and sign a transfer from this wallet."""
        from pychain.core.transaction import Transaction

        return Transaction.create(
            self,
            recipient=recipient,
            amount=amount,
            nonce=nonce,
            timestamp=timestamp,
            chain_id=CHAIN_ID if chain_id is None else chain_id,
        )
