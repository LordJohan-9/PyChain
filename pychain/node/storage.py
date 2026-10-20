"""Atomic JSON persistence and fully validated chain recovery."""

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from pychain.config import SERIALIZATION_VERSION
from pychain.core.block import Block
from pychain.core.chain import Blockchain, BlockValidationError
from pychain.core.consensus import DEFAULT_CONSENSUS, ConsensusEngine


class PersistenceError(ValueError):
    """Raised when persisted data is unreadable, malformed, or invalid."""


class JsonStorage:
    """Persist one node's blockchain and peer list as atomically replaced JSON files."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)

    @property
    def chain_path(self) -> Path:
        return self.data_dir / "chain.json"

    @property
    def peers_path(self) -> Path:
        return self.data_dir / "peers.json"

    def _write_json_atomic(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(payload, temporary_file, sort_keys=True, separators=(",", ":"))
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
        except OSError as error:
            raise PersistenceError(f"could not atomically write {path}: {error}") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _read_json(path: Path) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PersistenceError(f"could not read {path}: {error}") from error

    def save_chain(self, chain: Blockchain | list[Block]) -> None:
        """Atomically persist a versioned block-file object."""
        blocks = chain.chain if isinstance(chain, Blockchain) else chain
        self._write_json_atomic(
            self.chain_path,
            {"version": SERIALIZATION_VERSION, "blocks": [block.to_dict() for block in blocks]},
        )

    def load_chain(
        self,
        *,
        clock: Callable[[], float] | None = None,
        consensus: ConsensusEngine = DEFAULT_CONSENSUS,
    ) -> Blockchain:
        """Read and fully validate the saved chain before returning a Blockchain."""
        payload = self._read_json(self.chain_path)
        if not isinstance(payload, dict) or set(payload) != {"version", "blocks"}:
            raise PersistenceError("chain file must contain version and blocks fields")
        if type(payload["version"]) is not int or payload["version"] != SERIALIZATION_VERSION:
            raise PersistenceError(f"unsupported chain file version {payload['version']!r}")
        block_payloads = payload["blocks"]
        if not isinstance(block_payloads, list):
            raise PersistenceError("chain file blocks field must contain a JSON array")
        blocks: list[Block] = []
        for index, item in enumerate(block_payloads):
            try:
                blocks.append(self._block_from_dict(item))
            except (TypeError, ValueError) as error:
                raise PersistenceError(
                    f"invalid block at array position {index}: {error}"
                ) from error

        try:
            if clock is None:
                return Blockchain(blocks, consensus=consensus)
            return Blockchain(blocks, clock=clock, consensus=consensus)
        except BlockValidationError as error:
            validation_error = error.validation_error
            raise PersistenceError(
                f"saved chain failed block {validation_error.block_index} "
                f"rule {validation_error.rule}: {validation_error.message}"
            ) from error

    @staticmethod
    def _block_from_dict(value: object) -> Block:
        if not isinstance(value, dict):
            raise ValueError("block must be a JSON object")
        required = {
            "version",
            "chain_id",
            "index",
            "timestamp",
            "previous_hash",
            "merkle_root",
            "difficulty_bits",
            "nonce",
            "hash",
            "transactions",
        }
        if set(value) != required:
            raise ValueError("block has missing or unexpected fields")
        if type(value["version"]) is not int or value["version"] != SERIALIZATION_VERSION:
            raise ValueError(f"unsupported block version {value['version']!r}")
        string_fields = ("chain_id", "previous_hash", "merkle_root", "hash")
        if not all(isinstance(value[field], str) for field in string_fields):
            raise ValueError("block hash fields must be strings")
        integer_fields = ("version", "index", "timestamp", "difficulty_bits", "nonce")
        if not all(
            isinstance(value[field], int) and not isinstance(value[field], bool)
            for field in integer_fields
        ):
            raise ValueError("block index, timestamp, difficulty_bits, and nonce must be integers")
        if not isinstance(value["transactions"], list) or not all(
            isinstance(transaction, dict) for transaction in value["transactions"]
        ):
            raise ValueError("block transactions must be an array of objects")
        return Block(
            index=value["index"],
            timestamp=value["timestamp"],
            previous_hash=value["previous_hash"],
            merkle_root=value["merkle_root"],
            difficulty_bits=value["difficulty_bits"],
            nonce=value["nonce"],
            transactions=value["transactions"],
            hash=value["hash"],
            version=value["version"],
            chain_id=value["chain_id"],
        )

    def save_peers(self, peers: list[str]) -> None:
        """Atomically persist the node's peer URL list."""
        if not all(isinstance(peer, str) for peer in peers):
            raise ValueError("peer URLs must be strings")
        self._write_json_atomic(
            self.peers_path,
            {"version": SERIALIZATION_VERSION, "peers": peers},
        )

    def load_peers(self) -> list[str]:
        """Load peer URLs; an absent peer file represents an empty initial peer list."""
        if not self.peers_path.exists():
            return []
        payload = self._read_json(self.peers_path)
        if not isinstance(payload, dict) or set(payload) != {"version", "peers"}:
            raise PersistenceError("peer file must contain version and peers fields")
        if type(payload["version"]) is not int or payload["version"] != SERIALIZATION_VERSION:
            raise PersistenceError(f"unsupported peer file version {payload['version']!r}")
        peers = payload["peers"]
        if not isinstance(peers, list) or not all(isinstance(peer, str) for peer in peers):
            raise PersistenceError("peer file peers field must contain an array of strings")
        return peers
