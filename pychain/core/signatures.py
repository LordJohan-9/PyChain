"""Pluggable key, address, and transaction-signature schemes."""

from hashlib import sha256
from typing import Protocol

from ecdsa import (  # type: ignore[import-untyped]
    BadSignatureError,
    MalformedPointError,
    SECP256k1,
    SigningKey,
    VerifyingKey,
)
from ecdsa.util import sigdecode_string, sigencode_string  # type: ignore[import-untyped]

from pychain.core.hashing import sha256_hex


class SignatureScheme(Protocol):
    """Operations required by wallets and signed transactions."""

    name: str

    def generate_keypair(self) -> tuple[str, str]: ...

    def public_key_from_private(self, private_key: str) -> str: ...

    def derive_address(self, public_key: str) -> str: ...

    def sign(self, private_key: str, message: bytes) -> str: ...

    def verify(self, public_key: str, message: bytes, signature: str) -> bool: ...


class Secp256k1Scheme:
    """ECDSA over secp256k1 with deterministic signatures."""

    name = "secp256k1"

    def generate_keypair(self) -> tuple[str, str]:
        signing_key = SigningKey.generate(curve=SECP256k1, hashfunc=sha256)
        return signing_key.to_string().hex(), signing_key.verifying_key.to_string().hex()

    def public_key_from_private(self, private_key: str) -> str:
        return str(
            SigningKey.from_string(
                bytes.fromhex(private_key),
                curve=SECP256k1,
                hashfunc=sha256,
            )
            .verifying_key.to_string()
            .hex()
        )

    def derive_address(self, public_key: str) -> str:
        try:
            public_key_bytes = bytes.fromhex(public_key)
            if len(public_key) != 128:
                raise ValueError("unexpected public key length")
            VerifyingKey.from_string(public_key_bytes, curve=SECP256k1, hashfunc=sha256)
        except (ValueError, TypeError, MalformedPointError) as error:
            raise ValueError("public_key must be a valid secp256k1 public key in hex") from error
        return sha256_hex(public_key)[:40]

    def sign(self, private_key: str, message: bytes) -> str:
        signing_key = SigningKey.from_string(
            bytes.fromhex(private_key),
            curve=SECP256k1,
            hashfunc=sha256,
        )
        return str(
            signing_key.sign_deterministic(
                message,
                hashfunc=sha256,
                sigencode=sigencode_string,
            ).hex()
        )

    def verify(self, public_key: str, message: bytes, signature: str) -> bool:
        try:
            verifying_key = VerifyingKey.from_string(
                bytes.fromhex(public_key),
                curve=SECP256k1,
                hashfunc=sha256,
            )
            return bool(
                verifying_key.verify(
                    bytes.fromhex(signature),
                    message,
                    hashfunc=sha256,
                    sigdecode=sigdecode_string,
                )
            )
        except (BadSignatureError, ValueError, TypeError, MalformedPointError):
            return False


_SCHEMES: dict[str, SignatureScheme] = {}


def register_signature_scheme(scheme: SignatureScheme) -> None:
    """Register a scheme implementation by its stable protocol identifier."""
    if not scheme.name or scheme.name in _SCHEMES:
        raise ValueError(f"signature scheme {scheme.name!r} is already registered or invalid")
    _SCHEMES[scheme.name] = scheme


def get_signature_scheme(name: str) -> SignatureScheme:
    """Return a registered scheme or raise for an unsupported identifier."""
    try:
        return _SCHEMES[name]
    except KeyError as error:
        raise ValueError(f"unsupported signature scheme {name!r}") from error


register_signature_scheme(Secp256k1Scheme())
