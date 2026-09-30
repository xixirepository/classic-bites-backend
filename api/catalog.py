"""Guest catalogue reads and account-authorized catalogue administration."""

from dataclasses import dataclass
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auth import current_user, failure
from catalog_store import CatalogConflict, CatalogNotFound


@dataclass(frozen=True)
class CatalogSettings:
    enabled: bool = False
    admin_user_ids: tuple[str, ...] = ()

    @classmethod
    def from_env(cls):
        enabled = os.environ.get("CATALOG_ENABLED", "false").lower()
        if enabled not in ("true", "false"):
            raise ValueError("CATALOG_ENABLED must be true or false")
        return cls(enabled == "true", tuple(str(UUID(value.strip()))
                   for value in os.environ.get("CATALOG_ADMIN_USER_IDS", "").split(",") if value.strip()))

    def validate(self):
        for value in self.admin_user_ids:
            if str(UUID(value)) != value:
                raise ValueError("Catalog administrator IDs must be canonical UUIDs")


class EntityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    sort_order: int = Field(default=0, ge=-2147483648, le=2147483647, strict=True)
    is_published: bool = Field(default=False, strict=True)

    @field_validator("title", mode="before")
    @classmethod
    def title_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError("Title must not contain control characters")
        return value


class BookInput(EntityInput):
    description: str = Field(default="", max_length=10000)

    @field_validator("description", mode="before")
    @classmethod
    def description_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class WorkInput(BookInput):
    book_id: UUID
    title_hanzi: str = Field(default="", max_length=200)
    title_pinyin: str = Field(default="", max_length=200)
    source_edition: str = Field(default="", max_length=1000)
    source_url: str = Field(default="", max_length=2048)
    pinyin_source: str = Field(default="", max_length=2000)
    review_status: Literal["draft", "reviewed"] = "draft"

    @field_validator("title_hanzi", "title_pinyin", mode="before")
    @classmethod
    def reading_title(cls, value):
        return cls.title_text(value)

    @field_validator("source_edition", "source_url", "pinyin_source", mode="before")
    @classmethod
    def source_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class ChapterInput(EntityInput):
    book_id: UUID
    work_id: UUID | None = None
    title_hanzi: str = Field(default="", max_length=200)
    title_pinyin: str = Field(default="", max_length=200)

    @field_validator("title_hanzi", "title_pinyin", mode="before")
    @classmethod
    def reading_title(cls, value):
        return cls.title_text(value)


class BiteInput(EntityInput):
    chapter_id: UUID
    original: str = Field(default="", max_length=100000)
    pinyin: str = Field(default="", max_length=100000)
    translation: str = Field(default="", max_length=100000)
    commentary: str = Field(default="", max_length=100000)

    @field_validator("original", "pinyin", "translation", "commentary", mode="before")
    @classmethod
    def body_text(cls, value):
        return value.strip() if isinstance(value, str) else value


