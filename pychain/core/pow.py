"""Proof-of-work validation, mining, and difficulty adjustment."""

import time
from collections.abc import Callable
from dataclasses import replace
from threading import Event

from pychain.config import (
    MAX_DIFFICULTY_BITS,
    MIN_DIFFICULTY_BITS,
    RETARGET_INTERVAL,
    TARGET_BLOCK_TIME,
)
from pychain.core.block import Block
from pychain.core.hashing import canonical_json, sha256_hex


def meets_target(block_hash: str, difficulty_bits: int) -> bool:
    """Return whether a 256-bit hexadecimal hash is below the difficulty target."""
    if not 0 <= difficulty_bits <= 256:
        raise ValueError("difficulty_bits must be between 0 and 256")
    if len(block_hash) != 64 or any(
        character not in "0123456789abcdefABCDEF" for character in block_hash
    ):
        raise ValueError("block_hash must be a 64-character hexadecimal SHA-256 digest")
    hash_value: int = int.from_bytes(bytes.fromhex(block_hash), byteorder="big")
    target: int = 1 << (256 - difficulty_bits)
    return bool(hash_value < target)


def mine_block(
    block: Block,
    cancel_event: Event,
    *,
    timestamp_fn: Callable[[], float] = time.time,
    timestamp_refresh_interval: int = 1_000,
    progress_fn: Callable[[int, int], None] | None = None,
) -> Block | None:
    """Mine a copy of a block, returning None if cancellation is requested."""
    if timestamp_refresh_interval < 1:
        raise ValueError("timestamp_refresh_interval must be positive")

    candidate = replace(block, hash="")
    attempts = 0

    def nonce_template() -> tuple[bytes, bytes]:
        serialized = canonical_json(candidate.header_dict())
        nonce_marker = '"nonce":'
        nonce_start = serialized.index(nonce_marker) + len(nonce_marker)
        nonce_end = nonce_start + len(str(candidate.nonce))
        return serialized[:nonce_start].encode("utf-8"), serialized[nonce_end:].encode("utf-8")

    prefix, suffix = nonce_template()
    if progress_fn is not None:
        progress_fn(candidate.nonce, attempts)
    while not cancel_event.is_set():
        if attempts and attempts % timestamp_refresh_interval == 0:
            candidate.timestamp = max(candidate.timestamp, int(timestamp_fn()))
            prefix, suffix = nonce_template()
        candidate.hash = sha256_hex(prefix + str(candidate.nonce).encode("ascii") + suffix)
        if meets_target(candidate.hash, candidate.difficulty_bits):
            return candidate
        candidate.nonce += 1
        attempts += 1
        if progress_fn is not None and attempts % 256 == 0:
            progress_fn(candidate.nonce, attempts)
    return None


def retarget_difficulty(
    current_bits: int,
    elapsed_seconds: float,
    *,
    interval: int = RETARGET_INTERVAL,
    target_block_time: int = TARGET_BLOCK_TIME,
) -> int:
    """Adjust difficulty by one bit when an interval is less than half or over twice target."""
    if not MIN_DIFFICULTY_BITS <= current_bits <= MAX_DIFFICULTY_BITS:
        raise ValueError(
            f"current_bits must be between {MIN_DIFFICULTY_BITS} and {MAX_DIFFICULTY_BITS}"
        )
    if elapsed_seconds < 0:
        raise ValueError("elapsed_seconds cannot be negative")
    if interval < 1 or target_block_time < 1:
        raise ValueError("interval and target_block_time must be positive")

    expected_seconds = interval * target_block_time
    if elapsed_seconds < expected_seconds / 2:
        return min(current_bits + 1, MAX_DIFFICULTY_BITS)
    if elapsed_seconds > expected_seconds * 2:
        return max(current_bits - 1, MIN_DIFFICULTY_BITS)
    return current_bits
