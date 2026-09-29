"""Private, API-key-protected CRUD for the fixed Classic Bites media bucket."""

import logging
import os
import re
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Annotated
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

import anyio
import urllib3
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, Security, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from minio import Minio
from minio.error import MinioException, S3Error
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from upload_guard import UploadRoute, valid_api_key

logger = logging.getLogger(__name__)


class Category(str, Enum):
    images = "images"
    videos = "videos"
    audio = "audio"
    documents = "documents"
    subtitles = "subtitles"


@dataclass(frozen=True)
class Settings:
    api_key: str
    access_key: str
    secret_key: str
    bucket: str = "classic-bites-media"
    endpoint: str = "http://minio:9000"
    max_upload_bytes: int = 100 * 1024 * 1024

    @classmethod
    def from_env(cls):
        settings = cls(
            api_key=os.environ.get("MEDIA_API_KEY", ""),
            access_key=os.environ.get("MINIO_ACCESS_KEY", ""),
            secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
            bucket=os.environ.get("MINIO_BUCKET", "classic-bites-media"),
            endpoint=os.environ.get("MINIO_ENDPOINT", "http://minio:9000"),
            max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))),
        )
        settings.validate()
        return settings

    def validate(self):
        if not self.api_key or not self.access_key or not self.secret_key:
            raise RuntimeError("MEDIA_API_KEY, MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be configured.")
        if not self.api_key.isascii() or any(character.isspace() for character in self.api_key):
            raise RuntimeError("MEDIA_API_KEY must contain non-whitespace ASCII characters.")
        if self.max_upload_bytes < 1:
            raise RuntimeError("MAX_UPLOAD_BYTES must be a positive integer.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket):
            raise RuntimeError("MINIO_BUCKET must be a valid S3 bucket name.")
        endpoint = urlsplit(self.endpoint)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.path not in {"", "/"}
                or endpoint.query or endpoint.fragment):
            raise RuntimeError("MINIO_ENDPOINT must be an http(s) origin without credentials or path.")


def create_storage(settings: Settings):
    endpoint = urlsplit(settings.endpoint)
    pool = urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=3, read=30),
        retries=urllib3.Retry(total=2, connect=2, read=0, backoff_factor=0.2),
        maxsize=16,
    )
    client = Minio(
        endpoint.netloc,
        access_key=settings.access_key,
        secret_key=settings.secret_key,
        secure=endpoint.scheme == "https",
        http_client=pool,
    )
    return client, pool


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(request: Request, supplied: Annotated[str | None, Security(api_key_header)]):
    if not valid_api_key(request.app.state.settings.api_key, supplied):
        raise HTTPException(401, "A valid X-API-Key is required.", headers={"WWW-Authenticate": "APIKey"})


router = APIRouter(
    prefix="/files", tags=["Media files"],
    dependencies=[Depends(require_api_key)], route_class=UploadRoute,
    responses={401: {"description": "Missing or invalid X-API-Key"},
               503: {"description": "Object storage unavailable"}},
)


class FileItem(BaseModel):
    key: str
    size: int
    etag: str | None = None
    last_modified: datetime | None = None


class FileInfo(FileItem):
    bucket: str
    filename: str
    content_type: str


class FilePage(BaseModel):
    bucket: str
    items: list[FileItem]
    next_start_after: str | None = None


def validate_key(key: str) -> str:
    try:
        byte_length = len(key.encode("utf-8"))
    except UnicodeEncodeError:
        byte_length = 1025
    parts = key.split("/")
    if (byte_length > 1024 or len(parts) < 2 or parts[0] not in Category._value2member_map_
            or any(part in {"", ".", ".."} for part in parts) or "\\" in key
            or any(unicodedata.category(char).startswith("C") for char in key)):
        raise HTTPException(422, "Invalid object key. Use a file within one of the five media categories.")
    return key


def safe_filename(filename: str | None) -> str:
    filename = unicodedata.normalize("NFC", (filename or "file").replace("\\", "/").rsplit("/", 1)[-1])
    filename = "".join(char if (char.isalnum() or char in " ._-") else "_" for char in filename)
    filename = filename.strip(" .") or "file"
    stem, dot, extension = filename.rpartition(".")
    suffix = dot + extension if stem and len(extension.encode("utf-8")) <= 20 else ""
    stem = stem if suffix else filename
    budget = 180 - len(suffix.encode("utf-8"))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .") or "file"
    return stem + suffix


def safe_content_type(content_type: str | None) -> str:
    if content_type and len(content_type) <= 200 and re.fullmatch(r"[\x20-\x7e]+", content_type) and "/" in content_type:
        return content_type
    return "application/octet-stream"


@contextmanager
def storage_errors():
    try:
        yield
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchVersion", "NotFound"}:
            raise HTTPException(404, "File not found.") from None
        logger.warning("Object storage request failed (%s).", type(exc).__name__)
        raise HTTPException(503, "Object storage is temporarily unavailable.") from None
    except (MinioException, urllib3.exceptions.HTTPError, OSError) as exc:
        logger.warning("Object storage request failed (%s).", type(exc).__name__)
        raise HTTPException(503, "Object storage is temporarily unavailable.") from None


