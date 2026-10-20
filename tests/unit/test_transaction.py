import pytest

from pychain.config import BLOCK_REWARD
from pychain.core.transaction import (
    COINBASE,
    Transaction,
    build_coinbase_transaction,
)
from pychain.core.wallet import Wallet


def make_transaction(
    wallet: Wallet,
    recipient: str,
    *,
    amount: int = 12,
    nonce: int = 3,
    timestamp: int = 1_700_000_000,
) -> Transaction:
    return wallet.create_transaction(
        recipient=recipient,
        amount=amount,
        nonce=nonce,
        timestamp=timestamp,
    )


def test_signed_transaction_verifies_and_json_round_trips() -> None:
    wallet = Wallet.generate()
    recipient = Wallet.generate()
    transaction = make_transaction(wallet, recipient.address)

    decoded = Transaction.from_json(transaction.to_json())

    assert decoded.to_dict() == transaction.to_dict()
    assert decoded.verify()
    assert decoded.sender_address == wallet.address
    assert decoded.version == 1
    assert decoded.transaction_type == "transfer"
    assert decoded.signature_scheme == "secp256k1"


@pytest.mark.parametrize(
    ("field_name", "new_value"),
    [
        ("amount", 13),
        ("recipient", "0" * 40),
        ("nonce", 4),
    ],
)
def test_modified_signed_transaction_fields_fail_verification(
    field_name: str,
    new_value: int | str,
) -> None:
    wallet = Wallet.generate()
    transaction = make_transaction(wallet, Wallet.generate().address)
    setattr(transaction, field_name, new_value)

    assert not transaction.verify()


def test_signature_from_wrong_wallet_is_rejected() -> None:
    sender = Wallet.generate()
    other_wallet = Wallet.generate()
    transaction = Transaction(
        sender_public_key=sender.public_key,
        recipient=Wallet.generate().address,
        amount=1,
        nonce=0,
        timestamp=1_700_000_000,
    )

    with pytest.raises(ValueError, match="does not match transaction sender"):
        transaction.sign(other_wallet)


def test_signature_does_not_verify_under_a_different_public_key() -> None:
    sender = Wallet.generate()
    transaction = make_transaction(sender, Wallet.generate().address)
    transaction.sender_public_key = Wallet.generate().public_key

    assert not transaction.verify()


def test_expected_sender_address_must_match_public_key() -> None:
    sender = Wallet.generate()
    transaction = make_transaction(sender, Wallet.generate().address)

    assert transaction.verify(expected_sender_address=sender.address)
    assert not transaction.verify(expected_sender_address=Wallet.generate().address)


def test_chain_id_is_committed_to_transaction_id_and_signature() -> None:
    wallet = Wallet.generate()
    recipient = Wallet.generate()
    transaction = wallet.create_transaction(
        recipient.address,
        1,
        nonce=0,
        timestamp=1_700_000_000,
        chain_id="testnet-a",
    )
    replay = wallet.create_transaction(
        recipient.address,
        1,
        nonce=0,
        timestamp=1_700_000_000,
        chain_id="testnet-b",
    )

    assert transaction.verify(expected_chain_id="testnet-a")
    assert not transaction.verify(expected_chain_id="testnet-b")
    assert transaction.tx_id != replay.tx_id
    assert transaction.to_dict()["chain_id"] == "testnet-a"


def test_transaction_rejects_invalid_amount_and_recipient_when_signing() -> None:
    wallet = Wallet.generate()
    invalid = Transaction(
        sender_public_key=wallet.public_key,
        recipient="not-an-address",
        amount=0,
        nonce=0,
        timestamp=1_700_000_000,
    )

    with pytest.raises(ValueError, match="invalid transfer fields"):
        invalid.sign(wallet)


def test_coinbase_builder_uses_fixed_reward_and_has_no_signature() -> None:
    miner = Wallet.generate()
    transaction = build_coinbase_transaction(miner.address, timestamp=1_700_000_000)

    assert transaction.is_coinbase
    assert transaction.sender_public_key == COINBASE
    assert transaction.recipient == miner.address
    assert transaction.amount == BLOCK_REWARD
    assert transaction.signature == ""
    assert transaction.nonce == 0
    assert transaction.tx_id == transaction.calculate_tx_id()


def test_coinbase_builder_rejects_invalid_recipient() -> None:
    with pytest.raises(ValueError, match="recipient"):
        build_coinbase_transaction("invalid")


def test_json_parser_rejects_missing_fields_and_wrong_types() -> None:
    wallet = Wallet.generate()
    valid_payload = Transaction.create(
        wallet,
        recipient=Wallet.generate().address,
        amount=1,
        nonce=0,
        timestamp=1_700_000_000,
    ).to_dict()
    invalid_payloads = [
        {key: value for key, value in valid_payload.items() if key != "signature"},
        {key: value for key, value in valid_payload.items() if key != "version"},
        {**valid_payload, "amount": True},
        {**valid_payload, "tx_id": ""},
        {**valid_payload, "unexpected": "field"},
    ]

    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            Transaction.from_dict(payload)
