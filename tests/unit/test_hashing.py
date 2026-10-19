import hashlib

import pytest

from pychain.core.hashing import canonical_json, merkle_root, sha256_hex


def test_canonical_json_sorts_keys_and_removes_whitespace() -> None:
    assert canonical_json({"z": 1, "a": {"y": 2, "x": 3}}) == '{"a":{"x":3,"y":2},"z":1}'


def test_canonical_json_preserves_unicode_as_utf8_text() -> None:
    assert canonical_json({"greeting": "café"}) == '{"greeting":"café"}'


def test_canonical_json_rejects_nonstandard_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json({"value": float("nan")})


def test_hash_is_independent_of_dictionary_key_order() -> None:
    first = {"sender": "alice", "details": {"amount": 5, "recipient": "bob"}}
    reordered = {"details": {"recipient": "bob", "amount": 5}, "sender": "alice"}

    assert sha256_hex(canonical_json(first)) == sha256_hex(canonical_json(reordered))


def test_sha256_hex_matches_standard_vector() -> None:
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_hex("abc") == sha256_hex(b"abc")


def test_merkle_root_of_empty_list_is_hash_of_empty_bytes() -> None:
    assert merkle_root([]) == hashlib.sha256(b"").hexdigest()


def test_merkle_root_of_one_transaction_is_its_id() -> None:
    assert merkle_root(["tx-a"]) == "tx-a"


def test_merkle_root_of_two_transactions_hashes_the_pair() -> None:
    expected = hashlib.sha256(b"tx-atx-b").hexdigest()

    assert merkle_root(["tx-a", "tx-b"]) == expected


def test_merkle_root_of_three_duplicates_odd_leaf() -> None:
    left = hashlib.sha256(b"tx-atx-b").hexdigest()
    right = hashlib.sha256(b"tx-ctx-c").hexdigest()
    expected = hashlib.sha256((left + right).encode("utf-8")).hexdigest()

    assert merkle_root(["tx-a", "tx-b", "tx-c"]) == expected
