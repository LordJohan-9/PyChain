from copy import deepcopy
from threading import Event

import pytest

from pychain.config import MAX_FUTURE_DRIFT
from pychain.core.block import GENESIS_BLOCK, Block
from pychain.core.chain import (
    Blockchain,
    BlockValidationError,
    ValidationError,
    chain_weight,
    compare_chains,
)
from pychain.core.hashing import merkle_root
from pychain.core.pow import mine_block
from pychain.core.transaction import Transaction, build_coinbase_transaction
from pychain.core.wallet import Wallet

TEST_NOW = GENESIS_BLOCK.timestamp


class TestConsensus:
    def propose(self, candidate, cancel_event, *, timestamp_fn, progress_fn=None):
        return candidate

    def validate(self, block):
        return True

    def finalize(self, block):
        return block

    def score(self, chain):
        return len(chain)

    def expected_difficulty(self, previous_difficulty, elapsed, *, interval, target_block_time):
        return previous_difficulty


def make_chain() -> Blockchain:
    return Blockchain(clock=lambda: TEST_NOW)


def mine_again(block: Block) -> Block:
    result = mine_block(block, Event(), timestamp_fn=lambda: float(block.timestamp))
    assert result is not None
    return result


def update_transactions(block: Block) -> None:
    block.merkle_root = merkle_root(
        [str(transaction["tx_id"]) for transaction in block.transactions]
    )
    block.hash = block.calculate_hash()


def signed_transfer(
    sender: Wallet,
    recipient: Wallet,
    *,
    amount: int = 1,
    nonce: int = 0,
) -> Transaction:
    return sender.create_transaction(
        recipient=recipient.address,
        amount=amount,
        nonce=nonce,
        timestamp=GENESIS_BLOCK.timestamp + 1,
    )


def test_happy_path_mines_and_validates_twenty_blocks() -> None:
    chain = make_chain()
    miner = Wallet.generate()

    for offset in range(1, 21):
        block = chain.mine_candidate_block(
            miner.address,
            timestamp=TEST_NOW + offset,
        )
        chain.append_block(block)

    assert chain.height == 20
    assert chain.balance_of(miner.address) == 20 * 50
    assert chain.nonce_of(miner.address) == 0
    assert chain.validate_chain() is None
    assert chain.chain[10].difficulty_bits == 17
    assert chain.chain[20].difficulty_bits == 18
    assert chain.total_work == chain_weight(chain.chain)


def test_rebuild_state_restores_balances_and_nonces() -> None:
    chain = make_chain()
    sender = Wallet.generate()
    recipient = Wallet.generate()
    block1 = chain.mine_candidate_block(
        sender.address,
        timestamp=TEST_NOW + 1,
    )
    chain.append_block(block1)
    transfer = signed_transfer(sender, recipient, amount=12, nonce=0)
    block2 = chain.mine_candidate_block(
        Wallet.generate().address,
        [transfer],
        timestamp=TEST_NOW + 2,
    )
    chain.append_block(block2)
    chain.balances = {}
    chain.nonces = {}

    assert chain.rebuild_state() is None
    assert chain.balance_of(sender.address) == 38
    assert chain.balance_of(recipient.address) == 12
    assert chain.nonce_of(sender.address) == 1


def test_append_invalid_block_raises_structured_error_without_mutating_state() -> None:
    chain = make_chain()
    miner = Wallet.generate()
    invalid = chain.create_candidate_block(miner.address)
    invalid.previous_hash = "f" * 64
    invalid.hash = invalid.calculate_hash()
    invalid = mine_again(invalid)

    with pytest.raises(BlockValidationError) as raised:
        chain.append_block(invalid)

    assert raised.value.validation_error == ValidationError(
        1,
        "previous_hash",
        "previous_hash does not match the current chain tip",
    )
    assert chain.height == 0
    assert chain.balances == {}


@pytest.mark.parametrize(
    ("mutation", "expected_rule", "remine"),
    [
        (lambda block: setattr(block, "index", 2), "index", True),
        (lambda block: setattr(block, "previous_hash", "f" * 64), "previous_hash", True),
        (lambda block: setattr(block, "nonce", block.nonce + 1), "hash", False),
        (lambda block: setattr(block, "difficulty_bits", 256), "proof_of_work", False),
        (lambda block: setattr(block, "difficulty_bits", 8), "difficulty_bits", True),
        (lambda block: setattr(block, "difficulty_bits", 257), "difficulty_bits", False),
        (
            lambda block: setattr(block, "timestamp", GENESIS_BLOCK.timestamp),
            "timestamp_order",
            True,
        ),
        (
            lambda block: setattr(
                block,
                "timestamp",
                TEST_NOW + MAX_FUTURE_DRIFT + 1,
            ),
            "timestamp_future",
            True,
        ),
        (lambda block: setattr(block, "merkle_root", "f" * 64), "merkle_root", True),
    ],
)
def test_validator_rejects_each_block_header_rule(
    mutation,
    expected_rule: str,
    remine: bool,
) -> None:
    chain = make_chain()
    miner = Wallet.generate()
    block = chain.create_candidate_block(miner.address, timestamp=TEST_NOW + 1)
    mutation(block)
    if remine:
        block.hash = block.calculate_hash()
        block = mine_again(block)
    elif expected_rule in {"proof_of_work", "difficulty_bits"}:
        block.hash = block.calculate_hash()

    error = chain.validate_chain([*chain.chain, block])

    assert error is not None
    assert error.block_index == (2 if expected_rule == "index" else 1)
    assert error.rule == expected_rule


