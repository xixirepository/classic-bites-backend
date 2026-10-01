"""Persistent catalogue with ancestor visibility enforced on every public query."""

from contextlib import contextmanager
import json
import uuid

import pymysql
from auth_store import mysql_connect_from_env
from catalog_learning import decode_learning, validate_learning


class CatalogNotFound(Exception):
    pass


class CatalogConflict(Exception):
    def __init__(self, code="invalid_parent"):
        self.code = code


COLUMNS = {
    "books": ("id", "title", "description", "cover_key", "sort_order", "is_published"),
    "works": ("id", "book_id", "title", "title_hanzi", "title_pinyin", "description", "cover_key", "sort_order", "is_published",
              "source_edition", "source_url", "pinyin_source", "review_status"),
    "chapters": ("id", "book_id", "work_id", "title", "title_hanzi", "title_pinyin", "sort_order", "is_published"),
    "bites": ("id", "chapter_id", "title", "original", "pinyin", "translation", "commentary", "learning", "sort_order", "is_published"),
}
READING_DEFAULTS = {
    "works": {"title_hanzi": "", "title_pinyin": "", "source_edition": "", "source_url": "",
              "pinyin_source": "", "review_status": "draft"},
    "chapters": {"title_hanzi": "", "title_pinyin": ""},
    "bites": {"pinyin": "", "learning": None},
}
PARENTS = {"books": (), "works": ("book_id",), "chapters": ("book_id", "work_id"), "bites": ("chapter_id",)}
# Every alias is static code, never request input. LEFT JOIN keeps direct chapters.
FROM = {
    "books": "catalog_books t",
    "works": "catalog_works t JOIN catalog_books b ON b.id=t.book_id",
    "chapters": "catalog_chapters t JOIN catalog_books b ON b.id=t.book_id LEFT JOIN catalog_works w ON w.id=t.work_id AND w.book_id=t.book_id",
    "bites": "catalog_bites t JOIN catalog_chapters c ON c.id=t.chapter_id JOIN catalog_books b ON b.id=c.book_id LEFT JOIN catalog_works w ON w.id=c.work_id AND w.book_id=c.book_id",
}
VISIBILITY = {
    "books": "t.is_published=1",
    "works": "t.is_published=1 AND b.is_published=1",
    "chapters": "t.is_published=1 AND b.is_published=1 AND (t.work_id IS NULL OR w.is_published=1)",
    "bites": "t.is_published=1 AND c.is_published=1 AND b.is_published=1 AND (c.work_id IS NULL OR w.is_published=1)",
}
HAS_TEXT = "(CHAR_LENGTH(TRIM(x.original))>0 OR CHAR_LENGTH(TRIM(x.translation))>0 OR CHAR_LENGTH(TRIM(x.commentary))>0)"
READABLE_DESCENDANT = (
    "SELECT 1 FROM catalog_bites x JOIN catalog_chapters xc ON xc.id=x.chapter_id "
    "JOIN catalog_books xb ON xb.id=xc.book_id LEFT JOIN catalog_works xw ON xw.id=xc.work_id AND xw.book_id=xc.book_id "
    "WHERE x.is_published=1 AND xc.is_published=1 AND xb.is_published=1 "
    "AND (xc.work_id IS NULL OR xw.is_published=1) AND " + HAS_TEXT
)
READINESS = {
    "books": "EXISTS (" + READABLE_DESCENDANT + " AND xc.book_id=t.id)",
    "works": "EXISTS (" + READABLE_DESCENDANT + " AND xc.work_id=t.id)",
    "chapters": "EXISTS (" + READABLE_DESCENDANT + " AND xc.id=t.id)",
    "bites": "(" + VISIBILITY["bites"] + " AND (CHAR_LENGTH(TRIM(t.original))>0 OR CHAR_LENGTH(TRIM(t.translation))>0 OR CHAR_LENGTH(TRIM(t.commentary))>0))",
}


