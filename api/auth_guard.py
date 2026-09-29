"""Bound authentication request bodies and prevent caching sensitive responses."""
import asyncio
from starlette.responses import JSONResponse


class AuthGuardMiddleware:
    def __init__(self, app, max_bytes=16384):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/auth/"):
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [(k, v) for k, v in message.get("headers", [])
                                      if k.lower() not in (b"cache-control", b"pragma")]
                message["headers"] += [(b"cache-control", b"no-store"), (b"pragma", b"no-cache")]
            await send(message)

        async def reject(status, code, message):
            await JSONResponse({"detail": {"code": code, "message": message}}, status_code=status)(scope, receive, private_send)

        body = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > self.max_bytes:
                        return await reject(413, "request_too_large", "요청 크기가 너무 큽니다.")
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await reject(408, "request_timeout", "요청 시간이 초과되었습니다.")
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, private_send)
