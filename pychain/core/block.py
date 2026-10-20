"""Block model and deterministic genesis block."""

from dataclasses import dataclass, field

from pychain.config import CHAIN_ID, INITIAL_DIFFICULTY_BITS, SERIALIZATION_VERSION
from pychain.core.hashing import canonical_json, merkle_root, sha256_hex

GENESIS_TIMESTAMP = 1_700_000_000
GENESIS_NONCE = 84_992
GENESIS_HASH = "0000016894cfec6303be1a40892c88b7556cd3faf73e2691b29a24d634b2b2cf"


@dataclass
class Block:
    """A block whose proof-of-work hash covers only its header fields."""

    index: int
    timestamp: int
    previous_hash: str
    merkle_root: str
    difficulty_bits: int
    nonce: int = 0
    transactions: list[dict[str, object]] = field(default_factory=list)
    hash: str = ""
    version: int = SERIALIZATION_VERSION
    chain_id: str = CHAIN_ID

    def __post_init__(self) -> None:
        if not self.hash:
            self.hash = self.calculate_hash()

    def header_dict(self) -> dict[str, int | str]:
        """Return exactly the fields committed by the block hash."""
        return {
            "version": self.version,
            "chain_id": self.chain_id,
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "merkle_root": self.merkle_root,
            "difficulty_bits": self.difficulty_bits,
            "nonce": self.nonce,
        }

    def calculate_hash(self) -> str:
        """Recompute the block hash from its canonical header."""
        return sha256_hex(canonical_json(self.header_dict()))

    def has_valid_hash(self) -> bool:
        """Check that the stored hash still matches the current header."""
        return self.hash == self.calculate_hash()

    def to_dict(self) -> dict[str, object]:
        """Serialize the full block for storage and API responses."""
        return {
            **self.header_dict(),
            "hash": self.hash,
            "transactions": list(self.transactions),
        }


GENESIS_BLOCK = Block(
    index=0,
    timestamp=GENESIS_TIMESTAMP,
    previous_hash="0" * 64,
    merkle_root=merkle_root([]),
    difficulty_bits=INITIAL_DIFFICULTY_BITS,
    nonce=GENESIS_NONCE,
    hash=GENESIS_HASH,
    version=SERIALIZATION_VERSION,
    chain_id=CHAIN_ID,
)
