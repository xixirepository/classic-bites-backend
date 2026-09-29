"""Authenticate and bound uploads before FastAPI reads multipart request bodies."""

import hmac
from threading import BoundedSemaphore

import anyio
from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import JSONResponse

MULTIPART_OVERHEAD_BYTES = 1024 * 1024


def valid_api_key(expected: str, supplied: str | None) -> bool:
    return bool(expected and supplied) and hmac.compare_digest(
        expected.encode("utf-8"), supplied.encode("utf-8")
    )


class UploadTooLarge(MultiPartException):
    pass


class StrictMultipartParser(MultiPartParser):
    """A single spooled file, bounded fields, and cleanup even on disconnect."""

    def __init__(self, *args, file_limit: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.file_limit = file_limit
        self.file_bytes = 0
        self.completed = False

    def on_part_begin(self):
        super().on_part_begin()
        self.file_bytes = 0

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > self.file_limit:
                raise UploadTooLarge("File exceeds MAX_UPLOAD_BYTES.")
        super().on_part_data(data, start, end)

    def on_end(self):
        super().on_end()
        self.completed = True

    async def parse(self):
        try:
            form = await super().parse()
            if not self.completed:
                raise MultiPartException("Incomplete multipart body.")
            return form
        except BaseException:
            # Include files not yet added to FormData when a client disconnects.
            for temporary_file in self._files_to_close_on_error:
                temporary_file.close()
            raise


class UploadRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            if request.method not in {"POST", "PUT"}:
                return await original(request)
            content_type = request.headers.get("content-type", "").split(";", 1)[0].lower().strip()
            if content_type != "multipart/form-data":
                raise HTTPException(415, "Use multipart/form-data with one file field.")
            parser = StrictMultipartParser(
                request.headers,
                request.stream(),
                max_files=1,
                max_fields=1 if request.method == "POST" else 0,
                max_part_size=4096,
                file_limit=request.app.state.settings.max_upload_bytes,
            )
            try:
                form = await parser.parse()
            except UploadTooLarge as exc:
                raise HTTPException(413, "Upload exceeds the configured size limit.") from exc
            except MultiPartException as exc:
                raise HTTPException(400, "Invalid or incomplete multipart upload.") from exc
            # FastAPI uses Request.form(); reuse the parsed form rather than read
            # the body twice. Starlette is pinned because this is its form cache.
            request._form = form
            try:
                allowed = {"file", "category"} if request.method == "POST" else {"file"}
                if any(name not in allowed for name in form):
                    raise HTTPException(400, "Unexpected multipart field.")
                return await original(request)
            finally:
                with anyio.CancelScope(shield=True):
                    await form.close()

        return handler


class MediaGuardMiddleware:
    """Authentication, an actual byte cap, and one non-queued upload per worker."""

    def __init__(self, app):
        self.app = app
        self.upload_slot = BoundedSemaphore(1)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/files" or path.startswith("/files/")):
            return await self.app(scope, receive, send)
        settings = getattr(scope["app"].state, "settings", None)
        if settings is None or not settings.api_key:
            return await JSONResponse({"detail": "Media service is unavailable."}, 503)(scope, receive, send)
        headers = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), []).append(value)
        supplied_keys = headers.get(b"x-api-key", [])
        supplied = supplied_keys[0].decode("latin-1") if len(supplied_keys) == 1 else None
        if not valid_api_key(settings.api_key, supplied):
            return await JSONResponse(
                {"detail": "A valid X-API-Key is required."}, 401,
                headers={"WWW-Authenticate": "APIKey"},
            )(scope, receive, send)
        is_upload = path.rstrip("/") == "/files" and scope["method"] in {"POST", "PUT"}
        if not is_upload:
            return await self.app(scope, receive, send)
        body_limit = settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES
        lengths = headers.get(b"content-length", [])
        if lengths:
            try:
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError
                declared_length = int(lengths[0])
            except ValueError:
                return await JSONResponse({"detail": "Invalid Content-Length."}, 400)(scope, receive, send)
            if declared_length > body_limit:
                return await JSONResponse({"detail": "Upload exceeds the configured size limit."}, 413)(scope, receive, send)
        if not self.upload_slot.acquire(blocking=False):
            return await JSONResponse(
                {"detail": "Another upload is in progress. Try again shortly."}, 429,
                headers={"Retry-After": "1"},
            )(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > body_limit:
                    raise UploadTooLarge("Multipart request exceeds the size limit.")
            return message

        try:
            return await self.app(scope, limited_receive, send)
        finally:
            self.upload_slot.release()
