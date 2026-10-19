from dataclasses import fields
from threading import Event

import pytest

from pychain.config import INITIAL_DIFFICULTY_BITS
from pychain.core.block import GENESIS_BLOCK, GENESIS_HASH, Block
from pychain.core.hashing import merkle_root
from pychain.core.pow import meets_target, mine_block, retarget_difficulty


def make_block(
    *,
    index: int = 1,
    timestamp: int = 1_700_000_010,
    previous_hash: str = GENESIS_BLOCK.hash,
    merkle_root_value: str = merkle_root([]),
    difficulty_bits: int = 8,
    nonce: int = 0,
    transactions: list[dict[str, object]] | None = None,
) -> Block:
    return Block(
        index=index,
        timestamp=timestamp,
        previous_hash=previous_hash,
        merkle_root=merkle_root_value,
        difficulty_bits=difficulty_bits,
        nonce=nonce,
        transactions=transactions if transactions is not None else [],
    )


def test_genesis_block_is_fixed_and_meets_initial_difficulty() -> None:
    assert GENESIS_BLOCK.hash == GENESIS_HASH
    assert GENESIS_BLOCK.index == 0
    assert GENESIS_BLOCK.previous_hash == "0" * 64
    assert GENESIS_BLOCK.transactions == []
    assert GENESIS_BLOCK.has_valid_hash()
    assert meets_target(GENESIS_BLOCK.hash, INITIAL_DIFFICULTY_BITS)


def test_block_hash_is_deterministic_and_only_covers_header() -> None:
    block = make_block()
    same_block = make_block()

    assert block.hash == same_block.hash
    block.transactions.append({"tx_id": "not-in-header"})
    assert block.calculate_hash() == same_block.hash


@pytest.mark.parametrize(
    ("field_name", "new_value"),
    [
        ("index", 2),
        ("timestamp", 1_700_000_011),
        ("previous_hash", "1" * 64),
        ("merkle_root", "2" * 64),
        ("difficulty_bits", 9),
        ("nonce", 1),
    ],
)
def test_changing_any_header_field_invalidates_stored_hash(
    field_name: str,
    new_value: int | str,
) -> None:
    block = make_block()
    setattr(block, field_name, new_value)

    assert not block.has_valid_hash()


def test_block_serialization_includes_hash_and_transactions() -> None:
    block = make_block(transactions=[{"tx_id": "tx-1"}])

    assert block.to_dict()["hash"] == block.hash
    assert block.to_dict()["transactions"] == [{"tx_id": "tx-1"}]


def test_mined_block_meets_target_and_is_validly_hashed() -> None:
    result = mine_block(make_block(difficulty_bits=INITIAL_DIFFICULTY_BITS), Event())

    assert result is not None
    assert result.has_valid_hash()
    assert meets_target(result.hash, result.difficulty_bits)


def test_mining_returns_none_when_already_cancelled() -> None:
    cancel = Event()
    cancel.set()

    assert mine_block(make_block(), cancel) is None


@pytest.mark.parametrize("block_hash", ["not-a-hash", "0" * 63])
def test_meets_target_rejects_malformed_hash(block_hash: str) -> None:
    with pytest.raises(ValueError):
        meets_target(block_hash, 8)


@pytest.mark.parametrize("bits", [-1, 257])
def test_meets_target_rejects_out_of_range_difficulty(bits: int) -> None:
    with pytest.raises(ValueError):
        meets_target("0" * 64, bits)


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(49, 17), (50, 16), (100, 16), (200, 16), (201, 15)],
)
def test_retarget_adjusts_only_outside_strict_half_to_double_window(
    elapsed: float,
    expected: int,
) -> None:
    assert retarget_difficulty(16, elapsed) == expected


def test_retarget_respects_difficulty_bounds() -> None:
    assert retarget_difficulty(40, 0) == 40
    assert retarget_difficulty(8, 201) == 8


@pytest.mark.parametrize(
    ("bits", "elapsed", "kwargs"),
    [
        (7, 100, {}),
        (41, 100, {}),
        (16, -1, {}),
        (16, 100, {"interval": 0}),
        (16, 100, {"target_block_time": 0}),
    ],
)
def test_retarget_rejects_invalid_inputs(
    bits: int,
    elapsed: float,
    kwargs: dict[str, int],
) -> None:
    with pytest.raises(ValueError):
        retarget_difficulty(bits, elapsed, **kwargs)


def test_header_fields_are_explicitly_defined() -> None:
    assert [item.name for item in fields(Block)][:6] == [
        "index",
        "timestamp",
        "previous_hash",
        "merkle_root",
        "difficulty_bits",
        "nonce",
    ]
