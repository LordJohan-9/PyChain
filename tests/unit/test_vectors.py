import json
from pathlib import Path

from pychain.core.block import GENESIS_BLOCK, Block
from pychain.core.chain import Blockchain
from pychain.core.hashing import canonical_json, sha256_hex
from pychain.core.signatures import get_signature_scheme
from pychain.core.transaction import Transaction
from pychain.core.wallet import Wallet

VECTORS_PATH = Path(__file__).parent.parent / "vectors" / "canonical_vectors.json"


def test_canonical_vectors_are_reproducible() -> None:
    vectors = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    hashes = vectors["hashes"]

    sender_key = "00" * 31 + "01"
    recipient_key = "00" * 31 + "02"
    scheme = get_signature_scheme("secp256k1")
    sender_public_key = scheme.public_key_from_private(sender_key)
    recipient_public_key = scheme.public_key_from_private(recipient_key)
    sender = Wallet(
        sender_key,
        sender_public_key,
        scheme.derive_address(sender_public_key),
    )
    recipient = Wallet(
        recipient_key,
        recipient_public_key,
        scheme.derive_address(recipient_public_key),
    )
    transfer = sender.create_transaction(
        recipient.address,
        7,
        nonce=0,
        timestamp=1_700_000_001,
    )

    assert transfer.to_dict() == vectors["transfer"]
    assert transfer.verify()
    assert transfer.tx_id == hashes["transfer_tx_id"]
    assert sha256_hex(canonical_json(transfer.to_dict())) == hashes["transfer_canonical_sha256"]

    genesis = Block(
        index=vectors["genesis"]["index"],
        timestamp=vectors["genesis"]["timestamp"],
        previous_hash=vectors["genesis"]["previous_hash"],
        merkle_root=vectors["genesis"]["merkle_root"],
        difficulty_bits=vectors["genesis"]["difficulty_bits"],
        nonce=vectors["genesis"]["nonce"],
        transactions=vectors["genesis"]["transactions"],
        hash=vectors["genesis"]["hash"],
        version=vectors["genesis"]["version"],
        chain_id=vectors["genesis"]["chain_id"],
    )
    sample_block = Block(
        index=vectors["sample_block"]["index"],
        timestamp=vectors["sample_block"]["timestamp"],
        previous_hash=vectors["sample_block"]["previous_hash"],
        merkle_root=vectors["sample_block"]["merkle_root"],
        difficulty_bits=vectors["sample_block"]["difficulty_bits"],
        nonce=vectors["sample_block"]["nonce"],
        transactions=vectors["sample_block"]["transactions"],
        hash=vectors["sample_block"]["hash"],
        version=vectors["sample_block"]["version"],
        chain_id=vectors["sample_block"]["chain_id"],
    )

    assert genesis.to_dict() == GENESIS_BLOCK.to_dict()
    assert sample_block.to_dict() == vectors["sample_block"]
    assert sample_block.calculate_hash() == hashes["sample_block_hash"]
    assert Blockchain([genesis, sample_block], clock=lambda: 1_700_000_001).validate_chain() is None
    assert Transaction.from_dict(vectors["coinbase"]).tx_id == hashes["coinbase_tx_id"]
