"""Account-based blockchain state, validation, and cumulative-work fork choice."""

import time
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from threading import Event
from typing import TypeAlias

from pychain.config import (
    BLOCK_REWARD,
    CHAIN_ID,
    MAX_FUTURE_DRIFT,
    MAX_TX_PER_BLOCK,
    RETARGET_INTERVAL,
    TARGET_BLOCK_TIME,
)
from pychain.core.block import GENESIS_BLOCK, Block
from pychain.core.consensus import DEFAULT_CONSENSUS, ConsensusEngine
from pychain.core.hashing import merkle_root
from pychain.core.transaction import COINBASE, Transaction, build_coinbase_transaction
from pychain.core.wallet import WalletError, is_valid_address

Clock: TypeAlias = Callable[[], float]


@dataclass(frozen=True)
class ValidationError:
    """The first rule violation encountered while validating a chain."""

    block_index: int
    rule: str
    message: str


class BlockValidationError(ValueError):
    """Raised when a proposed block fails validation."""

    def __init__(self, error: ValidationError) -> None:
        self.validation_error = error
        super().__init__(f"block {error.block_index} failed {error.rule}: {error.message}")


def chain_weight(chain: Sequence[Block]) -> int:
    """Return cumulative proof-of-work, summing 2**difficulty_bits per block."""
    return sum(1 << block.difficulty_bits for block in chain)


def compare_chains(current: Sequence[Block], candidate: Sequence[Block]) -> bool:
    """Return true only when the candidate has strictly more work than the current chain."""
    return chain_weight(candidate) > chain_weight(current)


def apply_transaction(
    transaction: Transaction,
    balances: dict[str, int],
    nonces: dict[str, int],
    *,
    block_index: int,
    chain_id: str,
    allow_coinbase: bool = False,
) -> ValidationError | None:
    """Validate and apply one transaction; this is the sole confirmed-state mutation path."""
    if (
        not transaction._has_valid_fields()
        or transaction.version != GENESIS_BLOCK.version
        or transaction.chain_id != chain_id
    ):
        return ValidationError(
            block_index,
            "transaction_chain_id",
            "transaction version or chain_id does not match the ledger",
        )
    if transaction.is_coinbase:
        if not allow_coinbase:
            return ValidationError(
                block_index,
                "coinbase_count",
                "coinbase transactions are only permitted at position zero",
            )
        if (
            transaction.transaction_type != "coinbase"
            or transaction.sender_public_key != COINBASE
            or transaction.amount != BLOCK_REWARD
            or not is_valid_address(transaction.recipient)
            or transaction.signature != ""
            or transaction.nonce != 0
        ):
            return ValidationError(
                block_index,
                "coinbase",
                "coinbase must be unsigned, pay exactly the block reward, "
                "and have a valid recipient",
            )
        balances[transaction.recipient] = (
            balances.get(transaction.recipient, 0) + transaction.amount
        )
        return None

    if transaction.transaction_type != "transfer":
        return ValidationError(block_index, "transaction_type", "unsupported transaction type")
    try:
        sender = transaction.sender_address
    except WalletError as error:
        return ValidationError(block_index, "sender_public_key", str(error))
    if not transaction.verify(expected_sender_address=sender, expected_chain_id=chain_id):
        return ValidationError(
            block_index,
            "signature",
            f"transaction {transaction.tx_id} has an invalid signature or sender identity",
        )
    expected_nonce = nonces.get(sender, 0)
    if transaction.nonce != expected_nonce:
        return ValidationError(
            block_index,
            "nonce",
            f"sender {sender} expected nonce {expected_nonce}, got {transaction.nonce}",
        )
    sender_balance = balances.get(sender, 0)
    if sender_balance < transaction.amount:
        return ValidationError(
            block_index,
            "balance",
            f"sender {sender} has balance {sender_balance}, needs {transaction.amount}",
        )
    balances[sender] = sender_balance - transaction.amount
    balances[transaction.recipient] = balances.get(transaction.recipient, 0) + transaction.amount
    nonces[sender] = expected_nonce + 1
    return None


