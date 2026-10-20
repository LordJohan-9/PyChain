"""Transport-independent, versioned P2P message envelope."""

from dataclasses import dataclass
from typing import Any

from pychain.config import SERIALIZATION_VERSION
from pychain.core.hashing import canonical_json


@dataclass(frozen=True)
class P2PMessage:
    """A protocol message independent of its HTTP, socket, or binary transport."""

    message_type: str
    sender_id: str
    payload: dict[str, Any]
    version: int = SERIALIZATION_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != SERIALIZATION_VERSION:
            raise ValueError(f"unsupported P2P message version {self.version}")
        if (
            not isinstance(self.message_type, str)
            or not self.message_type
            or not isinstance(self.sender_id, str)
            or not self.sender_id
        ):
            raise ValueError("P2P message type and sender ID are required")
        if not isinstance(self.payload, dict):
            raise ValueError("P2P message payload must be an object")

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "message_type": self.message_type,
            "sender_id": self.sender_id,
            "payload": self.payload,
        }

    def to_bytes(self) -> bytes:
        """Encode the canonical message bytes usable by any transport."""
        return canonical_json(self.to_dict()).encode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> "P2PMessage":
        if not isinstance(value, dict) or set(value) != {
            "version",
            "message_type",
            "sender_id",
            "payload",
        }:
            raise ValueError("P2P message has missing or unexpected fields")
        if (
            type(value["version"]) is not int
            or not isinstance(value["message_type"], str)
            or not isinstance(value["sender_id"], str)
            or not isinstance(value["payload"], dict)
        ):
            raise ValueError("P2P message has incorrectly typed fields")
        return cls(
            message_type=value["message_type"],
            sender_id=value["sender_id"],
            payload=value["payload"],
            version=value["version"],
        )

    @classmethod
    def from_bytes(cls, value: bytes) -> "P2PMessage":
        import json

        try:
            decoded = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("P2P message bytes are not valid UTF-8 JSON") from error
        message = cls.from_dict(decoded)
        if message.to_bytes() != value:
            raise ValueError("P2P message bytes must use canonical JSON serialization")
        return message
