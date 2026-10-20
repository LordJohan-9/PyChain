"""Start a three-node PyChain demo network and open the node explorer."""

import argparse
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTS = (8000, 8001, 8002)
DEFAULT_DATA_ROOT = ROOT / "data" / "demo-v1"


def wait_for_node(process: subprocess.Popen[bytes], port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"node on port {port} exited with code {process.returncode}")
        try:
            response = httpx.get(f"{url}/health", timeout=0.5)
            if response.is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise TimeoutError(f"node on port {port} did not become ready within {timeout:g} seconds")


def connect_demo_peers(ports: tuple[int, ...]) -> None:
    for index, port in enumerate(ports):
        next_port = ports[(index + 1) % len(ports)]
        response = httpx.post(
            f"http://127.0.0.1:{port}/peers",
            json={"url": f"http://127.0.0.1:{next_port}"},
            timeout=5.0,
        )
        response.raise_for_status()


def stop_nodes(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help="directory containing persistent data for the three nodes",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="do not open the UI automatically",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for index, port in enumerate(DEFAULT_PORTS, start=1):
            data_dir = args.data_root.resolve() / f"node{index}"
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "pychain.node",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--data-dir",
                        str(data_dir),
                    ],
                    cwd=ROOT,
                )
            )
        for process, port in zip(processes, DEFAULT_PORTS, strict=True):
            wait_for_node(process, port)
        connect_demo_peers(DEFAULT_PORTS)
        ui_url = f"http://127.0.0.1:{DEFAULT_PORTS[0]}/ui"
        print(f"PyChain demo is ready at {ui_url}. Press Ctrl+C to stop all nodes.")
        if not args.no_browser:
            webbrowser.open(ui_url)
        while any(process.poll() is None for process in processes):
            time.sleep(1)
        raise RuntimeError("a demo node exited unexpectedly")
    except KeyboardInterrupt:
        print("\nStopping the demo nodes...")
    except (httpx.HTTPError, OSError, RuntimeError, TimeoutError) as error:
        print(f"Could not start the demo: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    finally:
        stop_nodes(processes)


if __name__ == "__main__":
    main()
