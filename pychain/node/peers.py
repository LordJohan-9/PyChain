"""HTTP peer management, discovery, health checks, and loop-safe gossip."""

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from pychain.config import MAX_PEERS, SERIALIZATION_VERSION
from pychain.node.consensus import SYNC_INTERVAL_SECONDS, ConsensusManager
from pychain.node.p2p import P2PMessage

if TYPE_CHECKING:
    from pychain.node.miner import NodeRuntime

logger = logging.getLogger(__name__)
PEER_TIMEOUT_SECONDS = 3.0
MAX_SEEN_IDS = 10_000
MAX_PEER_STRIKES = 5
MAX_PEER_FAILURES = 3


@dataclass
class Peer:
    """Tracked peer connection and session health state."""

    node_id: str
    url: str
    height: int
    total_work: int
    last_seen: float
    failures: int = 0
    strikes: int = 0
    version: int = SERIALIZATION_VERSION

    def to_dict(self) -> dict[str, str | int | float]:
        return {
            "version": self.version,
            "node_id": self.node_id,
            "url": self.url,
            "height": self.height,
            "total_work": self.total_work,
            "last_seen": self.last_seen,
            "failures": self.failures,
            "strikes": self.strikes,
        }


class PeerManager:
    """Manage capped peer state and forward each valid object no more than once."""

    def __init__(self, runtime: "NodeRuntime") -> None:
        self.runtime = runtime
        self.consensus = ConsensusManager(runtime, self)
        self._peers: dict[str, Peer] = {}
        self._banned: set[str] = set()
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._health_thread: threading.Thread | None = None

    @property
    def peers(self) -> list[Peer]:
        with self._lock:
            return list(self._peers.values())

    @property
    def peer_urls(self) -> list[str]:
        with self._lock:
            return list(self._peers)

    def start(self) -> None:
        """Connect configured peers, sync once, and launch periodic health checks."""
        for url in self.runtime.storage.load_peers():
            try:
                self.add_peer(url)
            except (ValueError, httpx.HTTPError) as error:
                logger.warning("could not connect to configured peer %s: %s", url, error)
        self.sync_all()
        if self._health_thread is None or not self._health_thread.is_alive():
            self._stop.clear()
            self._health_thread = threading.Thread(
                target=self._health_loop,
                name=f"pychain-peer-health-{self.runtime.port}",
                daemon=True,
            )
            self._health_thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._health_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("peer URL must be an absolute HTTP or HTTPS URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("peer URL must not contain credentials")
        return url.rstrip("/")

    def _save_peers(self) -> None:
        self.runtime.storage.save_peers(self.peer_urls)

    def persist_peers(self) -> None:
        """Persist current peer URLs after an inbound handshake or management update."""
        self._save_peers()

    def add_peer(self, url: str) -> Peer:
        """Perform the handshake and accept its peer record; reject self and over-cap peers."""
        normalized_url = self._normalize_url(url)
        if normalized_url == self.runtime.url:
            raise ValueError("cannot add this node as its own peer")
        with self._lock:
            if normalized_url in self._banned:
                raise ValueError("peer is banned for this session")
            existing = self._peers.get(normalized_url)
            if existing is not None:
                return existing
            if len(self._peers) >= MAX_PEERS:
                raise ValueError(f"peer limit of {MAX_PEERS} has been reached")

        payload = P2PMessage(
            "hello",
            self.runtime.node_id,
            {
                "node_id": self.runtime.node_id,
                "url": self.runtime.url,
                "height": self.runtime.blockchain.height,
                "total_work": self.runtime.blockchain.total_work,
            },
        ).to_dict()
        with httpx.Client(timeout=PEER_TIMEOUT_SECONDS) as client:
            response = client.post(f"{normalized_url}/p2p/hello", json=payload)
            response.raise_for_status()
            remote = response.json()
        peer = self._record_peer(remote)
        self._save_peers()
        discovered = P2PMessage.from_dict(remote).payload.get("peers", [])
        if isinstance(discovered, list):
            for item in discovered:
                if not isinstance(item, dict):
                    continue
                discovered_url = item.get("url")
                if isinstance(discovered_url, str) and discovered_url not in {
                    self.runtime.url,
                    normalized_url,
                }:
                    try:
                        self.add_peer(discovered_url)
                    except (ValueError, httpx.HTTPError) as error:
                        logger.debug("peer discovery skipped %s: %s", discovered_url, error)
        return peer

    def record_hello(
        self,
        node_id: str,
        url: str,
        height: int,
        total_work: int,
    ) -> Peer:
        normalized_url = self._normalize_url(url)
        if normalized_url == self.runtime.url:
            raise ValueError("cannot handshake with this node as its own peer")
        return self._store_peer(
            Peer(
                node_id=node_id,
                url=normalized_url,
                height=height,
                total_work=total_work,
                last_seen=time.time(),
            )
        )

    def _record_peer(self, response: object) -> Peer:
        if not isinstance(response, dict):
            raise ValueError("peer handshake response must be an object")
        message = P2PMessage.from_dict(response)
        if message.message_type != "hello":
            raise ValueError("peer handshake response has an unsupported message type")
        if message.payload.get("node_id") != message.sender_id:
            raise ValueError("peer handshake sender ID does not match its payload")
        return self.record_hello(
            node_id=message.sender_id,
            url=self._required_string(message.payload, "url"),
            height=self._required_int(message.payload, "height"),
            total_work=self._required_int(message.payload, "total_work"),
        )

    @staticmethod
    def _required_string(value: Mapping[str, Any], key: str) -> str:
        field = value.get(key)
        if not isinstance(field, str):
            raise ValueError(f"peer handshake field {key} must be a string")
        return field

    @staticmethod
    def _required_int(value: Mapping[str, Any], key: str) -> int:
        field = value.get(key)
        if not isinstance(field, int) or isinstance(field, bool):
            raise ValueError(f"peer handshake field {key} must be an integer")
        return field

    def _store_peer(self, peer: Peer) -> Peer:
        with self._lock:
            existing = self._peers.get(peer.url)
            if existing is None and len(self._peers) >= MAX_PEERS:
                raise ValueError(f"peer limit of {MAX_PEERS} has been reached")
            self._peers[peer.url] = peer
            return peer

    def remove_peer(self, url: str) -> bool:
        normalized_url = self._normalize_url(url)
        with self._lock:
            removed = self._peers.pop(normalized_url, None) is not None
        if removed:
            self._save_peers()
        return removed

    def mark_seen(self, message_id: str) -> bool:
        """Return false when the ID has already been processed; otherwise remember it."""
        with self._lock:
            if message_id in self._seen:
                self._seen.move_to_end(message_id)
                return False
            self._seen[message_id] = None
            if len(self._seen) > MAX_SEEN_IDS:
                self._seen.popitem(last=False)
            return True

    def peer_is_banned(self, url: str) -> bool:
        with self._lock:
            return url in self._banned

    def has_peer(self, url: str) -> bool:
        normalized_url = self._normalize_url(url)
        with self._lock:
            return normalized_url in self._peers

    def is_known_peer(self, url: str, node_id: str) -> bool:
        normalized_url = self._normalize_url(url)
        with self._lock:
            peer = self._peers.get(normalized_url)
            return peer is not None and peer.node_id == node_id

    def strike(self, url: str) -> int:
        normalized_url = self._normalize_url(url)
        with self._lock:
            peer = self._peers.get(normalized_url)
            if peer is None:
                return 0
            peer.strikes += 1
            strikes = peer.strikes
            if strikes >= MAX_PEER_STRIKES:
                self._banned.add(normalized_url)
                del self._peers[normalized_url]
                logger.warning("banned peer %s after %s invalid messages", url, strikes)
        if strikes >= MAX_PEER_STRIKES:
            self._save_peers()
        return strikes

    def _send(self, peer: Peer, path: str, payload: dict[str, object]) -> bool:
        try:
            with httpx.Client(timeout=PEER_TIMEOUT_SECONDS) as client:
                response = client.post(f"{peer.url}{path}", json=payload)
                response.raise_for_status()
            with self._lock:
                peer.failures = 0
                peer.last_seen = time.time()
            return True
        except httpx.HTTPError as error:
            with self._lock:
                peer.failures += 1
                failures = peer.failures
            logger.debug("peer call to %s failed (%s): %s", peer.url, failures, error)
            if failures >= MAX_PEER_FAILURES:
                self.remove_peer(peer.url)
            return False

    def gossip_transaction(
        self,
        transaction: dict[str, object],
        exclude_url: str | None = None,
        *,
        already_seen: bool = False,
    ) -> None:
        tx_id = transaction.get("tx_id")
        if not isinstance(tx_id, str):
            return
        if not already_seen and not self.mark_seen(f"tx:{tx_id}"):
            return
        payload = P2PMessage("transaction", self.runtime.node_id, transaction).to_dict()
        payload["sender_url"] = self.runtime.url
        self._broadcast("/p2p/transactions", payload, exclude_url)

    def gossip_block(
        self,
        block: dict[str, object],
        exclude_url: str | None = None,
        *,
        already_seen: bool = False,
    ) -> None:
        block_hash = block.get("hash")
        if not isinstance(block_hash, str):
            return
        if not already_seen and not self.mark_seen(f"block:{block_hash}"):
            return
        payload = P2PMessage("block", self.runtime.node_id, block).to_dict()
        payload["sender_url"] = self.runtime.url
        self._broadcast("/p2p/blocks", payload, exclude_url)

    def _broadcast(
        self,
        path: str,
        payload: dict[str, object],
        exclude_url: str | None,
    ) -> None:
        with self._lock:
            peers = [peer for peer in self._peers.values() if peer.url != exclude_url]
        for peer in peers:
            self._send(peer, path, payload)

    def _health_loop(self) -> None:
        while not self._stop.wait(30.0):
            with self._lock:
                peers = list(self._peers.values())
            for peer in peers:
                try:
                    with httpx.Client(timeout=PEER_TIMEOUT_SECONDS) as client:
                        response = client.get(f"{peer.url}/status")
                        response.raise_for_status()
                        remote = response.json()
                    with self._lock:
                        peer.height = int(remote["height"])
                        peer.total_work = int(remote["total_work"])
                        peer.failures = 0
                        peer.last_seen = time.time()
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                    with self._lock:
                        peer.failures += 1
                        failures = peer.failures
                    logger.debug("peer health check %s failed (%s): %s", peer.url, failures, error)
                    if failures >= MAX_PEER_FAILURES:
                        self.remove_peer(peer.url)
            if time.monotonic() - self.consensus.last_sync >= SYNC_INTERVAL_SECONDS:
                self.sync_all()

    def sync_all(self) -> int:
        """Sync from known peers and return how many heavier chains were adopted."""
        return self.consensus.sync_all()

    def sync_peer(self, url: str) -> bool:
        """Fetch, validate, compare, and possibly adopt one peer's complete chain."""
        return self.consensus.sync_peer(url)

    def handle_new_block(self, block_payload: dict[str, object], sender_url: str) -> bool:
        """Extend the tip, ignore stale side blocks, or sync when the peer is ahead."""
        return self.consensus.handle_new_block(block_payload, sender_url)
