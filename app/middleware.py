"""ASGI middleware that caps request body size before the multipart parser buffers it to disk."""

from fastapi import HTTPException, status
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_size: int, detail: str) -> None:
        self.app = app
        self.max_body_size = max_body_size
        self.detail = detail

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length", "")
        if content_length.isdigit() and int(content_length) > self.max_body_size:
            response = JSONResponse({"detail": self.detail}, status_code=status.HTTP_413_CONTENT_TOO_LARGE)
            await response(scope, receive, send)
            return

        # Chunked requests carry no Content-Length, so also count the bytes as they arrive.
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_size:
                    raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=self.detail)
            return message

        await self.app(scope, limited_receive, send)
