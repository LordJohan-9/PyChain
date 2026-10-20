import json

import httpx
import pytest

from tests.integration.test_peer_network import running_node_with_app, wait_for


@pytest.mark.parametrize("attempt", range(20))
def test_partition_converges_on_heavier_chain(tmp_path, attempt: int) -> None:
    with running_node_with_app(tmp_path, f"short-{attempt}") as (short_url, _):
        with running_node_with_app(tmp_path, f"long-{attempt}") as (long_url, _):
            with httpx.Client(timeout=10) as client:
                assert client.post(f"{short_url}/mine").status_code == 201
                assert client.post(f"{long_url}/mine").status_code == 201
                assert client.post(f"{long_url}/mine").status_code == 201

                connected = client.post(f"{short_url}/peers", json={"url": long_url})
                assert connected.status_code == 201
                synced = client.post(f"{short_url}/sync")
                assert synced.status_code == 200
                assert synced.json()["adopted_chains"] == 1

                short_chain = client.get(f"{short_url}/chain").json()
                long_chain = client.get(f"{long_url}/chain").json()
                assert short_chain["height"] == long_chain["height"] == 2
                assert [block["hash"] for block in short_chain["blocks"]] == [
                    block["hash"] for block in long_chain["blocks"]
                ]


def test_sync_rejects_and_strikes_peer_with_invalid_longer_chain(tmp_path) -> None:
    with running_node_with_app(tmp_path, "honest") as (honest_url, _):
        with running_node_with_app(tmp_path, "malicious") as (malicious_url, malicious_app):
            with httpx.Client(timeout=10) as client:
                assert client.post(f"{malicious_url}/mine").status_code == 201
                assert client.post(f"{malicious_url}/mine").status_code == 201
                assert (
                    client.post(f"{honest_url}/peers", json={"url": malicious_url}).status_code
                    == 201
                )

                malicious_app.state.runtime.blockchain.chain[1].nonce += 1
                sync_response = client.post(f"{honest_url}/sync")
                assert sync_response.status_code == 200
                assert sync_response.json()["adopted_chains"] == 0
                assert client.get(f"{honest_url}/status").json()["height"] == 0

                peer = next(
                    item
                    for item in client.get(f"{honest_url}/peers").json()["peers"]
                    if item["url"] == malicious_url
                )
                assert peer["strikes"] == 1


def test_reorg_requeues_still_valid_orphaned_transaction(tmp_path) -> None:
    with running_node_with_app(tmp_path, "fork-a") as (fork_a, app_a):
        with running_node_with_app(tmp_path, "fork-b") as (fork_b, app_b):
            with httpx.Client(timeout=10) as client:
                assert client.post(f"{fork_a}/peers", json={"url": fork_b}).status_code == 201
                assert client.post(f"{fork_a}/mine").status_code == 201
                assert wait_for(lambda: client.get(f"{fork_b}/status").json()["height"] == 1)

                app_a.state.runtime.peer_manager.remove_peer(fork_b)
                app_b.state.runtime.peer_manager.remove_peer(fork_a)

                transaction_response = client.post(
                    f"{fork_a}/wallet/send",
                    json={"recipient": "1" * 40, "amount": 5},
                )
                assert transaction_response.status_code == 201
                tx_id = transaction_response.json()["tx_id"]
                assert client.post(f"{fork_a}/mine").status_code == 201
                assert client.post(f"{fork_b}/mine").status_code == 201
                assert client.post(f"{fork_b}/mine").status_code == 201

                assert client.post(f"{fork_a}/peers", json={"url": fork_b}).status_code == 201
                assert client.post(f"{fork_a}/sync").status_code == 200
                assert wait_for(
                    lambda: any(
                        transaction["tx_id"] == tx_id
                        for transaction in client.get(f"{fork_a}/transactions/pending").json()
                    )
                )
                assert client.get(f"{fork_a}/status").json()["height"] == 3


def test_startup_syncs_from_configured_peer(tmp_path) -> None:
    with running_node_with_app(tmp_path, "source") as (source_url, _):
        with httpx.Client(timeout=10) as client:
            assert client.post(f"{source_url}/mine").status_code == 201
            assert client.post(f"{source_url}/mine").status_code == 201

        configured_dir = tmp_path / "configured"
        configured_dir.mkdir()
        (configured_dir / "peers.json").write_text(
            json.dumps({"version": 1, "peers": [source_url]}),
            encoding="utf-8",
        )
        with running_node_with_app(tmp_path, "configured") as (configured_url, _):
            with httpx.Client(timeout=10) as client:
                assert client.get(f"{configured_url}/status").json()["height"] == 2
