"""Consensus engine contract and the default proof-of-work implementation."""

from collections.abc import Callable, Sequence
from threading import Event
from typing import Protocol

from pychain.core.block import Block
from pychain.core.pow import meets_target, mine_block, retarget_difficulty


class ConsensusEngine(Protocol):
    """Consensus operations used by the ledger without binding it to proof-of-work."""

    def propose(
        self,
        candidate: Block,
        cancel_event: Event,
        *,
        timestamp_fn: Callable[[], float],
        progress_fn: Callable[[int, int], None] | None = None,
    ) -> Block | None: ...

    def validate(self, block: Block) -> bool: ...

    def finalize(self, block: Block) -> Block: ...

    def score(self, chain: Sequence[Block]) -> int: ...

    def expected_difficulty(
        self,
        previous_difficulty: int,
        elapsed: float,
        *,
        interval: int,
        target_block_time: int,
    ) -> int: ...


class ProofOfWorkConsensus:
    """Nonce-search consensus with cumulative-work fork choice."""

    def propose(
        self,
        candidate: Block,
        cancel_event: Event,
        *,
        timestamp_fn: Callable[[], float],
        progress_fn: Callable[[int, int], None] | None = None,
    ) -> Block | None:
        return mine_block(
            candidate,
            cancel_event,
            timestamp_fn=timestamp_fn,
            progress_fn=progress_fn,
        )

    def validate(self, block: Block) -> bool:
        return meets_target(block.hash, block.difficulty_bits)

    def finalize(self, block: Block) -> Block:
        if not block.has_valid_hash() or not self.validate(block):
            raise ValueError("block does not satisfy proof-of-work consensus")
        return block

    def score(self, chain: Sequence[Block]) -> int:
        return sum(1 << block.difficulty_bits for block in chain)

    def expected_difficulty(
        self,
        previous_difficulty: int,
        elapsed: float,
        *,
        interval: int,
        target_block_time: int,
    ) -> int:
        return retarget_difficulty(
            previous_difficulty,
            elapsed,
            interval=interval,
            target_block_time=target_block_time,
        )


DEFAULT_CONSENSUS = ProofOfWorkConsensus()