def test_genesis_must_be_the_hard_coded_block() -> None:
    chain = make_chain()
    altered_genesis = deepcopy(GENESIS_BLOCK)
    altered_genesis.nonce += 1

    error = chain.validate_chain([altered_genesis])

    assert error is not None
    assert error.block_index == 0
    assert error.rule == "genesis"


def test_tampering_with_historical_block_is_reported_at_that_height() -> None:
    chain = make_chain()
    miner = Wallet.generate()
    for offset in (1, 2):
        chain.append_block(chain.mine_candidate_block(miner.address, timestamp=TEST_NOW + offset))
    altered_chain = deepcopy(chain.chain)
    altered_chain[1].nonce += 1

    error = chain.validate_chain(altered_chain)

    assert error is not None
    assert error.block_index == 1
    assert error.rule == "hash"


def test_block_requires_coinbase_first_and_exactly_one() -> None:
    chain = make_chain()
    sender = Wallet.generate()
    recipient = Wallet.generate()
    transfer = signed_transfer(sender, recipient)
    block = chain.create_candidate_block(
        Wallet.generate().address,
        [transfer],
        timestamp=TEST_NOW + 1,
    )
    block.transactions.reverse()
    update_transactions(block)

    error = chain.validate_chain([*chain.chain, mine_again(block)])

    assert error is not None
    assert error.rule == "coinbase_position"

    block = chain.create_candidate_block(
        Wallet.generate().address,
        timestamp=TEST_NOW + 1,
    )
    second_coinbase = build_coinbase_transaction(
        Wallet.generate().address,
        timestamp=TEST_NOW + 2,
    )
    block.transactions.append(second_coinbase.to_dict())
    update_transactions(block)

    error = chain.validate_chain([*chain.chain, mine_again(block)])

    assert error is not None
    assert error.rule == "coinbase_count"


def test_block_rejects_incorrect_coinbase_reward() -> None:
    chain = make_chain()
    miner = Wallet.generate()
    block = chain.create_candidate_block(miner.address, timestamp=TEST_NOW + 1)
    coinbase = build_coinbase_transaction(miner.address, timestamp=TEST_NOW + 1)
    coinbase.amount -= 1
    coinbase.tx_id = coinbase.calculate_tx_id()
    block.transactions[0] = coinbase.to_dict()
    update_transactions(block)

    error = chain.validate_chain([*chain.chain, mine_again(block)])

    assert error is not None
    assert error.rule == "coinbase"


def test_block_rejects_more_than_one_hundred_transactions() -> None:
    chain = make_chain()
    miner = Wallet.generate()
    block = chain.create_candidate_block(miner.address, timestamp=TEST_NOW + 1)
    block.transactions.extend(deepcopy(block.transactions[0]) for _ in range(100))
    block = mine_again(block)

    error = chain.validate_chain([*chain.chain, block])

    assert error is not None
    assert error.rule == "transaction_count"


def test_block_rejects_repeated_transaction_ids() -> None:
    chain = make_chain()
    sender = Wallet.generate()
    recipient = Wallet.generate()
    transaction = signed_transfer(sender, recipient)
    block = chain.create_candidate_block(
        Wallet.generate().address,
        [transaction, transaction],
        timestamp=TEST_NOW + 1,
    )
    block = mine_again(block)

    error = chain.validate_chain([*chain.chain, block])

    assert error is not None
    assert error.rule == "duplicate_transaction"


def test_block_rejects_invalid_transaction_signature() -> None:
    chain = make_chain()
    sender = Wallet.generate()
    transaction = signed_transfer(sender, Wallet.generate())
    transaction.signature = "00" * 64
    block = chain.create_candidate_block(
        Wallet.generate().address,
        [transaction],
        timestamp=TEST_NOW + 1,
    )
    block = mine_again(block)

    error = chain.validate_chain([*chain.chain, block])

    assert error is not None
    assert error.rule == "signature"


def test_block_rejects_malformed_sender_public_key() -> None:
    chain = make_chain()
    sender = Wallet.generate()
    transaction = signed_transfer(sender, Wallet.generate())
    transaction.sender_public_key = "0" * 128
    transaction.tx_id = transaction.calculate_tx_id()
    block = chain.create_candidate_block(
        Wallet.generate().address,
        timestamp=TEST_NOW + 1,
    )
    block.transactions.append(transaction.to_dict())
    update_transactions(block)

    error = chain.validate_chain([*chain.chain, mine_again(block)])

    assert error is not None
    assert error.rule == "sender_public_key"


