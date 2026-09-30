"""Fill only missing Daehak reading fields; inspect by default, --apply to write.

Requires the separately deployed Hanzi/pinyin schema. It never installs schema,
changes publication, replaces nonempty readings, or promotes review status.
"""

import argparse
import json
from pathlib import Path
import sys

import pymysql

from auth_store import mysql_connect_from_env
from import_daehak import CLASSICS_ID, DAEHAK_ID, ImportConflict, load_content, prepare_content


READING_SCHEMA = {
    "works": ("title_hanzi", "title_pinyin", "source_edition", "source_url", "pinyin_source", "review_status"),
    "chapters": ("title_hanzi", "title_pinyin"),
    "bites": ("pinyin",),
}
WORK_COLUMNS = ("id", "book_id", *READING_SCHEMA["works"])
CHAPTER_COLUMNS = ("id", "book_id", "work_id", "title_hanzi", "title_pinyin")
BITE_COLUMNS = ("id", "chapter_id", "original", "pinyin")


class ReadingSchemaMissing(ImportConflict):
    """The independently deployed reading schema must already be installed."""


def _text(value, label, limit, *, title=False):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > limit:
        raise ValueError(label + " must be nonempty trimmed text within its length limit")
    if title and any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(label + " must not contain control characters")
    return value


def prepare_readings(document):
    """Pair readings with the existing stable IDs and exact original text."""
    result = prepare_content(document)
    result["work"] = {
        "id": DAEHAK_ID,
        "title_hanzi": _text(document.get("title_hanzi"), "work title_hanzi", 200, title=True),
        "title_pinyin": _text(document.get("title_pinyin"), "work title_pinyin", 200, title=True),
        "source_edition": _text(document.get("edition"), "edition", 1000),
        "source_url": _text(document.get("source_url"), "source_url", 2048),
        "pinyin_source": _text(document.get("pinyin_source"), "pinyin_source", 2000),
    }
    input_bites = []
    for row, chapter in zip(result["chapters"], document["chapters"]):
        row["title_hanzi"] = _text(chapter.get("title_hanzi"), "chapter title_hanzi", 200, title=True)
        row["title_pinyin"] = _text(chapter.get("title_pinyin"), "chapter title_pinyin", 200, title=True)
        input_bites.extend(chapter["bites"])
    for row, bite in zip(result["bites"], input_bites):
        row["pinyin"] = _text(bite.get("pinyin"), "pinyin", 100000)
    return result


def _placeholders(values):
    return ",".join(["%s"] * len(values))


def _fill_fields(current, expected, fields, *, preserve_nonempty=False):
    changed = {}
    for field in fields:
        existing = current[field]
        if not isinstance(existing, str):
            raise ImportConflict("A reading field has an invalid stored value")
        if not existing.strip():
            changed[field] = expected[field]
        elif existing != expected[field] and not preserve_nonempty:
            raise ImportConflict("Existing reading differs from this edition; no fields were changed")
    return changed


def _exact_tree(actual, expected, fields):
    by_id = {row["id"]: row for row in actual}
    if set(by_id) != {row["id"] for row in expected}:
        raise ImportConflict("Existing Daehak hierarchy is incomplete or differs from this edition")
    for row in expected:
        if any(by_id[row["id"]][field] != row[field] for field in fields):
            raise ImportConflict("Existing parent or original text differs from this edition")
    return by_id


