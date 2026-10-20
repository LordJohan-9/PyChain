import time

from fastapi.testclient import TestClient

from pychain.core.wallet import Wallet
from pychain.node.app import create_app


def test_read_endpoints_report_genesis_and_openapi_routes(tmp_path) -> None:
    with TestClient(create_app(tmp_path)) as client:
        status_response = client.get("/status")
        assert status_response.status_code == 200
        assert status_response.json()["height"] == 0
        assert status_response.json()["pending_count"] == 0
        assert status_response.json()["mining_attempts"] == 0

        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/chain").json()["height"] == 0
        assert client.get("/blocks/latest").json()["index"] == 0
        assert client.get("/blocks/0").json()["index"] == 0
        assert client.get("/blocks/1").status_code == 404
        assert client.get("/validate").json() == {"valid": True}
        assert client.get("/ui/").status_code == 200
        assert "Dashboard" in client.get("/ui/").text
        assert "setInterval(refresh, 3000)" in client.get("/ui/app.js").text
        assert "--green" in client.get("/ui/styles.css").text

        openapi = client.get("/openapi.json").json()
        assert openapi["info"]["title"] == "PyChain"
        assert "/wallet/send" in openapi["paths"]
        assert "/mine" in openapi["paths"]
        assert "/peers" in openapi["paths"]
        assert "/p2p/hello" in openapi["paths"]


def test_peer_can_be_removed_through_api(tmp_path) -> None:
    with TestClient(create_app(tmp_path)) as client:
        client.app.state.runtime.peer_manager.record_hello(
            "remote-node",
            "http://127.0.0.1:8001",
            0,
            0,
        )

        removed = client.delete("/peers", params={"url": "http://127.0.0.1:8001"})

        assert removed.status_code == 204
        assert client.get("/peers").json() == {"peers": []}
        assert (
            client.delete(
                "/peers",
                params={"url": "http://127.0.0.1:8001"},
            ).status_code
            == 404
        )
        assert client.delete("/peers", params={"url": "not-a-url"}).status_code == 400


def test_p2p_body_size_limit_rejects_large_request(tmp_path) -> None:
    with TestClient(create_app(tmp_path, p2p_max_body_bytes=32)) as client:
        response = client.post(
            "/p2p/hello",
            json={
                "node_id": "remote-node",
                "url": "http://127.0.0.1:8001",
                "height": 0,
                "total_work": 0,
            },
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "peer request body is too large"}


def test_p2p_rate_limit_is_per_client_and_returns_retry_after(tmp_path) -> None:
    with TestClient(create_app(tmp_path, p2p_requests_per_window=1)) as client:
        payload = {
            "version": 1,
            "message_type": "hello",
            "sender_id": "remote-node",
            "payload": {
                "node_id": "remote-node",
                "url": "http://127.0.0.1:8001",
                "height": 0,
                "total_work": 0,
            },
        }
        first = client.post("/p2p/hello", json=payload)
        limited = client.post("/p2p/hello", json=payload)
        health = client.get("/health")

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.json() == {"detail": "peer request rate limit exceeded"}
    assert int(limited.headers["retry-after"]) > 0
    assert health.status_code == 200


def test_mine_wallet_balance_and_balance_endpoint(tmp_path) -> None:
    with TestClient(create_app(tmp_path)) as client:
        wallet = client.get("/wallet").json()
        mined = client.post("/mine")

        assert mined.status_code == 201
        assert mined.json()["index"] == 1
        assert client.get("/wallet").json()["balance"] == 50
        assert client.get(f"/balances/{wallet['address']}").json() == {
            "address": wallet["address"],
            "balance": 50,
            "nonce": 0,
        }
        status_response = client.get("/status").json()
        assert status_response["height"] == 1
        assert status_response["estimated_hash_rate"] > 0


def test_wallet_send_pending_confirmed_and_invalid_requests(tmp_path) -> None:
    with TestClient(create_app(tmp_path)) as client:
        sender = client.get("/wallet").json()
        assert client.post("/mine").status_code == 201
        recipient = Wallet.generate().address

        sent = client.post(
            "/wallet/send",
            json={"recipient": recipient, "amount": 10},
        )
        assert sent.status_code == 201
        tx_id = sent.json()["tx_id"]
        assert client.get("/transactions/pending").json()[0]["tx_id"] == tx_id
        assert client.get(f"/transactions/{tx_id}").json()["status"] == "pending"

        mined = client.post("/mine")
        assert mined.status_code == 201
        assert client.get(f"/transactions/{tx_id}").json()["status"] == "confirmed"
        assert client.get(f"/transactions/{tx_id}").json()["block_index"] == 2
        assert client.get(f"/balances/{sender['address']}").json()["balance"] == 90
        assert client.get(f"/balances/{recipient}").json()["balance"] == 10

        bad_amount = client.post(
            "/wallet/send",
            json={"recipient": recipient, "amount": 0},
        )
        assert bad_amount.status_code == 422
        bad_recipient = client.post(
            "/wallet/send",
            json={"recipient": "not-an-address", "amount": 1},
        )
        assert bad_recipient.status_code == 400
        overspend = client.post(
            "/wallet/send",
            json={"recipient": recipient, "amount": 100},
        )
        assert overspend.status_code == 400
        assert client.get("/balances/not-an-address").status_code == 400
        assert client.get(f"/transactions/{'f' * 64}").status_code == 404


def test_submit_signed_transaction_validates_signature_and_duplicates(tmp_path) -> None:
    with TestClient(create_app(tmp_path)) as client:
        wallet_response = client.get("/wallet").json()
        client.post("/mine")
        transaction = Wallet.load(tmp_path / "wallet.json").create_transaction(
            recipient=Wallet.generate().address,
            amount=3,
            nonce=0,
        )

        response = client.post("/transactions", json=transaction.to_dict())
        assert response.status_code == 201
        duplicate = client.post("/transactions", json=transaction.to_dict())
        assert duplicate.status_code == 409

        bad_signature = transaction.to_dict()
        bad_signature["signature"] = "00" * 64
        invalid = client.post("/transactions", json=bad_signature)
        assert invalid.status_code == 400

        wrong_schema = client.post("/transactions", json={"amount": 3})
        assert wrong_schema.status_code == 422
        assert wallet_response["address"]


def test_mining_controls_start_and_stop_auto_mining(tmp_path) -> None:
    with TestClient(create_app(tmp_path)) as client:
        started = client.post("/mine/start")
        assert started.status_code == 200
        assert started.json() == {"mining": True, "mode": "auto"}
        duplicate = client.post("/mine/start")
        assert duplicate.status_code == 409

        stopped = client.post("/mine/stop")
        assert stopped.status_code == 200
        assert stopped.json() == {"mining": False, "mode": "stopped"}
        time.sleep(0.05)
        assert client.get("/status").json()["mining"] is False


def test_validate_reports_the_first_corrupted_block(tmp_path) -> None:
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/mine").status_code == 201
        runtime = client.app.state.runtime
        runtime.blockchain.chain[1].nonce += 1

        response = client.get("/validate")

        assert response.status_code == 200
        assert response.json()["valid"] is False
        assert response.json()["block_index"] == 1
        assert response.json()["rule"] == "hash"
