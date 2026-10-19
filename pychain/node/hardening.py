"""Request limits for untrusted peer-to-peer HTTP routes."""

import json
import time
from threading import Lock

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_P2P_BODY_BYTES = 1_000_000
P2P_REQUESTS_PER_WINDOW = 60
P2P_RATE_WINDOW_SECONDS = 60


class P2PRequestLimitMiddleware:
    """Bound peer request bodies and apply a per-client fixed-window rate limit."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int = MAX_P2P_BODY_BYTES,
        requests_per_window: int = P2P_REQUESTS_PER_WINDOW,
        window_seconds: int = P2P_RATE_WINDOW_SECONDS,
    ) -> None:
        if max_body_bytes < 1 or requests_per_window < 1 or window_seconds < 1:
            raise ValueError("P2P request limits must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self._lock = Lock()
        self._requests: dict[tuple[str, int], int] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_p2p_route(scope["path"]):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = str(client[0]) if client else "unknown"
        retry_after = self._record_request(client_ip)
        if retry_after is not None:
            await self._respond(
                send,
                429,
                "peer request rate limit exceeded",
                [(b"retry-after", str(retry_after).encode("ascii"))],
            )
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._respond(send, 413, "peer request body is too large")
                    return
            except ValueError:
                await self._respond(send, 400, "invalid content-length header")
                return

        chunks: list[bytes] = []
        body_size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            body_size += len(chunk)
            if body_size > self.max_body_bytes:
                await self._respond(send, 413, "peer request body is too large")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        body_sent = False

        async def replay_receive() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    def _is_p2p_route(path: str) -> bool:
        return path == "/p2p" or path.startswith("/p2p/")

    def _record_request(self, client_ip: str) -> int | None:
        now = time.monotonic()
        bucket = int(now // self.window_seconds)
        key = (client_ip, bucket)
        with self._lock:
            if len(self._requests) > 4096:
                self._requests = {
                    entry: count
                    for entry, count in self._requests.items()
                    if entry[1] >= bucket - 1
                }
            count = self._requests.get(key, 0)
            if count >= self.requests_per_window:
                return max(1, self.window_seconds - int(now % self.window_seconds))
            self._requests[key] = count + 1
        return None

    @staticmethod
    async def _respond(
        send: Send,
        status_code: int,
        detail: str,
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        response_headers.extend(headers or [])
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": response_headers,
            }
        )
        await send({"type": "http.response.body", "body": body})
