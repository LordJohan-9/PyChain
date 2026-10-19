import json
import sys

import pytest

from pychain.core.transaction import Transaction
from tools import wallet_cli


def test_wallet_cli_new_address_and_sign(tmp_path, monkeypatch, capsys) -> None:
    wallet_path = tmp_path / "wallet.json"

    monkeypatch.setattr(
        sys,
        "argv",
        ["wallet_cli", "new", "--wallet", str(wallet_path)],
    )
    wallet_cli.main()
    created = json.loads(capsys.readouterr().out)
    assert created["wallet_file"] == str(wallet_path)
    assert "private_key" not in created

    monkeypatch.setattr(
        sys,
        "argv",
        ["wallet_cli", "address", "--wallet", str(wallet_path)],
    )
    wallet_cli.main()
    address = capsys.readouterr().out.strip()
    assert address == created["address"]

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "wallet_cli",
            "sign",
            "--wallet",
            str(wallet_path),
            "--recipient",
            "a" * 40,
            "--amount",
            "7",
            "--nonce",
            "3",
            "--timestamp",
            "100",
        ],
    )
    wallet_cli.main()
    signed_json = capsys.readouterr().out
    signed = json.loads(signed_json)
    assert signed["recipient"] == "a" * 40
    assert signed["amount"] == 7
    assert signed["nonce"] == 3
    assert signed["timestamp"] == 100
    assert Transaction.from_json(signed_json).verify()


def test_wallet_cli_new_does_not_overwrite_existing_wallet(tmp_path, monkeypatch) -> None:
    wallet_path = tmp_path / "wallet.json"
    wallet_path.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["wallet_cli", "new", "--wallet", str(wallet_path)],
    )

    with pytest.raises(SystemExit) as error:
        wallet_cli.main()

    assert error.value.code == 2
    assert wallet_path.read_text(encoding="utf-8") == "keep"
