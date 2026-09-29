"""Explicit, repeatable installation of the additive authentication schema."""

import hashlib
import sys
from collections.abc import Callable
from pathlib import Path

from auth_store import AuthStore, mysql_connect_from_env


def migrate(connect: Callable = mysql_connect_from_env) -> None:
    statements = [
        statement.strip()
        for statement in Path(__file__).with_name("migrations").joinpath("001_auth.sql")
        .read_text(encoding="utf-8").split(";")
        if statement.strip()
    ]
    connection = connect()
    lock_name = None
    locked = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS database_name")
            database_name = cursor.fetchone()["database_name"]
            if not database_name:
                raise RuntimeError("An application database must be selected.")
            # MySQL named locks belong to the connection and survive DDL commits.
            # Include a digest of the database to avoid serializing other projects.
            lock_name = "classic-bites-auth:" + hashlib.sha256(database_name.encode()).hexdigest()[:40]
            cursor.execute("SELECT GET_LOCK(%s, 3) AS acquired", (lock_name,))
            if cursor.fetchone()["acquired"] != 1:
                raise RuntimeError("Another authentication migration is running.")
            locked = True
            for statement in statements:
                cursor.execute(statement)
            connection.commit()
        # CREATE IF NOT EXISTS alone does not ensure pre-existing tables have the
        # required columns. Validate explicitly after all four additive steps.
        AuthStore(connect).check_schema()
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


def main() -> int:
    try:
        migrate()
    except Exception:
        print("Authentication migration failed. Check database settings, permissions and connectivity.", file=sys.stderr)
        return 1
    print("Authentication schema is ready. Existing tables and data were preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
