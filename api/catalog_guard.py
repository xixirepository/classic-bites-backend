"""Bound content writes and prevent stale visibility, including error responses."""
import asyncio
import os
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


def admin_origins_from_env():
    origins = []
    for value in os.environ.get("CATALOG_ADMIN_ORIGINS", "").split(","):
        value = value.strip().rstrip("/")
        if not value:
            continue
        if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Catalogue administrator origins must not contain whitespace or control characters.")
        parsed = urlsplit(value)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment or (parsed.scheme != "https" and not (
                    parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")))):
            raise ValueError("CATALOG_ADMIN_ORIGINS must contain HTTPS origins or local loopback HTTP origins.")
        _ = parsed.port
        origins.append(value)
    return origins


class CatalogGuardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path.startswith("/catalog/") or path.startswith("/admin/api/")):
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [(k, v) for k, v in message.get("headers", [])
                                      if k.lower() not in (b"cache-control", b"pragma")]
                message["headers"] += [(b"cache-control", b"no-store"), (b"pragma", b"no-cache")]
            await send(message)

        # Cover uploads are streamed, authenticated and separately bounded.
        if scope["method"] not in ("POST", "PUT") or path.endswith("/cover"):
            return await self.app(scope, receive, private_send)
        body = bytearray()
        try:
            async with asyncio.timeout(15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > 2 * 1024 * 1024:
                        return await JSONResponse({"detail": {"code": "request_too_large", "message": "내용이 너무 큽니다."}}, 413)(scope, receive, private_send)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await JSONResponse({"detail": {"code": "request_timeout", "message": "요청 시간이 초과되었습니다."}}, 408)(scope, receive, private_send)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, private_send)