def test_block_rejects_overspending_and_double_spending() -> None:
    chain = make_chain()
    poor_sender = Wallet.generate()
    overspend = signed_transfer(poor_sender, Wallet.generate(), amount=1)
    block = chain.create_candidate_block(
        Wallet.generate().address,
        [overspend],
        timestamp=TEST_NOW + 1,
    )
    block = mine_again(block)
    error = chain.validate_chain([*chain.chain, block])
    assert error is not None
    assert error.rule == "balance"

    chain = make_chain()
    funded_sender = Wallet.generate()
    chain.append_block(
        chain.mine_candidate_block(
            funded_sender.address,
            timestamp=TEST_NOW + 1,
        )
    )
    first = signed_transfer(funded_sender, Wallet.generate(), amount=40, nonce=0)
    second = signed_transfer(funded_sender, Wallet.generate(), amount=40, nonce=0)
    block = chain.create_candidate_block(
        Wallet.generate().address,
        [first, second],
        timestamp=TEST_NOW + 2,
    )
    block = mine_again(block)

    error = chain.validate_chain([*chain.chain, block])

    assert error is not None
    assert error.rule == "nonce"


def test_chain_rejects_transaction_signed_for_another_chain_id() -> None:
    chain = make_chain()
    sender = Wallet.generate()
    recipient = Wallet.generate()
    chain.append_block(chain.mine_candidate_block(sender.address, timestamp=TEST_NOW + 1))
    replay = sender.create_transaction(
        recipient.address,
        1,
        nonce=0,
        timestamp=TEST_NOW + 2,
        chain_id="another-network",
    )
    block = chain.mine_candidate_block(
        Wallet.generate().address,
        [replay],
        timestamp=TEST_NOW + 2,
    )

    error = chain.validate_chain([*chain.chain, block])

    assert error is not None
    assert error.rule == "transaction_chain_id"


def test_block_rejects_replayed_nonce_and_already_confirmed_transaction() -> None:
    chain = make_chain()
    sender = Wallet.generate()
    recipient = Wallet.generate()
    chain.append_block(
        chain.mine_candidate_block(
            sender.address,
            timestamp=TEST_NOW + 1,
        )
    )
    transfer = signed_transfer(sender, recipient, amount=1, nonce=0)
    first_transfer_block = chain.mine_candidate_block(
        Wallet.generate().address,
        [transfer],
        timestamp=TEST_NOW + 2,
    )
    chain.append_block(first_transfer_block)

    replay = chain.create_candidate_block(
        Wallet.generate().address,
        [transfer],
        timestamp=TEST_NOW + 3,
    )
    replay = mine_again(replay)
    error = chain.validate_chain([*chain.chain, replay])

    assert error is not None
    assert error.rule == "duplicate_transaction"

    replayed_nonce = signed_transfer(sender, Wallet.generate(), amount=1, nonce=0)
    replayed_nonce_block = chain.create_candidate_block(
        Wallet.generate().address,
        [replayed_nonce],
        timestamp=TEST_NOW + 3,
    )
    replayed_nonce_block = mine_again(replayed_nonce_block)
    error = chain.validate_chain([*chain.chain, replayed_nonce_block])

    assert error is not None
    assert error.rule == "nonce"


def test_invalid_chain_is_rejected_during_construction() -> None:
    invalid_genesis = deepcopy(GENESIS_BLOCK)
    invalid_genesis.timestamp += 1

    with pytest.raises(BlockValidationError) as raised:
        Blockchain([invalid_genesis], clock=lambda: TEST_NOW)

    assert raised.value.validation_error.rule == "genesis"


def test_fork_choice_uses_cumulative_work_and_keeps_current_on_ties() -> None:
    current = [GENESIS_BLOCK]
    equal_weight = [GENESIS_BLOCK]
    heavier = [
        GENESIS_BLOCK,
        Block(
            index=1,
            timestamp=GENESIS_BLOCK.timestamp + 1,
            previous_hash=GENESIS_BLOCK.hash,
            merkle_root=merkle_root([]),
            difficulty_bits=17,
        ),
    ]

    assert not compare_chains(current, equal_weight)
    assert compare_chains(current, heavier)
    assert chain_weight(heavier) > chain_weight(current)


def test_consensus_engine_can_be_swapped_without_changing_ledger() -> None:
    chain = Blockchain(clock=lambda: TEST_NOW, consensus=TestConsensus())
    block = chain.mine_candidate_block(Wallet.generate().address, timestamp=TEST_NOW + 1)

    chain.append_block(block)

    assert chain.height == 1
    assert chain.total_work == 2
    assert chain.validate_chain() is None
