"""Administrator cover uploads and visibility-checked, same-origin image reads."""
import asyncio
from io import BytesIO
from threading import BoundedSemaphore
from typing import Literal
from uuid import UUID, uuid4

import anyio
from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from auth import failure
from catalog import catalog_store, require_admin
from catalog_store import CatalogNotFound
from media import ObjectStreamingResponse, storage_errors
from upload_guard import StrictMultipartParser, UploadTooLarge


router = APIRouter(tags=["Catalogue covers"])
COVER_BYTES = 5 * 1024 * 1024
upload_slot = BoundedSemaphore(1)
CoverKind = Literal["books", "works"]


def entity_cover(store, kind, item_id, public):
    try:
        return store.get_detail(kind, str(item_id), public=public)["book" if kind == "books" else "work"]["cover_key"]
    except CatalogNotFound:
        raise failure(404, "content_not_found", "콘텐츠를 찾을 수 없습니다.") from None


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise failure(415, "invalid_cover", "PNG, JPEG 또는 WebP 이미지 파일을 선택해 주세요.")


@router.post("/admin/api/{kind}/{item_id}/cover", dependencies=[Depends(require_admin)])
async def upload_cover(kind: CoverKind, item_id: UUID, request: Request, store=Depends(catalog_store)):
    # No File dependency: authentication runs before multipart parsing begins.
    await run_in_threadpool(entity_cover, store, kind, item_id, False)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise failure(415, "invalid_upload", "이미지 파일을 선택해 주세요.")
    if not upload_slot.acquire(blocking=False):
        raise failure(429, "upload_busy", "다른 표지를 저장 중입니다. 잠시 후 다시 시도해 주세요.", {"Retry-After": "1"})
    form = None
    try:
        received = 0

        async def bounded_stream():
            nonlocal received
            async for chunk in request.stream():
                received += len(chunk)
                if received > COVER_BYTES + 64 * 1024:
                    raise UploadTooLarge("Cover request limit")
                yield chunk

        parser = StrictMultipartParser(request.headers, bounded_stream(), max_files=1, max_fields=0,
                                      max_part_size=4096, file_limit=COVER_BYTES)
        async with asyncio.timeout(30):
            form = await parser.parse()
            if len(form.multi_items()) != 1 or not isinstance(form.get("file"), UploadFile):
                raise failure(400, "invalid_upload", "표지 파일 하나를 선택해 주세요.")
            data = await form["file"].read(COVER_BYTES + 1)
        if len(data) > COVER_BYTES:
            raise UploadTooLarge("Cover file limit")
        mime, extension = image_type(data)
        key = f"images/catalog/{uuid4().hex}.{extension}"

        def persist():
            with storage_errors():
                request.app.state.storage.put_object(request.app.state.settings.bucket, key, BytesIO(data),
                                                     len(data), content_type=mime, num_parallel_uploads=1)
            # Retain prior objects and uncertain failed writes; never delete an
            # object which a concurrently committed database update may reference.
            store.set_cover(kind, str(item_id), key)

        await run_in_threadpool(persist)
        return {"cover_url": f"/admin/api/{kind}/{item_id}/cover"}
    except UploadTooLarge:
        raise failure(413, "cover_too_large", "표지는 5 MiB 이하로 선택해 주세요.") from None
    except MultiPartException:
        raise failure(400, "invalid_upload", "이미지 파일 전송을 완료하지 못했습니다.") from None
    except TimeoutError:
        raise failure(408, "upload_timeout", "표지 전송 시간이 초과되었습니다.") from None
    finally:
        if form is not None:
            with anyio.CancelScope(shield=True):
                await form.close()
        upload_slot.release()


def cover_response(request, store, kind, item_id, public):
    key = entity_cover(store, kind, item_id, public)
    if not key:
        raise failure(404, "cover_not_found", "등록된 표지가 없습니다.")
    with storage_errors():
        obj = request.app.state.storage.get_object(request.app.state.settings.bucket, key)
    try:
        mime = obj.headers.get("Content-Type", "").split(";", 1)[0]
        if mime not in ("image/png", "image/jpeg", "image/webp"):
            raise failure(415, "invalid_cover", "표지를 표시할 수 없습니다.")
        return ObjectStreamingResponse(obj, media_type=mime, headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        })
    except BaseException:
        obj.close()
        obj.release_conn()
        raise


@router.get("/catalog/{kind}/{item_id}/cover")
def public_cover(kind: CoverKind, item_id: UUID, request: Request, store=Depends(catalog_store)):
    return cover_response(request, store, kind, item_id, True)


@router.get("/admin/api/{kind}/{item_id}/cover", dependencies=[Depends(require_admin)])
def admin_cover(kind: CoverKind, item_id: UUID, request: Request, store=Depends(catalog_store)):
    return cover_response(request, store, kind, item_id, False)
