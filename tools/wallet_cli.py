"""Create wallets, print addresses, and sign offline PyChain transactions."""

import argparse
import json
from pathlib import Path

from pychain.core.wallet import Wallet, WalletError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a PyChain wallet.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="generate a wallet and save it to disk")
    new_parser.add_argument("--wallet", type=Path, default=Path("data") / "default" / "wallet.json")
    new_parser.add_argument("--force", action="store_true", help="replace an existing wallet file")

    address_parser = subparsers.add_parser("address", help="print a wallet's public address")
    address_parser.add_argument(
        "--wallet",
        type=Path,
        default=Path("data") / "default" / "wallet.json",
    )

    sign_parser = subparsers.add_parser("sign", help="sign and print a transfer as JSON")
    sign_parser.add_argument(
        "--wallet",
        type=Path,
        default=Path("data") / "default" / "wallet.json",
    )
    sign_parser.add_argument("--recipient", required=True, help="40-character recipient address")
    sign_parser.add_argument("--amount", type=int, required=True, help="positive whole-coin amount")
    sign_parser.add_argument(
        "--nonce",
        type=int,
        required=True,
        help="sender's next transaction nonce",
    )
    sign_parser.add_argument(
        "--timestamp",
        type=int,
        help="transaction timestamp (defaults to current time)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "new":
            if args.wallet.exists() and not args.force:
                parser.error(f"{args.wallet} already exists; use --force to replace it")
            wallet = Wallet.generate()
            wallet.save(args.wallet)
            print(
                json.dumps(
                    {"address": wallet.address, "wallet_file": str(args.wallet)},
                    indent=2,
                )
            )
        elif args.command == "address":
            print(Wallet.load(args.wallet).address)
        else:
            wallet = Wallet.load(args.wallet)
            transaction = wallet.create_transaction(
                recipient=args.recipient,
                amount=args.amount,
                nonce=args.nonce,
                timestamp=args.timestamp,
            )
            print(transaction.to_json())
    except (OSError, WalletError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
