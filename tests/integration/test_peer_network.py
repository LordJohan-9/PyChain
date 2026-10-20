import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import uvicorn

from pychain.core.wallet import Wallet
from pychain.node.app import create_app


@contextmanager
def running_node(data_dir, node_name: str) -> Iterator[str]:
    with running_node_with_app(data_dir, node_name) as (url, _):
        yield url


@contextmanager
def running_node_with_app(data_dir, node_name: str):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = int(listener.getsockname()[1])
    url = f"http://127.0.0.1:{port}"
    application = create_app(data_dir / node_name, port=port)
    config = uvicorn.Config(
        application,
        log_level="error",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name=f"test-{node_name}",
        daemon=True,
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            if not thread.is_alive():
                raise RuntimeError(f"{node_name} server stopped before startup")
            time.sleep(0.02)
        if not server.started:
            raise TimeoutError(f"{node_name} server did not start")
        yield url, application
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError(f"{node_name} server did not stop")


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_handshake_discovery_and_transaction_gossip_without_loops(tmp_path) -> None:
    with running_node(tmp_path, "node-a") as node_a:
        with running_node(tmp_path, "node-b") as node_b:
            with running_node(tmp_path, "node-c") as node_c:
                with httpx.Client(timeout=5) as client:
                    assert client.post(f"{node_b}/peers", json={"url": node_c}).status_code == 201
                    connected = client.post(f"{node_a}/peers", json={"url": node_b})
                    assert connected.status_code == 201

                    for node, expected in (
                        (node_a, {node_b, node_c}),
                        (node_b, {node_a, node_c}),
                        (node_c, {node_a, node_b}),
                    ):
                        peers = client.get(f"{node}/peers").json()["peers"]
                        assert {peer["url"] for peer in peers} == expected
                    peer_b_a = next(
                        peer
                        for peer in client.get(f"{node_b}/peers").json()["peers"]
                        if peer["url"] == node_a
                    )
                    assert peer_b_a["strikes"] == 0

                    mined = client.post(f"{node_a}/mine")
                    assert mined.status_code == 201
                    assert wait_for(
                        lambda: all(
                            client.get(f"{node}/status").json()["height"] == 1
                            for node in (node_a, node_b, node_c)
                        )
                    )
                    strikes_after_block = next(
                        peer["strikes"]
                        for peer in client.get(f"{node_b}/peers").json()["peers"]
                        if peer["url"] == node_a
                    )
                    assert strikes_after_block == 0

                    recipient = Wallet.generate().address
                    submitted = client.post(
                        f"{node_a}/wallet/send",
                        json={"recipient": recipient, "amount": 10},
                    )
                    assert submitted.status_code == 201
                    transaction = submitted.json()
                    tx_id = transaction["tx_id"]
                    assert wait_for(
                        lambda: all(
                            any(
                                item["tx_id"] == tx_id
                                for item in client.get(f"{node}/transactions/pending").json()
                            )
                            for node in (node_a, node_b, node_c)
                        )
                    )
                    strikes_after_transaction = next(
                        peer["strikes"]
                        for peer in client.get(f"{node_b}/peers").json()["peers"]
                        if peer["url"] == node_a
                    )
                    assert strikes_after_transaction == 0

                    duplicate = client.post(
                        f"{node_b}/p2p/transactions",
                        json={
                            "version": 1,
                            "message_type": "transaction",
                            "sender_id": client.get(f"{node_a}/status").json()["node_id"],
                            "sender_url": node_a,
                            "payload": {
                                key: transaction[key]
                                for key in (
                                    "version",
                                    "chain_id",
                                    "type",
                                    "signature_scheme",
                                    "sender_public_key",
                                    "recipient",
                                    "amount",
                                    "nonce",
                                    "timestamp",
                                    "signature",
                                    "tx_id",
                                )
                            },
                        },
                    )
                    assert duplicate.status_code == 200
                    assert duplicate.json() == {"version": 1, "accepted": False}
                    counts = [
                        sum(
                            item["tx_id"] == tx_id
                            for item in client.get(f"{node}/transactions/pending").json()
                        )
                        for node in (node_a, node_b, node_c)
                    ]
                    assert counts == [1, 1, 1]

                    for strike in range(5):
                        response = client.post(
                            f"{node_b}/p2p/blocks",
                            json={
                                "version": 1,
                                "message_type": "block",
                                "sender_id": client.get(f"{node_a}/status").json()["node_id"],
                                "sender_url": node_a,
                                "payload": {"malformed_attempt": strike},
                            },
                        )
                        assert response.status_code == 400
                    banned_response = client.post(
                        f"{node_b}/p2p/blocks",
                        json={
                            "version": 1,
                            "message_type": "block",
                            "sender_id": client.get(f"{node_a}/status").json()["node_id"],
                            "sender_url": node_a,
                            "payload": {"malformed_attempt": "after-ban"},
                        },
                    )
                    assert banned_response.status_code == 403
                    assert node_a not in {
                        peer["url"] for peer in client.get(f"{node_b}/peers").json()["peers"]
                    }