class CatalogStore:
    def __init__(self, connect):
        self._connect = connect

    @classmethod
    def from_env(cls):
        return cls(mysql_connect_from_env)

    @contextmanager
    def _transaction(self):
        connection = self._connect()
        try:
            # All related reads in a response share one consistent snapshot.
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.begin()
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _rows(self, cursor, kind, where="1=1", values=(), public=False):
        columns = ", ".join("t." + column for column in COLUMNS[kind])
        predicate = "(" + where + ")" + (" AND " + VISIBILITY[kind] if public else "")
        cursor.execute(
            "SELECT " + columns + ", " + READINESS[kind] + " AS is_ready FROM " + FROM[kind]
            + " WHERE " + predicate + " ORDER BY t.sort_order, t.id", values,
        )
        result = []
        for row in cursor.fetchall():
            row["is_ready"] = bool(row["is_ready"])
            row["is_published"] = bool(row["is_published"])
            if kind == "bites":
                row["learning"] = decode_learning(row.get("learning"))
            result.append(row)
        return result

    def list_books(self, public=True):
        with self._transaction() as connection, connection.cursor() as cursor:
            return self._rows(cursor, "books", public=public)

    def get_detail(self, kind, item_id, public=True):
        with self._transaction() as connection, connection.cursor() as cursor:
            rows = self._rows(cursor, kind, "t.id=%s", (item_id,), public)
            if not rows:
                raise CatalogNotFound()
            key = {"books": "book", "works": "work", "chapters": "chapter", "bites": "bite"}[kind]
            result = {key: rows[0]}
            if kind == "books":
                result["works"] = self._rows(cursor, "works", "t.book_id=%s", (item_id,), public)
                result["chapters"] = self._rows(cursor, "chapters", "t.book_id=%s AND t.work_id IS NULL", (item_id,), public)
            elif kind == "works":
                result["chapters"] = self._rows(cursor, "chapters", "t.work_id=%s", (item_id,), public)
            elif kind == "chapters":
                result["bites"] = self._rows(cursor, "bites", "t.chapter_id=%s", (item_id,), public)
            return result

    def get_work_reader(self, item_id):
        """Read the entire published work within one snapshot, ordered per level."""
        with self._transaction() as connection, connection.cursor() as cursor:
            works = self._rows(cursor, "works", "t.id=%s", (item_id,), public=True)
            if not works:
                raise CatalogNotFound()
            chapters = self._rows(cursor, "chapters", "t.work_id=%s", (item_id,), public=True)
            bites = self._rows(cursor, "bites", "c.work_id=%s", (item_id,), public=True)
            by_chapter = {chapter["id"]: [] for chapter in chapters}
            for bite in bites:
                # Reader readiness intentionally differs from the legacy general API:
                # translation/commentary or pinyin alone cannot supply a Hanzi line.
                bite["is_ready"] = bool(bite["original"].strip())
                by_chapter[bite["chapter_id"]].append(bite)
            result = []
            for chapter in chapters:
                rows = by_chapter[chapter["id"]]
                chapter["is_ready"] = any(bite["is_ready"] for bite in rows)
                result.append({"chapter": chapter, "bites": rows})
            work = works[0]
            work["is_ready"] = any(item["chapter"]["is_ready"] for item in result)
            return {"work": work, "chapters": result}

    def save(self, kind, values, item_id=None):
        data = {**READING_DEFAULTS.get(kind, {}), **values}
        data.pop("cover_key", None)  # Covers are attached only after validated storage writes.
        target_id = item_id or str(uuid.uuid4())
        try:
            with self._transaction() as connection, connection.cursor() as cursor:
                if item_id is not None:
                    cursor.execute("SELECT * FROM catalog_" + kind + " WHERE id=%s FOR UPDATE", (item_id,))
                    current = cursor.fetchone()
                    if current is None:
                        raise CatalogNotFound()
                    if any(key in values and current[key] != values[key] for key in PARENTS[kind]):
                        raise CatalogConflict("parent_immutable")
                    data = {**current, **values}
                    self._prepare_learning(kind, data)
                    columns = [key for key in COLUMNS[kind] if key not in ("id", "cover_key")]
                    cursor.execute("UPDATE catalog_" + kind + " SET " + ", ".join(key + "=%s" for key in columns)
                                   + " WHERE id=%s", tuple(data[key] for key in columns) + (item_id,))
                else:
                    self._prepare_learning(kind, data)
                    columns = [key for key in COLUMNS[kind] if key != "cover_key"]
                    data["id"] = target_id
                    cursor.execute("INSERT INTO catalog_" + kind + " (" + ", ".join(columns) + ") VALUES ("
                                   + ", ".join(["%s"] * len(columns)) + ")", tuple(data[key] for key in columns))
        except pymysql.err.IntegrityError as exc:
            if exc.args[0] in (1451, 1452):
                raise CatalogConflict() from None
            raise
        return self.get_detail(kind, target_id, public=False)

    @staticmethod
    def _prepare_learning(kind, data):
        if kind != "bites":
            return
        try:
            learning = validate_learning(data.get("learning"), data["original"], data["pinyin"])
        except (ValueError, TypeError):
            raise CatalogConflict("learning_source_mismatch") from None
        data["learning"] = json.dumps(learning, ensure_ascii=False) if learning is not None else None

    def set_cover(self, kind, item_id, key):
        if kind not in ("books", "works"):
            raise ValueError("Only books and works have covers")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id FROM catalog_" + kind + " WHERE id=%s FOR UPDATE", (item_id,))
            if cursor.fetchone() is None:
                raise CatalogNotFound()
            cursor.execute("UPDATE catalog_" + kind + " SET cover_key=%s WHERE id=%s", (key, item_id))

    def check_schema(self):
        with self._transaction() as connection, connection.cursor() as cursor:
            for kind, columns in COLUMNS.items():
                cursor.execute("SELECT " + ", ".join(columns) + " FROM catalog_" + kind + " WHERE 1=0")