class Blockchain:
    """A validated block sequence with account balances and next transaction nonces."""

    def __init__(
        self,
        chain: Sequence[Block] | None = None,
        *,
        clock: Clock = time.time,
        consensus: ConsensusEngine = DEFAULT_CONSENSUS,
    ) -> None:
        self._clock = clock
        self.consensus = consensus
        self.chain = deepcopy(list(chain) if chain is not None else [GENESIS_BLOCK])
        self.balances: dict[str, int] = {}
        self.nonces: dict[str, int] = {}
        error = self.rebuild_state()
        if error is not None:
            raise BlockValidationError(error)

    @property
    def height(self) -> int:
        """Return the current tip height."""
        return self.chain[-1].index

    @property
    def tip(self) -> Block:
        """Return the current chain tip."""
        return self.chain[-1]

    @property
    def total_work(self) -> int:
        """Return the configured consensus engine's cumulative chain score."""
        return self.consensus.score(self.chain)

    def balance_of(self, address: str) -> int:
        """Return the confirmed balance of an address, or zero if it is unknown."""
        return self.balances.get(address, 0)

    def nonce_of(self, address: str) -> int:
        """Return the next expected transaction nonce for an address."""
        return self.nonces.get(address, 0)

    def _timestamp_now(self) -> int:
        return int(self._clock())

    def _expected_difficulty(self, block: Block, previous_blocks: Sequence[Block]) -> int:
        if block.index % RETARGET_INTERVAL:
            return previous_blocks[-1].difficulty_bits
        interval_start = previous_blocks[-RETARGET_INTERVAL].timestamp
        elapsed = block.timestamp - interval_start
        return self.consensus.expected_difficulty(
            previous_blocks[-1].difficulty_bits,
            elapsed,
            interval=RETARGET_INTERVAL,
            target_block_time=TARGET_BLOCK_TIME,
        )

    def _validate_genesis(self, block: Block) -> ValidationError | None:
        if block.version != GENESIS_BLOCK.version or block.chain_id != CHAIN_ID:
            return ValidationError(0, "genesis", "genesis version or chain_id is unsupported")
        if block.to_dict() != GENESIS_BLOCK.to_dict():
            return ValidationError(
                0,
                "genesis",
                "genesis block must exactly match the configured fixed block",
            )
        if not block.has_valid_hash():
            return ValidationError(0, "hash", "genesis header hash does not match")
        try:
            target_met = self.consensus.validate(block)
        except ValueError as error:
            return ValidationError(block.index, "difficulty_bits", str(error))
        if not target_met:
            return ValidationError(0, "proof_of_work", "genesis hash does not meet its target")
        return None

    def _validate_block(
        self,
        block: Block,
        previous_blocks: Sequence[Block],
        balances: dict[str, int],
        nonces: dict[str, int],
        seen_tx_ids: set[str],
        now: int,
    ) -> ValidationError | None:
        expected_index = previous_blocks[-1].index + 1
        if (
            not isinstance(block.index, int)
            or isinstance(block.index, bool)
            or block.index != expected_index
        ):
            return ValidationError(
                expected_index,
                "index",
                f"expected index {expected_index}, got {block.index}",
            )

        if block.version != GENESIS_BLOCK.version or block.chain_id != CHAIN_ID:
            return ValidationError(
                block.index,
                "block_chain_id",
                "block version or chain_id does not match the ledger",
            )

        previous = previous_blocks[-1]
        if not isinstance(block.previous_hash, str) or block.previous_hash != previous.hash:
            return ValidationError(
                block.index,
                "previous_hash",
                "previous_hash does not match the current chain tip",
            )
        if not isinstance(block.merkle_root, str):
            return ValidationError(block.index, "merkle_root", "merkle_root must be text")
        if not isinstance(block.difficulty_bits, int) or isinstance(block.difficulty_bits, bool):
            return ValidationError(
                block.index,
                "difficulty_bits",
                "difficulty_bits must be an integer",
            )
        if not isinstance(block.nonce, int) or isinstance(block.nonce, bool) or block.nonce < 0:
            return ValidationError(block.index, "nonce", "nonce must be a nonnegative integer")
        if not block.has_valid_hash():
            return ValidationError(block.index, "hash", "stored hash does not match block header")
        try:
            target_met = self.consensus.validate(block)
        except ValueError as error:
            return ValidationError(block.index, "difficulty_bits", str(error))
        if not target_met:
            return ValidationError(
                block.index,
                "proof_of_work",
                "block hash does not meet its difficulty target",
            )

        try:
            expected_difficulty = self._expected_difficulty(block, previous_blocks)
        except ValueError as error:
            return ValidationError(block.index, "difficulty_bits", str(error))
        if block.difficulty_bits != expected_difficulty:
            return ValidationError(
                block.index,
                "difficulty_bits",
                f"expected {expected_difficulty} bits, got {block.difficulty_bits}",
            )

        if not isinstance(block.timestamp, int) or isinstance(block.timestamp, bool):
            return ValidationError(
                block.index,
                "timestamp",
                "timestamp must be an integer",
            )
        if block.timestamp <= previous.timestamp:
            return ValidationError(
                block.index,
                "timestamp_order",
                "timestamp must be greater than the previous block timestamp",
            )
        if block.timestamp > now + MAX_FUTURE_DRIFT:
            return ValidationError(
                block.index,
                "timestamp_future",
                f"timestamp is more than {MAX_FUTURE_DRIFT} seconds ahead of local time",
            )
        if not isinstance(block.transactions, list):
            return ValidationError(
                block.index,
                "transaction_count",
                "transactions must be a list",
            )
        if len(block.transactions) > MAX_TX_PER_BLOCK:
            return ValidationError(
                block.index,
                "transaction_count",
                f"block has more than {MAX_TX_PER_BLOCK} transactions",
            )

        transactions: list[Transaction] = []
        for position, payload in enumerate(block.transactions):
            if not isinstance(payload, dict):
                return ValidationError(
                    block.index,
                    "transaction_format",
                    f"transaction {position} must be a JSON object",
                )
            try:
                transaction = Transaction.from_dict(payload)
            except (TypeError, ValueError) as error:
                return ValidationError(
                    block.index,
                    "transaction_format",
                    f"transaction {position} is malformed: {error}",
                )
            transactions.append(transaction)

        if block.merkle_root != merkle_root([transaction.tx_id for transaction in transactions]):
            return ValidationError(
                block.index,
                "merkle_root",
                "merkle root does not match the block transactions",
            )

        if not transactions or not transactions[0].is_coinbase:
            return ValidationError(
                block.index,
                "coinbase_position",
                "the first transaction must be the block's coinbase",
            )
        coinbase_count = sum(transaction.is_coinbase for transaction in transactions)
        if coinbase_count != 1:
            return ValidationError(
                block.index,
                "coinbase_count",
                "a block must contain exactly one coinbase transaction",
            )

        block_tx_ids: set[str] = set()
        for transaction in transactions:
            if transaction.tx_id in seen_tx_ids or transaction.tx_id in block_tx_ids:
                return ValidationError(
                    block.index,
                    "duplicate_transaction",
                    f"transaction {transaction.tx_id} appears more than once",
                )
            if transaction.tx_id != transaction.calculate_tx_id():
                return ValidationError(
                    block.index,
                    "transaction_id",
                    f"transaction {transaction.tx_id} has an invalid transaction ID",
                )
            block_tx_ids.add(transaction.tx_id)

        # Apply to working copies so failed blocks cannot partially change chain state.
        next_balances = balances.copy()
        next_nonces = nonces.copy()
        for position, transaction in enumerate(transactions):
            apply_error = apply_transaction(
                transaction,
                next_balances,
                next_nonces,
                block_index=block.index,
                chain_id=CHAIN_ID,
                allow_coinbase=position == 0,
            )
            if apply_error is not None:
                return apply_error

        balances.clear()
        balances.update(next_balances)
        nonces.clear()
        nonces.update(next_nonces)
        seen_tx_ids.update(block_tx_ids)
        return None

    def validate_chain(
        self,
        chain: Sequence[Block] | None = None,
    ) -> ValidationError | None:
        """Return the first validation error, or None when the complete chain is valid."""
        blocks = self.chain if chain is None else chain
        if not blocks:
            return ValidationError(0, "genesis", "chain must contain the configured genesis block")

        error = self._validate_genesis(blocks[0])
        if error is not None:
            return error

        balances: dict[str, int] = {}
        nonces: dict[str, int] = {}
        seen_tx_ids: set[str] = set()
        now = self._timestamp_now()
        for position, block in enumerate(blocks[1:], start=1):
            if (
                not isinstance(block.index, int)
                or isinstance(block.index, bool)
                or block.index != position
            ):
                return ValidationError(
                    block.index
                    if isinstance(block.index, int) and not isinstance(block.index, bool)
                    else position,
                    "index",
                    f"expected index {position}, got {block.index}",
                )
            error = self._validate_block(
                block,
                blocks[:position],
                balances,
                nonces,
                seen_tx_ids,
                now,
            )
            if error is not None:
                return error
        return None

    def rebuild_state(self) -> ValidationError | None:
        """Replay the chain and atomically replace balances and next nonces if valid."""
        error = self.validate_chain()
        if error is not None:
            return error

        balances: dict[str, int] = {}
        nonces: dict[str, int] = {}
        seen_tx_ids: set[str] = set()
        now = self._timestamp_now()
        for position, block in enumerate(self.chain[1:], start=1):
            error = self._validate_block(
                block,
                self.chain[:position],
                balances,
                nonces,
                seen_tx_ids,
                now,
            )
            if error is not None:
                return error
        self.balances = balances
        self.nonces = nonces
        return None

    def append_block(self, block: Block) -> None:
        """Validate and append a single extending block, updating derived state atomically."""
        balances = self.balances.copy()
        nonces = self.nonces.copy()
        seen_tx_ids: set[str] = set()
        for existing_block in self.chain:
            for payload in existing_block.transactions:
                transaction_id = payload.get("tx_id")
                if isinstance(transaction_id, str):
                    seen_tx_ids.add(transaction_id)
        error = self._validate_block(
            block,
            self.chain,
            balances,
            nonces,
            seen_tx_ids,
            self._timestamp_now(),
        )
        if error is not None:
            raise BlockValidationError(error)
        try:
            finalized_block = self.consensus.finalize(block)
        except ValueError as finalize_error:
            raise BlockValidationError(
                ValidationError(block.index, "consensus_finalize", str(finalize_error))
            ) from finalize_error
        if finalized_block.to_dict() != block.to_dict():
            raise BlockValidationError(
                ValidationError(
                    block.index,
                    "consensus_finalize",
                    "consensus finalization changed the validated block",
                )
            )
        self.chain.append(finalized_block)
        self.balances = balances
        self.nonces = nonces

    def replace_chain(self, candidate: Sequence[Block]) -> bool:
        """Replace the current chain only when a fully validated candidate has more work."""
        candidate_blocks = deepcopy(list(candidate))
        candidate_chain = Blockchain(
            candidate_blocks,
            clock=self._clock,
            consensus=self.consensus,
        )
        if not self.consensus.score(candidate_chain.chain) > self.consensus.score(self.chain):
            return False
        self.chain = candidate_chain.chain
        self.balances = candidate_chain.balances
        self.nonces = candidate_chain.nonces
        return True

    def create_candidate_block(
        self,
        miner_address: str,
        transactions: Sequence[Transaction] = (),
        *,
        timestamp: int | None = None,
    ) -> Block:
        """Build a candidate with a coinbase first and the required next difficulty."""
        if not is_valid_address(miner_address):
            raise ValueError("miner_address must be a 40-character lowercase hexadecimal address")
        block_timestamp = (
            max(self.tip.timestamp + 1, int(self._clock())) if timestamp is None else timestamp
        )
        if not isinstance(block_timestamp, int) or isinstance(block_timestamp, bool):
            raise ValueError("timestamp must be an integer")
        coinbase = build_coinbase_transaction(
            miner_address,
            timestamp=block_timestamp,
            chain_id=self.tip.chain_id,
        )
        transaction_dicts: list[dict[str, object]] = [
            coinbase.to_dict(),
            *(transaction.to_dict() for transaction in transactions),
        ]
        if len(transaction_dicts) > MAX_TX_PER_BLOCK:
            raise ValueError(f"a block may contain at most {MAX_TX_PER_BLOCK} transactions")
        block = Block(
            index=self.tip.index + 1,
            timestamp=block_timestamp,
            previous_hash=self.tip.hash,
            merkle_root=merkle_root(
                [coinbase.tx_id, *(transaction.tx_id for transaction in transactions)]
            ),
            difficulty_bits=self.tip.difficulty_bits,
            transactions=transaction_dicts,
            chain_id=self.tip.chain_id,
        )
        block.difficulty_bits = self._expected_difficulty(block, self.chain)
        block.hash = block.calculate_hash()
        return block

    def mine_candidate_block(
        self,
        miner_address: str,
        transactions: Sequence[Transaction] = (),
        *,
        timestamp: int | None = None,
        cancel_event: Event | None = None,
    ) -> Block:
        """Mine a candidate block and raise if the optional cancellation event is set."""
        candidate = self.create_candidate_block(
            miner_address,
            transactions,
            timestamp=timestamp,
        )
        result = self.consensus.propose(
            candidate,
            cancel_event if cancel_event is not None else Event(),
            timestamp_fn=self._clock,
        )
        if result is None:
            raise RuntimeError("mining was cancelled")
        return result