def fill_readings(document, connect=mysql_connect_from_env, *, apply=False):
    """Fill missing readings atomically, preserving all other catalogue values.

    All validation precedes writes. Locking reads prevent an administrator from
    changing a checked original or reading between comparison and update. Parent
    locks and full child-set checks also protect against concurrent additions.
    Existing nonempty provenance and review_status are always left untouched.
    """
    readings = prepare_readings(document)
    chapter_ids = [row["id"] for row in readings["chapters"]]
    bite_ids = [row["id"] for row in readings["bites"]]
    connection = connect()
    try:
        with connection.cursor() as cursor:
            # Read-only presence checks; a data repair tool must not run DDL.
            try:
                for kind, columns in READING_SCHEMA.items():
                    cursor.execute("SELECT " + ",".join(columns) + " FROM catalog_" + kind + " WHERE 1=0")
            except pymysql.err.OperationalError as exc:
                if exc.args[0] == 1054:
                    raise ReadingSchemaMissing("The Hanzi/pinyin schema is not installed; this tool never migrates it") from None
                raise
            # End the read-only schema inspection before choosing isolation.
            connection.rollback()
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.begin()
            cursor.execute("SELECT id FROM catalog_books WHERE id=%s FOR UPDATE", (CLASSICS_ID,))
            book = cursor.fetchone()
            cursor.execute("SELECT " + ",".join(WORK_COLUMNS) + " FROM catalog_works WHERE id=%s FOR UPDATE", (DAEHAK_ID,))
            work = cursor.fetchone()
            if not book or not work or work["book_id"] != CLASSICS_ID:
                raise ImportConflict("Expected classics book and Daehak work hierarchy was not found")
            cursor.execute("SELECT " + ",".join(CHAPTER_COLUMNS) + " FROM catalog_chapters WHERE work_id=%s OR id IN ("
                           + _placeholders(chapter_ids) + ") FOR UPDATE", (DAEHAK_ID, *chapter_ids))
            chapters = _exact_tree(cursor.fetchall(), readings["chapters"], ("book_id", "work_id"))
            cursor.execute("SELECT " + ",".join(BITE_COLUMNS) + " FROM catalog_bites WHERE chapter_id IN ("
                           + _placeholders(chapter_ids) + ") OR id IN (" + _placeholders(bite_ids) + ") FOR UPDATE",
                           (*chapter_ids, *bite_ids))
            bites = _exact_tree(cursor.fetchall(), readings["bites"], ("chapter_id", "original"))
            work_updates = _fill_fields(work, readings["work"], ("title_hanzi", "title_pinyin"))
            work_updates.update(_fill_fields(work, readings["work"], ("source_edition", "source_url", "pinyin_source"),
                                             preserve_nonempty=True))
            updates = [("works", DAEHAK_ID, work_updates)]
            chapter_field_count = bite_count = 0
            for row in readings["chapters"]:
                fields = _fill_fields(chapters[row["id"]], row, ("title_hanzi", "title_pinyin"))
                chapter_field_count += len(fields)
                updates.append(("chapters", row["id"], fields))
            for row in readings["bites"]:
                fields = _fill_fields(bites[row["id"]], row, ("pinyin",))
                bite_count += bool(fields)
                updates.append(("bites", row["id"], fields))
            changed = any(fields for _, _, fields in updates)
            result = {"action": ("updated" if apply else "would_update") if changed else "unchanged",
                      "chapters": len(chapter_ids), "bites": len(bite_ids),
                      "work_fields_to_fill": len(work_updates), "chapter_fields_to_fill": chapter_field_count,
                      "bites_to_fill": bite_count}
            if apply:
                for kind, item_id, fields in updates:
                    if fields:
                        cursor.execute("UPDATE catalog_" + kind + " SET " + ",".join(field + "=%s" for field in fields)
                                       + " WHERE id=%s", (*fields.values(), item_id))
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
    parser.add_argument("--content", required=True, type=Path, help="Daehak content JSON with reviewed reading pairs")
    parser.add_argument("--apply", action="store_true", help="Commit missing reading fields; otherwise only inspect")
    args = parser.parse_args(argv)
    try:
        result = fill_readings(load_content(args.content), apply=args.apply)
    except (ValueError, ImportConflict) as exc:
        print("Daehak pinyin fill refused: " + str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("Daehak pinyin fill failed. No partial update was committed; check the file, database and permissions.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