def metadata_filename(metadata, key: str) -> str:
    normalized = {name.lower(): value for name, value in (metadata or {}).items()}
    encoded = normalized.get("x-amz-meta-original-filename")
    return safe_filename(unquote(encoded) if encoded else key.rsplit("/", 1)[-1])


def file_info(settings: Settings, key: str, stat) -> FileInfo:
    return FileInfo(
        bucket=settings.bucket, key=key,
        filename=metadata_filename(stat.metadata, key), size=stat.size,
        content_type=safe_content_type(stat.content_type), etag=stat.etag,
        last_modified=stat.last_modified,
    )


def save_file(request: Request, key: str, file: UploadFile):
    settings = request.app.state.settings
    storage = request.app.state.storage
    filename = safe_filename(file.filename)
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    if size > settings.max_upload_bytes:
        raise HTTPException(413, "Upload exceeds the configured size limit.")
    with storage_errors():
        storage.put_object(
            settings.bucket, key, file.file, size,
            content_type=safe_content_type(file.content_type),
            metadata={"original-filename": quote(filename, safe="")},
            num_parallel_uploads=1,
        )
        return file_info(settings, key, storage.stat_object(settings.bucket, key))


@router.post("", response_model=FileInfo, status_code=201,
             responses={413: {"description": "File or request body too large"},
                        429: {"description": "Another upload is in progress"}},
             summary="Upload a new file")
def create_file(
    request: Request,
    file: Annotated[UploadFile, File(description="One file, at most MAX_UPLOAD_BYTES (default 100 MiB).")],
    category: Annotated[Category, Form()] = Category.images,
):
    key = f"{category.value}/{uuid4().hex}-{safe_filename(file.filename)}"
    return save_file(request, key, file)


@router.get("", response_model=FilePage, summary="List files with key-based pagination")
def list_files(
    request: Request,
    category: Category | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    start_after: str | None = None,
):
    if start_after is not None:
        validate_key(start_after)
        if category and not start_after.startswith(f"{category.value}/"):
            raise HTTPException(422, "start_after must belong to the selected category.")
    settings = request.app.state.settings
    items = []
    next_start_after = None
    with storage_errors():
        objects = request.app.state.storage.list_objects(
            settings.bucket, prefix=f"{category.value}/" if category else "",
            recursive=True, start_after=start_after,
        )
        try:
            for obj in objects:
                try:
                    validate_key(obj.object_name)
                except HTTPException:
                    continue  # Hide console directory markers and out-of-scope keys.
                if len(items) == limit:
                    next_start_after = items[-1].key
                    break
                items.append(FileItem(key=obj.object_name, size=obj.size, etag=obj.etag, last_modified=obj.last_modified))
        finally:
            close = getattr(objects, "close", None)
            if close:
                close()
    return FilePage(bucket=settings.bucket, items=items, next_start_after=next_start_after)


@router.get("/info", response_model=FileInfo, summary="Read file metadata")
def get_file_info(request: Request, key: str):
    validate_key(key)
    settings = request.app.state.settings
    with storage_errors():
        return file_info(settings, key, request.app.state.storage.stat_object(settings.bucket, key))


class ObjectStreamingResponse(StreamingResponse):
    """Close the S3 connection even if the download is cancelled before iteration."""

    def __init__(self, object_response, **kwargs):
        self.object_response = object_response
        super().__init__(object_response.stream(amt=64 * 1024), **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            def cleanup():
                try:
                    self.object_response.close()
                finally:
                    self.object_response.release_conn()
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(cleanup)


@router.get("/download", response_class=StreamingResponse, summary="Download a complete file",
            responses={200: {"content": {"application/octet-stream": {}}}})
def download_file(request: Request, key: str):
    validate_key(key)
    settings = request.app.state.settings
    with storage_errors():
        object_response = request.app.state.storage.get_object(settings.bucket, key)
    try:
        filename = metadata_filename(object_response.headers, key)
        fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "download"
        headers = {
            "Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        }
        length = object_response.headers.get("Content-Length")
        if length is not None:
            headers["Content-Length"] = str(int(length))
        return ObjectStreamingResponse(
            object_response, media_type=safe_content_type(object_response.headers.get("Content-Type")),
            headers=headers,
        )
    except BaseException:
        object_response.close()
        object_response.release_conn()
        raise


@router.put("", response_model=FileInfo, summary="Replace an existing file at the same key",
            description="The key stays the same. Concurrent external writes use last-write-wins; no version precondition is enforced.",
            responses={413: {"description": "File or request body too large"},
                       429: {"description": "Another upload is in progress"}})
def replace_file(request: Request, key: str, file: Annotated[UploadFile, File()]):
    validate_key(key)
    settings = request.app.state.settings
    with storage_errors():
        request.app.state.storage.stat_object(settings.bucket, key)
    return save_file(request, key, file)


@router.delete("", status_code=204, response_class=Response, summary="Delete an existing file")
def delete_file(request: Request, key: str):
    validate_key(key)
    settings = request.app.state.settings
    with storage_errors():
        request.app.state.storage.stat_object(settings.bucket, key)
        request.app.state.storage.remove_object(settings.bucket, key)
    return Response(status_code=204)
