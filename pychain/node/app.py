"""FastAPI application exposing the PyChain node and wallet."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from pychain.core.mempool import MempoolError
from pychain.core.transaction import Transaction
from pychain.core.wallet import is_valid_address
from pychain.node.hardening import (
    MAX_P2P_BODY_BYTES,
    P2P_RATE_WINDOW_SECONDS,
    P2P_REQUESTS_PER_WINDOW,
    P2PRequestLimitMiddleware,
)
from pychain.node.miner import MiningCancelled, MiningError, NodeRuntime
from pychain.node.schemas import (
    BalanceResponse,
    BlockResponse,
    ChainResponse,
    HealthResponse,
    MiningStatusResponse,
    PeerAddedResponse,
    PeerAddRequest,
    PeerHelloRequest,
    PeerHelloResponse,
    PeerInfo,
    PeerMessage,
    PeerMessageResponse,
    PeersResponse,
    StatusResponse,
    SyncResponse,
    TransactionRequest,
    TransactionResponse,
    ValidationResponse,
    WalletResponse,
    WalletSendRequest,
)

logger = logging.getLogger(__name__)


def create_app(
    data_dir: str | Path = Path("data") / "default",
    *,
    port: int = 8000,
    p2p_max_body_bytes: int = MAX_P2P_BODY_BYTES,
    p2p_requests_per_window: int = P2P_REQUESTS_PER_WINDOW,
    p2p_rate_window_seconds: int = P2P_RATE_WINDOW_SECONDS,
) -> FastAPI:
    """Create an API application with an isolated on-disk node data directory."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        runtime = NodeRuntime(data_dir, port=port)
        application.state.runtime = runtime
        runtime.peer_manager.start()
        try:
            yield
        finally:
            runtime.close()

    application = FastAPI(
        title="PyChain",
        description="A local blockchain node and block explorer API.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        P2PRequestLimitMiddleware,
        max_body_bytes=p2p_max_body_bytes,
        requests_per_window=p2p_requests_per_window,
        window_seconds=p2p_rate_window_seconds,
    )

    def runtime_for(request: Request) -> NodeRuntime:
        return cast(NodeRuntime, request.app.state.runtime)

    def transaction_response(
        transaction: Transaction,
        transaction_status: str,
        block_index: int | None = None,
    ) -> TransactionResponse:
        return TransactionResponse.model_validate(
            {
                **transaction.to_dict(),
                "status": transaction_status,
                "block_index": block_index,
            }
        )

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/status", response_model=StatusResponse)
    async def get_status(request: Request) -> StatusResponse:
        runtime = runtime_for(request)
        with runtime.state_lock:
            chain = runtime.blockchain
            return StatusResponse(
                node_id=runtime.node_id,
                url=runtime.url,
                height=chain.height,
                tip_hash=chain.tip.hash,
                difficulty_bits=chain.tip.difficulty_bits,
                total_work=chain.total_work,
                pending_count=len(runtime.mempool),
                peer_count=len(runtime.peers),
                mining=runtime.is_mining,
                mining_nonce=runtime.mining_nonce,
                mining_attempts=runtime.mining_attempts,
                estimated_hash_rate=runtime.estimated_hash_rate,
            )

    @application.get("/chain", response_model=ChainResponse)
    async def get_chain(request: Request) -> ChainResponse:
        runtime = runtime_for(request)
        with runtime.state_lock:
            chain = runtime.blockchain
            return ChainResponse(
                blocks=[BlockResponse.model_validate(block.to_dict()) for block in chain.chain],
                height=chain.height,
                total_work=chain.total_work,
            )

    @application.get("/blocks/latest", response_model=BlockResponse)
    async def get_latest_block(request: Request) -> BlockResponse:
        runtime = runtime_for(request)
        with runtime.state_lock:
            return BlockResponse.model_validate(runtime.blockchain.tip.to_dict())

    @application.get("/blocks/{index}", response_model=BlockResponse)
    async def get_block(index: int, request: Request) -> BlockResponse:
        runtime = runtime_for(request)
        with runtime.state_lock:
            if index < 0 or index > runtime.blockchain.height:
                raise HTTPException(status_code=404, detail="block not found")
            return BlockResponse.model_validate(runtime.blockchain.chain[index].to_dict())

    @application.get("/transactions/pending", response_model=list[TransactionResponse])
    async def get_pending_transactions(request: Request) -> list[TransactionResponse]:
        runtime = runtime_for(request)
        with runtime.state_lock:
            return [
                transaction_response(transaction, "pending")
                for transaction in runtime.mempool.transactions
            ]

    @application.get("/transactions/{tx_id}", response_model=TransactionResponse)
    async def get_transaction(tx_id: str, request: Request) -> TransactionResponse:
        runtime = runtime_for(request)
        with runtime.state_lock:
            for transaction in runtime.mempool.transactions:
                if transaction.tx_id == tx_id:
                    return transaction_response(transaction, "pending")
            for block in runtime.blockchain.chain:
                for payload in block.transactions:
                    transaction = Transaction.from_dict(payload)
                    if transaction.tx_id == tx_id:
                        return transaction_response(transaction, "confirmed", block.index)
        raise HTTPException(status_code=404, detail="transaction not found")

    @application.post(
        "/transactions",
        response_model=TransactionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def submit_transaction(
        payload: TransactionRequest,
        request: Request,
        response: Response,
    ) -> TransactionResponse:
        runtime = runtime_for(request)
        transaction = Transaction.from_dict(payload.model_dump())
        if not transaction.verify():
            raise HTTPException(
                status_code=400,
                detail="transaction has invalid fields, ID, sender, or signature",
            )
        with runtime.state_lock:
            confirmed_ids = {
                item.get("tx_id")
                for block in runtime.blockchain.chain
                for item in block.transactions
            }
            if transaction.tx_id in confirmed_ids or transaction.tx_id in runtime.mempool:
                raise HTTPException(status_code=409, detail="transaction already known")
            try:
                runtime.mempool.add(transaction)
            except MempoolError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
        response.status_code = status.HTTP_201_CREATED
        logger.info("accepted transaction %s", transaction.tx_id)
        await run_in_threadpool(runtime.peer_manager.gossip_transaction, transaction.to_dict())
        return transaction_response(transaction, "pending")

    @application.post(
        "/mine",
        response_model=BlockResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def mine_block(request: Request) -> BlockResponse:
        runtime = runtime_for(request)
        try:
            block = await run_in_threadpool(runtime.mine_one)
        except MiningCancelled as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except MiningError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return BlockResponse.model_validate(block)

    @application.post("/mine/start", response_model=MiningStatusResponse)
    async def start_mining(request: Request) -> MiningStatusResponse:
        runtime = runtime_for(request)
        try:
            runtime.start_auto_mining()
        except MiningError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return MiningStatusResponse(mining=True, mode="auto")

    @application.post("/mine/stop", response_model=MiningStatusResponse)
    async def stop_mining(request: Request) -> MiningStatusResponse:
        runtime_for(request).stop_mining()
        return MiningStatusResponse(mining=False, mode="stopped")

    @application.post("/sync", response_model=SyncResponse)
    async def sync_peers(request: Request) -> SyncResponse:
        runtime = runtime_for(request)
        adopted = await run_in_threadpool(runtime.peer_manager.sync_all)
        with runtime.state_lock:
            return SyncResponse(
                adopted_chains=adopted,
                height=runtime.blockchain.height,
                total_work=runtime.blockchain.total_work,
            )

    @application.get("/peers", response_model=PeersResponse)
    async def get_peers(request: Request) -> PeersResponse:
        runtime = runtime_for(request)
        peers = await run_in_threadpool(lambda: runtime.peer_manager.peers)
        return PeersResponse(peers=[PeerInfo.model_validate(peer.to_dict()) for peer in peers])

    @application.post("/peers", response_model=PeerAddedResponse, status_code=201)
    async def add_peer(payload: PeerAddRequest, request: Request) -> PeerAddedResponse:
        runtime = runtime_for(request)
        try:
            peer = await run_in_threadpool(runtime.peer_manager.add_peer, payload.url)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except (httpx.HTTPError, OSError) as error:
            logger.warning("peer handshake failed for %s: %s", payload.url, error)
            raise HTTPException(status_code=502, detail="peer handshake failed") from error
        logger.info("connected peer %s", peer.url)
        return PeerAddedResponse(peer=PeerInfo.model_validate(peer.to_dict()))

    @application.delete("/peers", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_peer(url: str, request: Request) -> Response:
        runtime = runtime_for(request)
        try:
            removed = await run_in_threadpool(runtime.peer_manager.remove_peer, url)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not removed:
            raise HTTPException(status_code=404, detail="peer not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.post("/p2p/hello", response_model=PeerHelloResponse)
    async def p2p_hello(payload: PeerHelloRequest, request: Request) -> PeerHelloResponse:
        runtime = runtime_for(request)
        if payload.height < 0 or payload.total_work < 0:
            raise HTTPException(status_code=400, detail="height and total_work cannot be negative")
        try:
            await run_in_threadpool(
                runtime.peer_manager.record_hello,
                payload.node_id,
                payload.url,
                payload.height,
                payload.total_work,
            )
            await run_in_threadpool(runtime.peer_manager.persist_peers)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        peers = await run_in_threadpool(lambda: runtime.peer_manager.peers)
        return PeerHelloResponse(
            node_id=runtime.node_id,
            url=runtime.url,
            height=runtime.blockchain.height,
            total_work=runtime.blockchain.total_work,
            peers=[PeerInfo.model_validate(peer.to_dict()) for peer in peers],
        )

    @application.post("/p2p/transactions", response_model=PeerMessageResponse)
    async def p2p_transaction(
        payload: PeerMessage,
        request: Request,
    ) -> PeerMessageResponse:
        runtime = runtime_for(request)
        if payload.transaction is None:
            raise HTTPException(status_code=400, detail="transaction payload is required")
        try:
            if not runtime.peer_manager.has_peer(payload.sender_url):
                raise HTTPException(status_code=403, detail="sender is not a known peer")
            accepted = await run_in_threadpool(
                runtime.receive_transaction,
                payload.transaction,
                payload.sender_url,
            )
        except HTTPException:
            raise
        except (ValueError, MempoolError) as error:
            await run_in_threadpool(runtime.peer_manager.strike, payload.sender_url)
            raise HTTPException(status_code=400, detail=str(error)) from error
        return PeerMessageResponse(accepted=accepted)

    @application.post("/p2p/blocks", response_model=PeerMessageResponse)
    async def p2p_block(
        payload: PeerMessage,
        request: Request,
    ) -> PeerMessageResponse:
        runtime = runtime_for(request)
        if payload.block is None:
            raise HTTPException(status_code=400, detail="block payload is required")
        try:
            if not runtime.peer_manager.has_peer(payload.sender_url):
                raise HTTPException(status_code=403, detail="sender is not a known peer")
            accepted = await run_in_threadpool(
                runtime.receive_block,
                payload.block,
                payload.sender_url,
            )
        except HTTPException:
            raise
        except (ValueError, TypeError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return PeerMessageResponse(accepted=accepted)

    @application.get("/balances/{address}", response_model=BalanceResponse)
    async def get_balance(address: str, request: Request) -> BalanceResponse:
        if not is_valid_address(address):
            raise HTTPException(
                status_code=400,
                detail="address must be 40 lowercase hexadecimal characters",
            )
        runtime = runtime_for(request)
        with runtime.state_lock:
            return BalanceResponse(
                address=address,
                balance=runtime.blockchain.balance_of(address),
                nonce=runtime.blockchain.nonce_of(address),
            )

    @application.get("/wallet", response_model=WalletResponse)
    async def get_wallet(request: Request) -> WalletResponse:
        runtime = runtime_for(request)
        with runtime.state_lock:
            return WalletResponse(
                address=runtime.wallet.address,
                balance=runtime.blockchain.balance_of(runtime.wallet.address),
            )

    @application.post(
        "/wallet/send",
        response_model=TransactionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def wallet_send(
        payload: WalletSendRequest,
        request: Request,
    ) -> TransactionResponse:
        runtime = runtime_for(request)
        if not is_valid_address(payload.recipient):
            raise HTTPException(
                status_code=400,
                detail="recipient must be 40 lowercase hexadecimal characters",
            )
        with runtime.state_lock:
            sender = runtime.wallet
            nonce = runtime.blockchain.nonce_of(sender.address)
            for pending in runtime.mempool.transactions:
                if pending.sender_address == sender.address:
                    nonce += 1
            try:
                transaction = sender.create_transaction(
                    recipient=payload.recipient,
                    amount=payload.amount,
                    nonce=nonce,
                )
                runtime.mempool.add(transaction)
            except (MempoolError, ValueError) as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
        await run_in_threadpool(runtime.peer_manager.gossip_transaction, transaction.to_dict())
        return transaction_response(transaction, "pending")

    @application.get(
        "/validate",
        response_model=ValidationResponse,
        response_model_exclude_none=True,
    )
    async def validate_chain(request: Request) -> ValidationResponse:
        runtime = runtime_for(request)
        error = await run_in_threadpool(runtime.validate_chain)
        if error is None:
            return ValidationResponse(valid=True)
        return ValidationResponse(
            valid=False,
            block_index=error.block_index,
            rule=error.rule,
            message=error.message,
        )

    application.mount(
        "/ui",
        StaticFiles(directory=Path(__file__).resolve().parent.parent / "ui", html=True),
        name="ui",
    )
    return application


app = create_app()
