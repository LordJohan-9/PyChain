import json

import pytest

from pychain.core.wallet import (
    Wallet,
    WalletError,
    address_from_public_key,
    is_valid_address,
)


def test_generated_wallet_has_matching_secp256k1_identity() -> None:
    wallet = Wallet.generate()

    assert len(wallet.private_key) == 64
    assert len(wallet.public_key) == 128
    assert wallet.address == address_from_public_key(wallet.public_key)
    assert is_valid_address(wallet.address)


def test_wallet_save_and_load_round_trip(tmp_path) -> None:
    wallet = Wallet.generate()
    wallet_path = tmp_path / "data" / "node1" / "wallet.json"

    wallet.save(wallet_path)
    loaded_wallet = Wallet.load(wallet_path)

    assert loaded_wallet == wallet
    assert json.loads(wallet_path.read_text(encoding="utf-8")) == wallet.to_dict()


def test_wallet_load_rejects_address_key_mismatch(tmp_path) -> None:
    wallet = Wallet.generate()
    wallet_path = tmp_path / "wallet.json"
    wallet_path.write_text(
        json.dumps({**wallet.to_dict(), "address": "0" * 40}),
        encoding="utf-8",
    )

    with pytest.raises(WalletError, match="address does not match"):
        Wallet.load(wallet_path)


def test_wallet_load_rejects_unsupported_version(tmp_path) -> None:
    wallet = Wallet.generate()
    wallet_path = tmp_path / "wallet.json"
    wallet_path.write_text(
        json.dumps({**wallet.to_dict(), "version": 2}),
        encoding="utf-8",
    )

    with pytest.raises(WalletError, match="unsupported wallet version"):
        Wallet.load(wallet_path)


def test_wallet_load_rejects_public_private_key_mismatch(tmp_path) -> None:
    wallet = Wallet.generate()
    other_wallet = Wallet.generate()
    wallet_path = tmp_path / "wallet.json"
    wallet_path.write_text(
        json.dumps(
            {
                "version": 1,
                "signature_scheme": wallet.signature_scheme,
                "private_key": wallet.private_key,
                "public_key": other_wallet.public_key,
                "address": other_wallet.address,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WalletError, match="public_key does not match"):
        Wallet.load(wallet_path)


def test_wallet_rejects_malformed_public_key() -> None:
    with pytest.raises(WalletError, match="valid secp256k1 public key"):
        address_from_public_key("0" * 128)


def test_wallet_load_rejects_malformed_json_shape(tmp_path) -> None:
    wallet_path = tmp_path / "wallet.json"
    wallet_path.write_text('{"private_key":"only"}', encoding="utf-8")

    with pytest.raises(WalletError, match="must contain"):
        Wallet.load(wallet_path)


def test_missing_wallet_file_raises_clear_error(tmp_path) -> None:
    with pytest.raises(WalletError, match="could not read wallet file"):
        Wallet.load(tmp_path / "missing.json")
