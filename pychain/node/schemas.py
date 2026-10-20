"""Pydantic request and response models for the node REST API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TransactionRequest(StrictModel):
    version: int
    chain_id: str
    type: Literal["transfer"]
    signature_scheme: str
    sender_public_key: str
    recipient: str
    amount: int = Field(gt=0)
    nonce: int = Field(ge=0)
    timestamp: int = Field(ge=0)
    signature: str
    tx_id: str


class WalletSendRequest(StrictModel):
    recipient: str
    amount: int = Field(gt=0)


class TransactionResponse(StrictModel):
    version: int
    chain_id: str
    type: str
    signature_scheme: str
    sender_public_key: str
    recipient: str
    amount: int
    nonce: int
    timestamp: int
    signature: str
    tx_id: str
    status: str
    block_index: int | None = None


class BlockResponse(StrictModel):
    version: int
    chain_id: str
    index: int
    timestamp: int
    previous_hash: str
    merkle_root: str
    difficulty_bits: int
    nonce: int
    hash: str
    transactions: list[dict[str, object]]


class ChainResponse(StrictModel):
    version: int
    blocks: list[BlockResponse]
    height: int
    total_work: int


class StatusResponse(StrictModel):
    node_id: str
    url: str
    height: int
    tip_hash: str
    difficulty_bits: int
    total_work: int
    pending_count: int
    peer_count: int
    mining: bool
    mining_nonce: int
    mining_attempts: int
    estimated_hash_rate: float


class BalanceResponse(StrictModel):
    address: str
    balance: int
    nonce: int


class WalletResponse(StrictModel):
    address: str
    balance: int


class ValidationResponse(StrictModel):
    valid: bool
    block_index: int | None = None
    rule: str | None = None
    message: str | None = None


class HealthResponse(StrictModel):
    status: str


class MiningStatusResponse(StrictModel):
    mining: bool
    mode: str


class PeerAddRequest(StrictModel):
    url: str


class PeerInfo(StrictModel):
    version: int
    node_id: str
    url: str
    height: int
    total_work: int
    last_seen: float
    failures: int
    strikes: int


class PeersResponse(StrictModel):
    peers: list[PeerInfo]


class PeerHelloRequest(StrictModel):
    version: int
    message_type: Literal["hello"]
    sender_id: str
    payload: dict[str, object]


class PeerHelloResponse(StrictModel):
    version: int
    message_type: Literal["hello"]
    sender_id: str
    payload: dict[str, object]


class PeerMessage(StrictModel):
    version: int
    message_type: Literal["transaction", "block"]
    sender_id: str
    sender_url: str
    payload: dict[str, object]


class PeerMessageResponse(StrictModel):
    version: int
    accepted: bool


class PeerAddedResponse(StrictModel):
    peer: PeerInfo


class SyncResponse(StrictModel):
    adopted_chains: int
    height: int
    total_work: int
