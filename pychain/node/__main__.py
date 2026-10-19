"""Run a PyChain node from the command line."""

import argparse
from pathlib import Path

import uvicorn

from pychain.node.app import create_app
from pychain.node.storage import JsonStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local PyChain blockchain node.")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface to bind (default: localhost)",
    )
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data") / "default",
        help="directory for this node's chain, wallet, and peer files",
    )
    parser.add_argument(
        "--peers",
        nargs="*",
        default=None,
        metavar="URL",
        help="peer node URLs to connect to at startup",
    )
    parser.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default="info",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.peers is not None:
        JsonStorage(args.data_dir).save_peers(args.peers)
    uvicorn.run(
        create_app(args.data_dir, port=args.port),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
