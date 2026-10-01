"""Add only missing Daehak learning supplements; plan by default, --apply to write."""

import argparse
import json
from pathlib import Path
import sys

import pymysql

from auth_store import mysql_connect_from_env
from catalog_learning import decode_learning, validate_learning
from fill_daehak_pinyin import prepare_readings
from import_daehak import CLASSICS_ID, DAEHAK_ID, ImportConflict, load_content


MAX_DOCUMENT_BYTES = 4 * 1024 * 1024


class LearningSchemaMissing(ImportConflict):
    """Install the additive learning schema separately before importing content."""


def prepare_learning(document, source):
    """Match every supplement to the stable ID and unchanged source corpus."""
    if (not isinstance(document, dict) or set(document) != {"version", "work_id", "bites"}
            or type(document["version"]) is not int or document["version"] != 1
            or document["work_id"] != DAEHAK_ID or not isinstance(document["bites"], list)):
        raise ValueError("Learning document needs version 1, the Daehak work ID and bites")
    expected = prepare_readings(source)
    by_key = {}
    for chapter in source["chapters"]:
        for bite in chapter["bites"]:
            by_key[(chapter["key"], bite["key"])] = None
    if len(document["bites"]) != len(by_key):
        raise ValueError("Learning document must include every source bite exactly once")
    for entry in document["bites"]:
        if not isinstance(entry, dict) or set(entry) != {"chapter_key", "bite_key", "learning"}:
            raise ValueError("Each learning entry needs chapter_key, bite_key and learning")
        if not isinstance(entry["chapter_key"], str) or not isinstance(entry["bite_key"], str):
            raise ValueError("Source keys must be strings")
        key = (entry["chapter_key"], entry["bite_key"])
        if key not in by_key or by_key[key] is not None or entry["learning"] is None:
            raise ValueError("Unknown, duplicate or empty learning entry")
        by_key[key] = entry["learning"]
    ordered_keys = [(chapter["key"], bite["key"]) for chapter in source["chapters"] for bite in chapter["bites"]]
    for row, key in zip(expected["bites"], ordered_keys):
        row["learning"] = validate_learning(by_key[key], row["original"], row["pinyin"])
    return expected


def load_learning(path):
    raw = Path(path).read_bytes()
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("Learning document exceeds its byte limit")
    return json.loads(raw)


def _placeholders(values):
    return ",".join(["%s"] * len(values))


def _same_tree(actual, expected, fields):
    current = {row["id"]: row for row in actual}
    if set(current) != {row["id"] for row in expected}:
        raise ImportConflict("The existing Daehak hierarchy differs; no learning was changed")
    for row in expected:
        if any(current[row["id"]][field] != row[field] for field in fields):
            raise ImportConflict("The existing source or parent differs; no learning was changed")
    return current


def import_learning(document, source, connect=mysql_connect_from_env, *, apply=False):
    """Compare a locked snapshot, then fill NULL learning in one transaction.

    Source text, pinyin, translations, commentaries, provenance, titles, order,
    IDs and publication flags are never updated. Existing learning must be an
    exact structural match; an administrator's different supplement is retained.
    """
    expected = prepare_learning(document, source)
    chapter_ids = [row["id"] for row in expected["chapters"]]
    bite_ids = [row["id"] for row in expected["bites"]]
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("SELECT original,pinyin,learning FROM catalog_bites WHERE 1=0")
            except (pymysql.err.ProgrammingError, pymysql.err.OperationalError) as exc:
                if exc.args[0] in (1054, 1146):
                    raise LearningSchemaMissing("The reading and learning schema must already be installed") from None
                raise
            # End schema inspection before starting the locking transaction.
            connection.rollback()
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.begin()
            cursor.execute("SELECT id FROM catalog_books WHERE id=%s FOR UPDATE", (CLASSICS_ID,))
            book = cursor.fetchone()
            cursor.execute("SELECT id,book_id FROM catalog_works WHERE id=%s FOR UPDATE", (DAEHAK_ID,))
            work = cursor.fetchone()
            if not book or not work or work["book_id"] != CLASSICS_ID:
                raise ImportConflict("The existing classics/Daehak parents were not found unchanged")
            cursor.execute(
                "SELECT id,book_id,work_id FROM catalog_chapters WHERE work_id=%s OR id IN ("
                + _placeholders(chapter_ids) + ") ORDER BY id FOR UPDATE", (DAEHAK_ID, *chapter_ids),
            )
            _same_tree(cursor.fetchall(), expected["chapters"], ("book_id", "work_id"))
            cursor.execute(
                "SELECT id,chapter_id,original,pinyin,learning FROM catalog_bites WHERE chapter_id IN ("
                + _placeholders(chapter_ids) + ") OR id IN (" + _placeholders(bite_ids)
                + ") ORDER BY id FOR UPDATE", (*chapter_ids, *bite_ids),
            )
            current = _same_tree(cursor.fetchall(), expected["bites"], ("chapter_id", "original", "pinyin"))
            pending = []
            for row in expected["bites"]:
                try:
                    saved = decode_learning(current[row["id"]]["learning"])
                except (ValueError, TypeError):
                    raise ImportConflict("Existing learning is invalid; no learning was changed") from None
                if saved is None:
                    pending.append(row)
                elif saved != row["learning"]:
                    raise ImportConflict("Existing learning differs; no learning was changed")
            if apply:
                for row in pending:
                    cursor.execute("UPDATE catalog_bites SET learning=%s WHERE id=%s",
                                   (json.dumps(row["learning"], ensure_ascii=False), row["id"]))
                connection.commit()
            else:
                connection.rollback()
            return {"action": ("updated" if apply else "would_update") if pending else "unchanged",
                    "chapters": len(chapter_ids), "bites": len(bite_ids), "bites_to_fill": len(pending),
                    "units": sum(len(row["learning"]["units"]) for row in expected["bites"])}
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    content = Path(__file__).with_name("content")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", type=Path, default=content / "daehak-learning.json")
    parser.add_argument("--source-content", type=Path, default=content / "daehak.json")
    parser.add_argument("--apply", action="store_true", help="Fill only missing supplements after checking the locked source")
    args = parser.parse_args()
    try:
        result = import_learning(load_learning(args.content), load_content(args.source_content), apply=args.apply)
    except (ValueError, ImportConflict):
        print("Learning import refused: check the schema, complete content and unchanged source. No changes were committed.", file=sys.stderr)
        return 1
    except Exception:
        print("Learning import did not confirm completion. Check database settings and state before retrying.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
