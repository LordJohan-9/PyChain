"""Peer chain synchronization, validation, and heavier-chain adoption."""

import logging
import time
from typing import TYPE_CHECKING

import httpx

from pychain.config import SERIALIZATION_VERSION
from pychain.core.chain import Blockchain, BlockValidationError
from pychain.core.transaction import Transaction
from pychain.node.storage import JsonStorage, PersistenceError

if TYPE_CHECKING:
    from pychain.node.miner import NodeRuntime
    from pychain.node.peers import PeerManager

logger = logging.getLogger(__name__)
PEER_TIMEOUT_SECONDS = 3.0
SYNC_INTERVAL_SECONDS = 60.0


class ConsensusManager:
    """Fetch and adopt fully validated peer chains using cumulative work."""

    def __init__(self, runtime: "NodeRuntime", peers: "PeerManager") -> None:
        self.runtime = runtime
        self.peers = peers
        self.last_sync = 0.0

    def sync_all(self) -> int:
        """Sync from known peers and return how many heavier chains were adopted."""
        adopted = 0
        for peer in self.peers.peers:
            try:
                if self.sync_peer(peer.url):
                    adopted += 1
            except PersistenceError:
                raise
            except (httpx.HTTPError, ValueError) as error:
                logger.warning("chain sync from peer %s failed: %s", peer.url, error)
        self.last_sync = time.monotonic()
        return adopted

    def sync_peer(self, url: str) -> bool:
        """Fetch, validate, compare, and possibly adopt one peer's complete chain."""
        normalized_url = self.peers._normalize_url(url)
        peer = next((item for item in self.peers.peers if item.url == normalized_url), None)
        if peer is None:
            raise ValueError(f"unknown peer {normalized_url}")

        with httpx.Client(timeout=PEER_TIMEOUT_SECONDS) as client:
            response = client.get(f"{normalized_url}/chain")
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as error:
                self.peers.strike(normalized_url)
                raise ValueError("peer chain response is not valid JSON") from error
        if (
            not isinstance(payload, dict)
            or type(payload.get("version")) is not int
            or payload.get("version") != SERIALIZATION_VERSION
            or not isinstance(payload.get("blocks"), list)
        ):
            self.peers.strike(normalized_url)
            raise ValueError("peer chain response has an unsupported version or blocks array")

        try:
            blocks = [
                JsonStorage._block_from_dict(block_payload) for block_payload in payload["blocks"]
            ]
            candidate = Blockchain(
                blocks,
                clock=self.runtime.clock,
                consensus=self.runtime.blockchain.consensus,
            )
        except (BlockValidationError, TypeError, ValueError) as error:
            self.peers.strike(normalized_url)
            raise ValueError(f"peer supplied an invalid chain: {error}") from error

        with self.runtime.state_lock:
            current = self.runtime.blockchain
            current_work = current.total_work
            if candidate.total_work <= current_work:
                peer.height = candidate.height
                peer.total_work = candidate.total_work
                peer.failures = 0
                peer.last_seen = time.time()
                return False

            candidate_hashes = {block.hash for block in candidate.chain}
            orphaned = [
                transaction
                for block in current.chain
                if block.hash not in candidate_hashes
                for payload_item in block.transactions
                if not (transaction := Transaction.from_dict(payload_item)).is_coinbase
            ]
            self.runtime.storage.save_chain(candidate)
            if not current.replace_chain(candidate.chain):
                return False

            self.runtime.mempool.repair_after_reorg(orphaned)
            peer.height = candidate.height
            peer.total_work = candidate.total_work
            peer.failures = 0
            peer.last_seen = time.time()
            new_tip = current.tip.to_dict()

        logger.info(
            "adopted peer chain from %s (work %s -> %s)",
            normalized_url,
            current_work,
            candidate.total_work,
        )
        self.peers.gossip_block(new_tip, exclude_url=normalized_url)
        return True

    def handle_new_block(self, block_payload: dict[str, object], sender_url: str) -> bool:
        """Extend the tip, ignore stale side blocks, or sync when the peer is ahead."""
        try:
            block = JsonStorage._block_from_dict(block_payload)
        except (TypeError, ValueError) as error:
            self.peers.strike(sender_url)
            raise ValueError(f"malformed peer block: {error}") from error

        with self.runtime.state_lock:
            chain = self.runtime.blockchain
            if any(existing.hash == block.hash for existing in chain.chain):
                return False
            if block.index <= chain.height:
                return False
            extends_tip = block.index == chain.height + 1 and block.previous_hash == chain.tip.hash

        if extends_tip:
            return self.runtime.receive_extending_block(block, sender_url)
        return self.sync_peer(sender_url)
