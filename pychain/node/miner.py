"""Node runtime state and serialized background block mining."""

import logging
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from pychain.config import MAX_TX_PER_BLOCK
from pychain.core.block import Block
from pychain.core.chain import Blockchain, BlockValidationError, ValidationError
from pychain.core.consensus import DEFAULT_CONSENSUS, ConsensusEngine
from pychain.core.mempool import Mempool
from pychain.core.transaction import Transaction
from pychain.core.wallet import Wallet
from pychain.node.peers import PeerManager
from pychain.node.storage import JsonStorage

logger = logging.getLogger(__name__)
Clock = Callable[[], float]


class MiningError(RuntimeError):
    """Raised when mining cannot complete or a mined candidate becomes stale."""


class MiningCancelled(MiningError):
    """Raised when mining was stopped before producing a block."""


class NodeRuntime:
    """Own the in-process chain, wallet, mempool, storage, and miner lifecycle."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        port: int = 8000,
        clock: Clock = time.time,
        consensus: ConsensusEngine = DEFAULT_CONSENSUS,
    ) -> None:
        self.clock = clock
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.node_id = str(uuid.uuid4())
        self.storage = JsonStorage(data_dir)
        if self.storage.chain_path.exists():
            self.blockchain = self.storage.load_chain(clock=clock, consensus=consensus)
        else:
            self.blockchain = Blockchain(clock=clock, consensus=consensus)
            self.storage.save_chain(self.blockchain)
        wallet_path = self.storage.data_dir / "wallet.json"
        if wallet_path.exists():
            self.wallet = Wallet.load(wallet_path)
        else:
            self.wallet = Wallet.generate()
            self.wallet.save(wallet_path)

        self.mempool = Mempool(self.blockchain)
        self.state_lock = threading.RLock()
        self.peer_manager = PeerManager(self)
        self._operation_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._auto_thread: threading.Thread | None = None
        self._last_hash_rate = 0.0
        self._last_error: str | None = None
        self._mining_nonce = 0
        self._mining_attempts = 0

    @property
    def is_mining(self) -> bool:
        return self._operation_lock.locked()

    @property
    def peers(self) -> list[str]:
        return self.peer_manager.peer_urls

    @property
    def is_auto_mining(self) -> bool:
        return self._auto_thread is not None and self._auto_thread.is_alive()

    @property
    def estimated_hash_rate(self) -> float:
        return self._last_hash_rate

    @property
    def mining_nonce(self) -> int:
        return self._mining_nonce

    @property
    def mining_attempts(self) -> int:
        return self._mining_attempts

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def validate_chain(self) -> ValidationError | None:
        """Validate a stable chain snapshot while excluding concurrent appends."""
        with self.state_lock:
            return self.blockchain.validate_chain()

    def mine_one(self) -> dict[str, object]:
        """Mine one block outside the API event loop and persist it on success."""
        return self._mine_one(clear_cancel_event=True)

    def _mine_one(self, *, clear_cancel_event: bool) -> dict[str, object]:
        """Serialize mining and optionally preserve a stop signal from the auto-miner."""
        if not self._operation_lock.acquire(blocking=False):
            raise MiningError("another mining operation is already active")
        if clear_cancel_event:
            self._stop_event.clear()
        try:
            with self.state_lock:
                included = self.mempool.transactions[: MAX_TX_PER_BLOCK - 1]
                candidate = self.blockchain.create_candidate_block(
                    self.wallet.address,
                    included,
                    timestamp=max(self.blockchain.tip.timestamp + 1, int(self.clock())),
                )
            started = time.perf_counter()

            def update_progress(nonce: int, attempts: int) -> None:
                self._mining_nonce = nonce
                self._mining_attempts = attempts

            result = self.blockchain.consensus.propose(
                candidate,
                self._stop_event,
                timestamp_fn=self.clock,
                progress_fn=update_progress,
            )
            elapsed = max(time.perf_counter() - started, 1e-9)
            if result is None:
                raise MiningCancelled("mining was cancelled")
            self._last_hash_rate = (result.nonce - candidate.nonce + 1) / elapsed

            with self.state_lock:
                if self.blockchain.tip.hash != candidate.previous_hash:
                    raise MiningError("chain tip changed while the block was being mined")
                self.blockchain.append_block(result)
                self.mempool.remove_included(included)
                self.storage.save_chain(self.blockchain)
            self.peer_manager.gossip_block(result.to_dict())
            logger.info("mined block %s with hash %s", result.index, result.hash)
            self._last_error = None
            return result.to_dict()
        except BlockValidationError as error:
            self._last_error = str(error)
            raise
        finally:
            self._mining_nonce = 0
            self._mining_attempts = 0
            self._operation_lock.release()

    def start_auto_mining(self) -> None:
        """Start a daemon worker that mines repeatedly until stopped."""
        if self.is_auto_mining:
            raise MiningError("auto-mining is already running")
        self._stop_event.clear()
        self._auto_thread = threading.Thread(
            target=self._auto_mine_loop,
            name=f"pychain-miner-{self.port}",
            daemon=True,
        )
        self._auto_thread.start()

    def _auto_mine_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._mine_one(clear_cancel_event=False)
            except MiningCancelled:
                break
            except MiningError as error:
                if self._stop_event.is_set():
                    break
                self._last_error = str(error)
                logger.exception("auto-mining stopped: %s", error)
                self._stop_event.set()
                break
            except Exception as error:
                self._last_error = str(error)
                logger.exception("unexpected auto-mining error")
                self._stop_event.set()
                break

    def stop_mining(self, *, timeout: float = 2.0) -> None:
        """Signal the current miner and wait briefly for the background loop to exit."""
        self._stop_event.set()
        thread = self._auto_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def close(self) -> None:
        """Stop background mining before the application shuts down."""
        self.stop_mining()
        self.peer_manager.stop()

    def receive_transaction(self, payload: dict[str, object], sender_url: str) -> bool:
        """Validate, add, de-duplicate, and relay a transaction received from a peer."""
        transaction = Transaction.from_dict(payload)
        with self.state_lock:
            added = self.mempool.add(transaction)
        if not added:
            return False
        if not self.peer_manager.mark_seen(f"tx:{transaction.tx_id}"):
            return False
        self.peer_manager.gossip_transaction(
            transaction.to_dict(),
            exclude_url=sender_url,
            already_seen=True,
        )
        return True

    def receive_block(self, payload: dict[str, object], sender_url: str) -> bool:
        """Handle a block by extension, stale-block ignore, or ahead-peer chain sync."""
        return self.peer_manager.handle_new_block(payload, sender_url)

    def receive_extending_block(self, block: Block, sender_url: str) -> bool:
        """Validate and append an extending peer block and repair the pending pool."""
        try:
            block_hash = block.hash
            if not self.peer_manager.mark_seen(f"block:{block_hash}"):
                return False
            with self.state_lock:
                if any(existing.hash == block_hash for existing in self.blockchain.chain):
                    return False
                included_ids: list[str] = []
                for transaction in block.transactions:
                    transaction_id = transaction.get("tx_id")
                    if isinstance(transaction_id, str):
                        included_ids.append(transaction_id)
                self.blockchain.append_block(block)
                self.mempool.remove_included(included_ids)
                self.storage.save_chain(self.blockchain)
        except (BlockValidationError, TypeError, ValueError):
            self.peer_manager.strike(sender_url)
            raise
        self.peer_manager.gossip_block(
            block.to_dict(),
            exclude_url=sender_url,
            already_seen=True,
        )
        return True
