"""Repeatable additive catalogue migration; optional titles-only classics seed."""

import argparse
import hashlib
from pathlib import Path
import re
import sys
import uuid

from auth_store import mysql_connect_from_env
from catalog_store import CatalogStore

SEED_NAMESPACE = uuid.UUID("a1d0d2be-8d86-4a16-bd9c-177b030541c0")
CLASSICS_ID = str(uuid.uuid5(SEED_NAMESPACE, "classics"))
CLASSIC_WORKS = ("대학", "중용", "논어", "맹자", "시경", "서경", "역경", "예기", "춘추")


def seed_classics(connect):
    """Stable IDs make reruns safe; never overwrite or republish edited entries."""
    store = CatalogStore(connect)
    with store._transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO catalog_books (id,title,description,sort_order,is_published) "
            "VALUES (%s,%s,%s,0,1) ON DUPLICATE KEY UPDATE id=id", (CLASSICS_ID, "사서오경", ""),
        )
        for index, title in enumerate(CLASSIC_WORKS):
            item_id = str(uuid.uuid5(SEED_NAMESPACE, "classics/" + title))
            cursor.execute(
                "INSERT INTO catalog_works (id,book_id,title,description,sort_order,is_published) "
                "VALUES (%s,%s,%s,%s,%s,1) ON DUPLICATE KEY UPDATE id=id",
                (item_id, CLASSICS_ID, title, "", index),
            )


def migrate(connect=mysql_connect_from_env, *, seed=False):
    migrations = Path(__file__).with_name("migrations")
    statements = [statement.strip() for statement in migrations
                  .joinpath("002_catalog.sql").read_text(encoding="utf-8").split(";") if statement.strip()]
    additions = [statement.strip() for statement in migrations
                 .joinpath("003_hanzi_pinyin.sql").read_text(encoding="utf-8").split(";") if statement.strip()]
    connection = connect()
    lock_name = None
    locked = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS database_name")
            database = cursor.fetchone()["database_name"]
            if not database:
                raise RuntimeError("An application database must be selected")
            lock_name = "classic-bites-catalog:" + hashlib.sha256(database.encode()).hexdigest()[:40]
            cursor.execute("SELECT GET_LOCK(%s, 3) AS acquired", (lock_name,))
            if cursor.fetchone()["acquired"] != 1:
                raise RuntimeError("Another catalogue migration is running")
            locked = True
            for statement in statements:
                cursor.execute(statement)
            for statement in additions:
                # MySQL DDL commits individually. Inspect each column under the
                # migration lock so a partial migration can be safely resumed.
                match = re.fullmatch(r"ALTER TABLE (catalog_[a-z]+) ADD COLUMN ([a-z_]+) .+", statement)
                if match is None:
                    raise RuntimeError("Unsupported additive catalogue migration")
                table, column = match.groups()
                cursor.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s AND column_name=%s",
                    (database, table, column),
                )
                if cursor.fetchone() is None:
                    cursor.execute(statement)
            connection.commit()
        CatalogStore(connect).check_schema()
        if seed:
            seed_classics(connect)
    except BaseException:
        connection.rollback()
        raise
    finally:
        try:
            if locked:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        finally:
            connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-classics", action="store_true", help="Create only the classics book and nine work titles")
    args = parser.parse_args()
    try:
        migrate(seed=args.seed_classics)
    except Exception:
        print("Catalogue migration failed. Check database settings, permissions and connectivity.", file=sys.stderr)
        return 1
    print("Catalogue schema is ready. Existing tables and data were preserved.")
    if args.seed_classics:
        print("Classics titles are ready; existing edits were preserved and no lesson text was added.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