def catalog_store(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not request.app.state.catalog_settings.enabled or request.app.state.catalog_store is None:
        raise failure(503, "catalog_disabled", "서재 서비스가 아직 활성화되지 않았습니다.")
    return request.app.state.catalog_store


def require_admin(request: Request, user=Depends(current_user), store=Depends(catalog_store)):
    if user.id not in request.app.state.catalog_settings.admin_user_ids:
        raise failure(403, "admin_required", "콘텐츠 관리자 권한이 필요합니다.")
    return user


def serialize_entity(entity, kind, public=True, summary=False):
    result = dict(entity)
    if kind in ("books", "works"):
        cover = result.get("cover_key")
        prefix = "/catalog" if public else "/admin/api"
        result["cover_url"] = prefix + "/" + kind + "/" + result["id"] + "/cover" if cover else None
    if public:
        result.pop("is_published", None)
        result.pop("cover_key", None)
        for field in ("source_edition", "source_url", "pinyin_source", "review_status"):
            result.pop(field, None)
    if summary:
        for field in ("original", "pinyin", "translation", "commentary"):
            result.pop(field, None)
    return result


def detail(store, kind, item_id, public):
    try:
        result = store.get_detail(kind, str(item_id), public=public)
    except CatalogNotFound:
        raise failure(404, "content_not_found", "콘텐츠를 찾을 수 없습니다.") from None
    if kind == "bites" and public and not result["bite"]["is_ready"]:
        raise failure(409, "content_preparing", "이 한입의 본문을 준비하고 있습니다.")
    kinds = {"book": "books", "work": "works", "chapter": "chapters", "bite": "bites"}
    for key, entity in result.items():
        if key in kinds:
            result[key] = serialize_entity(entity, kinds[key], public)
        else:
            result[key] = [serialize_entity(item, key, public, summary=key == "bites") for item in entity]
    return result


def save(store, kind, data, item_id=None):
    try:
        result = store.save(kind, data.model_dump(mode="json"), str(item_id) if item_id else None)
    except CatalogNotFound:
        raise failure(404, "content_not_found", "콘텐츠를 찾을 수 없습니다.") from None
    except CatalogConflict as exc:
        message = "상위 콘텐츠는 생성 후 변경할 수 없습니다." if exc.code == "parent_immutable" else "상위 콘텐츠 연결을 확인해 주세요."
        raise failure(409, exc.code, message) from None
    singular = {"books": "book", "works": "work", "chapters": "chapter", "bites": "bite"}[kind]
    return detail(store, kind, result[singular]["id"], public=False)


router = APIRouter(tags=["Catalogue"])
admin = APIRouter(prefix="/admin/api", dependencies=[Depends(require_admin)], tags=["Catalogue administration"])


@router.get("/catalog/books")
def books(store=Depends(catalog_store)):
    return {"items": [serialize_entity(item, "books") for item in store.list_books(public=True)]}


@router.get("/catalog/books/{item_id}")
def book(item_id: UUID, store=Depends(catalog_store)):
    return detail(store, "books", item_id, True)


@router.get("/catalog/works/{item_id}")
def work(item_id: UUID, store=Depends(catalog_store)):
    return detail(store, "works", item_id, True)


@router.get("/catalog/works/{item_id}/reader")
def work_reader(item_id: UUID, store=Depends(catalog_store)):
    try:
        result = store.get_work_reader(str(item_id))
    except CatalogNotFound:
        raise failure(404, "content_not_found", "콘텐츠를 찾을 수 없습니다.") from None
    return {
        "work": serialize_entity(result["work"], "works"),
        "chapters": [
            {"chapter": serialize_entity(item["chapter"], "chapters"),
             "bites": [serialize_entity(bite, "bites") for bite in item["bites"]]}
            for item in result["chapters"]
        ],
    }


@router.get("/catalog/chapters/{item_id}")
def chapter(item_id: UUID, store=Depends(catalog_store)):
    return detail(store, "chapters", item_id, True)


@router.get("/catalog/bites/{item_id}")
def bite(item_id: UUID, store=Depends(catalog_store)):
    return detail(store, "bites", item_id, True)


@admin.get("/books")
def admin_books(store=Depends(catalog_store)):
    return {"items": [serialize_entity(item, "books", False) for item in store.list_books(public=False)]}


@admin.get("/books/{item_id}")
def admin_book(item_id: UUID, store=Depends(catalog_store)):
    return detail(store, "books", item_id, False)


@admin.get("/works/{item_id}")
def admin_work(item_id: UUID, store=Depends(catalog_store)):
    return detail(store, "works", item_id, False)


@admin.get("/chapters/{item_id}")
def admin_chapter(item_id: UUID, store=Depends(catalog_store)):
    return detail(store, "chapters", item_id, False)


@admin.get("/bites/{item_id}")
def admin_bite(item_id: UUID, store=Depends(catalog_store)):
    return detail(store, "bites", item_id, False)


@admin.post("/books", status_code=201)
def create_book(data: BookInput, store=Depends(catalog_store)):
    return save(store, "books", data)


@admin.put("/books/{item_id}")
def update_book(item_id: UUID, data: BookInput, store=Depends(catalog_store)):
    return save(store, "books", data, item_id)


@admin.post("/works", status_code=201)
def create_work(data: WorkInput, store=Depends(catalog_store)):
    return save(store, "works", data)


@admin.put("/works/{item_id}")
def update_work(item_id: UUID, data: WorkInput, store=Depends(catalog_store)):
    return save(store, "works", data, item_id)


@admin.post("/chapters", status_code=201)
def create_chapter(data: ChapterInput, store=Depends(catalog_store)):
    return save(store, "chapters", data)


@admin.put("/chapters/{item_id}")
def update_chapter(item_id: UUID, data: ChapterInput, store=Depends(catalog_store)):
    return save(store, "chapters", data, item_id)


@admin.post("/bites", status_code=201)
def create_bite(data: BiteInput, store=Depends(catalog_store)):
    return save(store, "bites", data)


@admin.put("/bites/{item_id}")
def update_bite(item_id: UUID, data: BiteInput, store=Depends(catalog_store)):
    return save(store, "bites", data, item_id)


router.include_router(admin)
