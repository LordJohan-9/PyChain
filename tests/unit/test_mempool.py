import pytest

from pychain.core.chain import Blockchain
from pychain.core.mempool import Mempool, MempoolError
from pychain.core.transaction import build_coinbase_transaction
from pychain.core.wallet import Wallet


def test_pending_transactions_require_confirmed_funds_and_sequential_nonces() -> None:
    chain = Blockchain()
    sender = Wallet.generate()
    recipient = Wallet.generate()
    pool = Mempool(chain)

    first = sender.create_transaction(recipient.address, 30, nonce=0, timestamp=1)
    second = sender.create_transaction(recipient.address, 15, nonce=1, timestamp=2)
    third = sender.create_transaction(recipient.address, 6, nonce=2, timestamp=3)

    with pytest.raises(MempoolError, match="available balance 0"):
        pool.add(first)

    chain.append_block(chain.mine_candidate_block(sender.address, timestamp=1_700_000_001))
    assert pool.add(first)
    assert pool.add(second)
    with pytest.raises(MempoolError, match="available balance 5"):
        pool.add(third)

    skipped_nonce = sender.create_transaction(recipient.address, 1, nonce=3, timestamp=4)
    with pytest.raises(MempoolError, match="expected nonce 2"):
        pool.add(skipped_nonce)


def test_pool_rejects_duplicate_confirmed_coinbase_and_invalid_transactions() -> None:
    chain = Blockchain()
    sender = Wallet.generate()
    recipient = Wallet.generate()
    chain.append_block(chain.mine_candidate_block(sender.address, timestamp=1_700_000_001))
    pool = Mempool(chain)
    transaction = sender.create_transaction(recipient.address, 5, nonce=0, timestamp=1)

    assert pool.add(transaction)
    assert not pool.add(transaction)

    invalid = sender.create_transaction(recipient.address, 5, nonce=1, timestamp=2)
    invalid.signature = "00" * 64
    with pytest.raises(MempoolError, match="invalid"):
        pool.add(invalid)

    with pytest.raises(MempoolError, match="coinbase"):
        pool.add(build_coinbase_transaction(sender.address))

    confirmed = chain.mine_candidate_block(
        Wallet.generate().address,
        [transaction],
        timestamp=chain.tip.timestamp + 1,
    )
    chain.append_block(confirmed)
    pool.remove_included([transaction])
    with pytest.raises(MempoolError, match="already confirmed"):
        pool.add(transaction)


def test_included_transaction_removal_revalidates_remaining_pool() -> None:
    chain = Blockchain()
    sender = Wallet.generate()
    recipient = Wallet.generate()
    chain.append_block(chain.mine_candidate_block(sender.address))
    pool = Mempool(chain)
    first = sender.create_transaction(recipient.address, 20, nonce=0, timestamp=1)
    second = sender.create_transaction(recipient.address, 20, nonce=1, timestamp=2)
    pool.add(first)
    pool.add(second)

    included_block = chain.mine_candidate_block(
        Wallet.generate().address,
        [first],
        timestamp=chain.tip.timestamp + 1,
    )
    chain.append_block(included_block)
    pool.remove_included([first])

    assert pool.transactions == [second]
