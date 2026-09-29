"""MySQL persistence for accounts and revocable, rotating token sessions."""

import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pymysql


class AuthConflict(Exception):
    """An account already owns the requested email or Google identity."""


class InvalidSession(Exception):
    """A token is unknown, expired, revoked, or has already been used."""


def mysql_connect_from_env():
    """Open a bounded connection without logging configuration or credentials."""
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=3,
        read_timeout=5,
        write_timeout=5,
    )


USER_COLUMNS = "id, email, display_name, password_hash, google_sub, email_verified, created_at"


def _user(row: dict | None) -> dict | None:
    if row is None:
        return None
    result = {key: row[key] for key in USER_COLUMNS.split(", ")}
    result["email_verified"] = bool(result["email_verified"])
    result["created_at"] = int(result["created_at"])
    return result


class AuthStore:
    def __init__(self, connect: Callable):
        self._connect = connect

    @classmethod
    def from_env(cls):
        return cls(mysql_connect_from_env)

    @contextmanager
    def _transaction(self) -> Iterator:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_user(
        self, email: str, display_name: str, password_hash: str | None = None,
        google_sub: str | None = None, email_verified: bool = False,
        now: int | None = None,
    ) -> dict:
        created = int(time.time()) if now is None else now
        user = {
            "id": str(uuid.uuid4()), "email": email.strip().lower(),
            "display_name": display_name, "password_hash": password_hash,
            "google_sub": google_sub, "email_verified": bool(email_verified),
            "created_at": created,
        }
        try:
            with self._transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO auth_users (" + USER_COLUMNS + ") "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    tuple(user[key] for key in USER_COLUMNS.split(", ")),
                )
        except pymysql.err.IntegrityError as exc:
            if exc.args[0] == 1062:
                raise AuthConflict("An account already owns this identity.") from None
            raise
        return user

    def find_user_by_email(self, email: str) -> dict | None:
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT " + USER_COLUMNS + " FROM auth_users WHERE email = %s",
                (email.strip().lower(),),
            )
            return _user(cursor.fetchone())

    def find_user_by_google_sub(self, sub: str) -> dict | None:
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT " + USER_COLUMNS + " FROM auth_users WHERE google_sub = %s", (sub,),
            )
            return _user(cursor.fetchone())

    def create_session(
        self, user_id: str, access_hash: str, refresh_hash: str, now: int,
        access_ttl: int, refresh_ttl: int,
    ) -> dict:
        if access_ttl <= 0 or refresh_ttl <= 0:
            raise ValueError("Session lifetimes must be positive.")
        session_id = str(uuid.uuid4())
        expires = now + refresh_ttl
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO auth_sessions "
                "(id, user_id, access_hash, access_expires_at, refresh_expires_at, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (session_id, user_id, access_hash, min(now + access_ttl, expires), expires, now),
            )
            cursor.execute(
                "INSERT INTO auth_refresh_tokens (token_hash, session_id, created_at) "
                "VALUES (%s, %s, %s)", (refresh_hash, session_id, now),
            )
        return {"refresh_expires_at": expires}

    def get_user_for_access(self, access_hash: str, now: int) -> dict:
        columns = ", ".join("u." + key for key in USER_COLUMNS.split(", "))
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT " + columns + " FROM auth_users u "
                "JOIN auth_sessions s ON s.user_id = u.id "
                "WHERE s.access_hash = %s AND s.revoked_at IS NULL "
                "AND s.access_expires_at > %s AND s.refresh_expires_at > %s",
                (access_hash, now, now),
            )
            user = _user(cursor.fetchone())
            if user is None:
                raise InvalidSession()
            return user

    @staticmethod
    def _lock_refresh_session(cursor, refresh_hash: str) -> tuple[dict | None, dict | None]:
        # Discover the immutable parent id without a lock. All mutations lock the
        # parent session before its token, including simultaneous refresh/logout.
        cursor.execute(
            "SELECT session_id FROM auth_refresh_tokens WHERE token_hash = %s", (refresh_hash,),
        )
        reference = cursor.fetchone()
        if reference is None:
            return None, None
        cursor.execute(
            "SELECT id, user_id, refresh_expires_at, revoked_at FROM auth_sessions "
            "WHERE id = %s FOR UPDATE", (reference["session_id"],),
        )
        session = cursor.fetchone()
        if session is None:
            return None, None
        # This locking read sees the latest committed version even when the
        # discovery query established a REPEATABLE READ snapshot before a wait.
        cursor.execute(
            "SELECT token_hash, session_id, used_at FROM auth_refresh_tokens "
            "WHERE token_hash = %s FOR UPDATE", (refresh_hash,),
        )
        return session, cursor.fetchone()

    def rotate_session(
        self, refresh_hash: str, new_access_hash: str, new_refresh_hash: str,
        now: int, access_ttl: int,
    ) -> dict:
        if access_ttl <= 0:
            raise ValueError("Access lifetime must be positive.")
        reused = False
        result = None
        with self._transaction() as connection, connection.cursor() as cursor:
            session, token = self._lock_refresh_session(cursor, refresh_hash)
            if session is None or token is None:
                raise InvalidSession()
            if token["used_at"] is not None:
                cursor.execute(
                    "UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, %s) WHERE id = %s",
                    (now, session["id"]),
                )
                reused = True
            elif session["revoked_at"] is not None or session["refresh_expires_at"] <= now:
                raise InvalidSession()
            else:
                cursor.execute(
                    "SELECT " + USER_COLUMNS + " FROM auth_users WHERE id = %s", (session["user_id"],),
                )
                user = _user(cursor.fetchone())
                if user is None:
                    raise InvalidSession()
                cursor.execute(
                    "UPDATE auth_refresh_tokens SET used_at = %s WHERE token_hash = %s",
                    (now, refresh_hash),
                )
                cursor.execute(
                    "INSERT INTO auth_refresh_tokens (token_hash, session_id, created_at) "
                    "VALUES (%s, %s, %s)", (new_refresh_hash, session["id"], now),
                )
                cursor.execute(
                    "UPDATE auth_sessions SET access_hash = %s, access_expires_at = %s WHERE id = %s",
                    (new_access_hash, min(now + access_ttl, session["refresh_expires_at"]), session["id"]),
                )
                result = {"user": user, "refresh_expires_at": int(session["refresh_expires_at"])}
        # Reuse revocation must commit before the caller handles the rejection.
        if reused:
            raise InvalidSession()
        return result

    def revoke_session(self, refresh_hash: str) -> None:
        with self._transaction() as connection, connection.cursor() as cursor:
            session, token = self._lock_refresh_session(cursor, refresh_hash)
            if session is not None and token is not None:
                cursor.execute(
                    "UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, %s) WHERE id = %s",
                    (int(time.time()), session["id"]),
                )

    def hit_rate_limit(self, key: str, limit: int, window_seconds: int, now: int) -> int:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("Rate limit and window must be positive.")
        window_start = now - now % window_seconds
        expires = window_start + window_seconds
        with self._transaction() as connection, connection.cursor() as cursor:
            # The insert/upsert takes an exclusive row lock even for a new key,
            # so independent API workers cannot each admit the first request.
            cursor.execute(
                "INSERT INTO auth_rate_limits (rate_key, window_started_at, request_count, expires_at) "
                "VALUES (%s, %s, 0, %s) ON DUPLICATE KEY UPDATE rate_key = rate_key",
                (key, window_start, expires),
            )
            cursor.execute(
                "SELECT window_started_at, request_count, expires_at FROM auth_rate_limits "
                "WHERE rate_key = %s FOR UPDATE", (key,),
            )
            row = cursor.fetchone()
            # A request queued before a window boundary may acquire the lock
            # after a newer request. Never reset the row to an older window.
            if row["window_started_at"] >= window_start:
                window_start = row["window_started_at"]
                expires = row["expires_at"]
                count = row["request_count"]
            else:
                count = 0
            if count >= limit:
                return max(1, expires - now)
            cursor.execute(
                "UPDATE auth_rate_limits SET window_started_at = %s, request_count = %s, expires_at = %s "
                "WHERE rate_key = %s", (window_start, count + 1, expires, key),
            )
        return 0

    def purge_expired_rate_limits(self, now: int, limit: int = 500) -> int:
        """Bounded maintenance; call outside the request's rate-limit transaction."""
        if not 1 <= limit <= 10000:
            raise ValueError("Cleanup batch size must be between 1 and 10000.")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM auth_rate_limits WHERE expires_at <= %s ORDER BY expires_at LIMIT %s",
                (now, limit),
            )
            return cursor.rowcount

    def purge_expired_sessions(self, now: int, limit: int = 500) -> int:
        """Delete expired families, retaining reuse evidence until absolute expiry."""
        if not 1 <= limit <= 10000:
            raise ValueError("Cleanup batch size must be between 1 and 10000.")
        with self._transaction() as connection, connection.cursor() as cursor:
            # Match rotation/logout's parent-before-child lock order. The expiry
            # index includes the primary id, keeping the batch scan bounded.
            cursor.execute(
                "SELECT id FROM auth_sessions WHERE refresh_expires_at <= %s "
                "ORDER BY refresh_expires_at, id LIMIT %s FOR UPDATE SKIP LOCKED",
                (now, limit),
            )
            session_ids = [row["id"] for row in cursor.fetchall()]
            for session_id in session_ids:
                cursor.execute("DELETE FROM auth_refresh_tokens WHERE session_id = %s", (session_id,))
                cursor.execute("DELETE FROM auth_sessions WHERE id = %s", (session_id,))
            return len(session_ids)

    def check_schema(self) -> None:
        """Check required tables/columns without creating or changing schema."""
        queries = (
            "SELECT " + USER_COLUMNS + " FROM auth_users WHERE 1 = 0",
            "SELECT id, user_id, access_hash, access_expires_at, refresh_expires_at, created_at, revoked_at "
            "FROM auth_sessions WHERE 1 = 0",
            "SELECT token_hash, session_id, created_at, used_at FROM auth_refresh_tokens WHERE 1 = 0",
            "SELECT rate_key, window_started_at, request_count, expires_at FROM auth_rate_limits WHERE 1 = 0",
        )
        with self._transaction() as connection, connection.cursor() as cursor:
            for query in queries:
                cursor.execute(query)
