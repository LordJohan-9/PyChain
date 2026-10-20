import json

import pytest

from pychain.core.block import GENESIS_BLOCK
from pychain.core.chain import Blockchain
from pychain.core.wallet import Wallet
from pychain.node.storage import JsonStorage, PersistenceError

TEST_NOW = GENESIS_BLOCK.timestamp + 10_000


def test_chain_persists_and_recovers_balances_and_nonce(tmp_path) -> None:
    chain = Blockchain(clock=lambda: TEST_NOW)
    sender = Wallet.generate()
    recipient = Wallet.generate()
    chain.append_block(chain.mine_candidate_block(sender.address, timestamp=TEST_NOW + 1))
    transfer = sender.create_transaction(
        recipient.address,
        11,
        nonce=0,
        timestamp=TEST_NOW + 2,
    )
    chain.append_block(
        chain.mine_candidate_block(
            Wallet.generate().address,
            [transfer],
            timestamp=TEST_NOW + 2,
        )
    )
    storage = JsonStorage(tmp_path / "data" / "node1")

    storage.save_chain(chain)
    stored_payload = json.loads(storage.chain_path.read_text(encoding="utf-8"))
    assert stored_payload["version"] == 1
    assert stored_payload["blocks"][0]["version"] == 1
    recovered = storage.load_chain(clock=lambda: TEST_NOW + 100)

    assert [block.to_dict() for block in recovered.chain] == [
        block.to_dict() for block in chain.chain
    ]
    assert recovered.balance_of(sender.address) == 39
    assert recovered.balance_of(recipient.address) == 11
    assert recovered.nonce_of(sender.address) == 1


def test_storage_refuses_hand_edited_block_and_names_the_failed_rule(tmp_path) -> None:
    chain = Blockchain(clock=lambda: TEST_NOW)
    chain.append_block(
        chain.mine_candidate_block(Wallet.generate().address, timestamp=TEST_NOW + 1)
    )
    storage = JsonStorage(tmp_path)
    storage.save_chain(chain)
    payload = json.loads(storage.chain_path.read_text(encoding="utf-8"))
    payload["blocks"][1]["nonce"] += 1
    storage.chain_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PersistenceError, match="block 1 rule hash"):
        storage.load_chain(clock=lambda: TEST_NOW + 100)


def test_storage_rejects_malformed_json_and_block_shape(tmp_path) -> None:
    storage = JsonStorage(tmp_path)
    storage.chain_path.parent.mkdir(parents=True, exist_ok=True)
    storage.chain_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(PersistenceError, match="could not read"):
        storage.load_chain()

    storage.chain_path.write_text(
        json.dumps({"version": 1, "blocks": [{"index": 0}]}),
        encoding="utf-8",
    )
    with pytest.raises(PersistenceError, match="invalid block at array position 0"):
        storage.load_chain()


def test_peer_list_is_atomically_persisted_and_missing_is_empty(tmp_path) -> None:
    storage = JsonStorage(tmp_path)

    assert storage.load_peers() == []
    storage.save_peers(["http://localhost:8001", "http://localhost:8002"])
    payload = json.loads(storage.peers_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1

    assert storage.load_peers() == [
        "http://localhost:8001",
        "http://localhost:8002",
    ]


def test_genesis_only_chain_recovers(tmp_path) -> None:
    storage = JsonStorage(tmp_path)
    storage.save_chain([GENESIS_BLOCK])

    assert storage.load_chain(clock=lambda: TEST_NOW).chain[0].hash == GENESIS_BLOCK.hash


def test_storage_rejects_unsupported_file_version(tmp_path) -> None:
    storage = JsonStorage(tmp_path)
    storage.chain_path.parent.mkdir(parents=True, exist_ok=True)
    storage.chain_path.write_text(
        json.dumps({"version": 2, "blocks": []}),
        encoding="utf-8",
    )

    with pytest.raises(PersistenceError, match="unsupported chain file version"):
        storage.load_chain()
