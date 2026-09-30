"""Explicit, atomic Daehak content import; dry-run unless --apply is supplied.

This is intentionally separate from schema migrations and the titles-only seed.
It only adds a complete edition to the existing, published classics/Daehak tree.
Existing content must match exactly to be considered an already applied import.
"""

import argparse
import json
from pathlib import Path
import re
import sys
import uuid

from auth_store import mysql_connect_from_env
from migrate_catalog import CLASSICS_ID, SEED_NAMESPACE


DAEHAK_ID = str(uuid.uuid5(SEED_NAMESPACE, "classics/대학"))
CHAPTER_COLUMNS = ("id", "book_id", "work_id", "title", "sort_order", "is_published")
BITE_COLUMNS = ("id", "chapter_id", "title", "original", "translation", "commentary", "sort_order", "is_published")
KEY_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")
MAX_CONTENT_BYTES = 2 * 1024 * 1024


class ImportConflict(Exception):
    """The existing catalogue cannot safely receive this complete edition."""


def _text(value, label, *, limit, required=True, title=False):
    if not isinstance(value, str) or len(value) > limit or value != value.strip():
        raise ValueError(label + " must be trimmed text within its length limit")
    if required and not value:
        raise ValueError(label + " must not be empty")
    if title and any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(label + " must not contain control characters")
    return value


def _key(value):
    if not isinstance(value, str) or not KEY_PATTERN.fullmatch(value):
        raise ValueError("Content keys must be stable lowercase ASCII identifiers")
    return value


def prepare_content(document):
    """Validate an edition and derive stable IDs without contacting any server."""
    if not isinstance(document, dict):
        raise ValueError("Content must be a JSON object")
    _text(document.get("edition"), "edition", limit=2000)
    _text(document.get("source_url"), "source_url", limit=2000)
    chapters = document.get("chapters")
    if not isinstance(chapters, list) or len(chapters) != 11:
        raise ValueError("Daehak requires the complete 11-chapter edition")
    chapter_rows, bite_rows, chapter_keys = [], [], set()
    for chapter_order, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            raise ValueError("Each chapter must be an object")
        chapter_key = _key(chapter.get("key"))
        if chapter_key in chapter_keys:
            raise ValueError("Chapter keys must be unique")
        chapter_keys.add(chapter_key)
        chapter_path = "classics/대학/" + chapter_key
        chapter_id = str(uuid.uuid5(SEED_NAMESPACE, chapter_path))
        chapter_rows.append(dict(zip(CHAPTER_COLUMNS, (
            chapter_id, CLASSICS_ID, DAEHAK_ID,
            _text(chapter.get("title"), "chapter title", limit=200, title=True),
            chapter_order, True,
        ))))
        bites = chapter.get("bites")
        if not isinstance(bites, list) or not bites:
            raise ValueError("Every chapter must contain at least one bite")
        bite_keys = set()
        for bite_order, bite in enumerate(bites):
            if not isinstance(bite, dict):
                raise ValueError("Each bite must be an object")
            bite_key = _key(bite.get("key"))
            if bite_key in bite_keys:
                raise ValueError("Bite keys must be unique within each chapter")
            bite_keys.add(bite_key)
            bite_rows.append(dict(zip(BITE_COLUMNS, (
                str(uuid.uuid5(SEED_NAMESPACE, chapter_path + "/" + bite_key)), chapter_id,
                _text(bite.get("title"), "bite title", limit=200, title=True),
                _text(bite.get("original"), "original", limit=100000),
                _text(bite.get("translation"), "translation", limit=100000),
                _text(bite.get("commentary", ""), "commentary", limit=100000, required=False),
                bite_order, True,
            ))))
    return {"chapters": chapter_rows, "bites": bite_rows}


def load_content(path):
    raw = Path(path).read_bytes()
    if len(raw) > MAX_CONTENT_BYTES:
        raise ValueError("Content exceeds the 2 MiB import limit")
    document = json.loads(raw)
    prepare_content(document)
    return document


def _placeholders(values):
    return ",".join(["%s"] * len(values))


def _same_rows(actual, expected):
    return {row["id"]: row for row in actual} == {row["id"]: row for row in expected}


def import_content(document, connect=mysql_connect_from_env, *, apply=False):
    """Return a plan/result; any conflict or write failure rolls back all changes.

    Parent row locks also block concurrent child inserts through their foreign
    keys. Locking reads include all existing Daehak chapters and all intended IDs,
    so a partial import, another edition, or a misplaced ID cannot be overwritten.
    Only base columns are read or written; later optional schema fields survive.
    """
    content = prepare_content(document)
    chapter_ids = [row["id"] for row in content["chapters"]]
    bite_ids = [row["id"] for row in content["bites"]]
    result = {"chapters": len(chapter_ids), "bites": len(bite_ids)}
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.begin()
            cursor.execute("SELECT id,title,is_published FROM catalog_books WHERE id=%s FOR UPDATE", (CLASSICS_ID,))
            book = cursor.fetchone()
            cursor.execute("SELECT id,book_id,title,is_published FROM catalog_works WHERE id=%s FOR UPDATE", (DAEHAK_ID,))
            work = cursor.fetchone()
            if (not book or not work or book["title"] != "사서오경" or work["title"] != "대학"
                    or work["book_id"] != CLASSICS_ID or not book["is_published"] or not work["is_published"]):
                raise ImportConflict("Expected published classics book and Daehak work were not found unchanged")
            cursor.execute(
                "SELECT " + ",".join(CHAPTER_COLUMNS) + " FROM catalog_chapters WHERE work_id=%s OR id IN ("
                + _placeholders(chapter_ids) + ") FOR UPDATE", (DAEHAK_ID, *chapter_ids),
            )
            existing_chapters = cursor.fetchall()
            cursor.execute(
                "SELECT " + ",".join(BITE_COLUMNS) + " FROM catalog_bites WHERE chapter_id IN ("
                + _placeholders(chapter_ids) + ") OR id IN (" + _placeholders(bite_ids) + ") FOR UPDATE",
                (*chapter_ids, *bite_ids),
            )
            existing_bites = cursor.fetchall()
            if existing_chapters or existing_bites:
                if not (_same_rows(existing_chapters, content["chapters"]) and _same_rows(existing_bites, content["bites"])):
                    raise ImportConflict("Existing chapters or bites differ; no content was changed")
                result["action"] = "unchanged"
            else:
                result["action"] = "created" if apply else "would_create"
                if apply:
                    for kind, columns in (("chapters", CHAPTER_COLUMNS), ("bites", BITE_COLUMNS)):
                        for row in content[kind]:
                            cursor.execute(
                                "INSERT INTO catalog_" + kind + " (" + ",".join(columns) + ") VALUES ("
                                + _placeholders(columns) + ")", tuple(row[column] for column in columns),
                            )
        if apply:
            connection.commit()
        else:
            connection.rollback()
        return result
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", required=True, type=Path, help="Reviewed Daehak edition JSON")
    parser.add_argument("--apply", action="store_true", help="Commit the complete import; otherwise only inspect")
    args = parser.parse_args(argv)
    try:
        result = import_content(load_content(args.content), apply=args.apply)
    except (ValueError, ImportConflict) as exc:
        print("Daehak import refused: " + str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("Daehak import failed. No partial import was committed; check the file, database and permissions.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
