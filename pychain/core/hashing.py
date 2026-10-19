"""Canonical serialization and hashing helpers shared by blockchain data types."""

import hashlib
import json
from collections.abc import Sequence
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize a JSON value with stable key ordering and no insignificant whitespace."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(data: bytes | str) -> str:
    """Return the SHA-256 digest of bytes or UTF-8 encoded text as lowercase hex."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def merkle_root(tx_ids: Sequence[str]) -> str:
    """Build a Merkle root from transaction IDs, duplicating an odd final node."""
    if not tx_ids:
        return sha256_hex(b"")

    level = list(tx_ids)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            sha256_hex((level[index] + level[index + 1]).encode("utf-8"))
            for index in range(0, len(level), 2)
        ]
    return level[0]
