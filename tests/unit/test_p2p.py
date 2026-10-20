import pytest

from pychain.node.p2p import P2PMessage


def test_p2p_message_is_canonical_and_transport_independent() -> None:
    message = P2PMessage(
        "transaction",
        "node-123",
        {"tx_id": "abc", "amount": 4},
    )

    encoded = message.to_bytes()
    decoded = P2PMessage.from_bytes(encoded)

    assert decoded == message
    assert b"sender_url" not in encoded
    assert b"http://" not in encoded


def test_p2p_message_rejects_unsupported_versions_and_noncanonical_bytes() -> None:
    with pytest.raises(ValueError, match="unsupported P2P message version"):
        P2PMessage("block", "node-123", {}, version=2)

    with pytest.raises(ValueError, match="canonical JSON"):
        P2PMessage.from_bytes(
            b'{ "version":1,"message_type":"block","sender_id":"node-123","payload":{} }'
        )
