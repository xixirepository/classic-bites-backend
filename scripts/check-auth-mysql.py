#!/usr/bin/env python3
"""Test auth persistence in a new disposable MySQL 8.4 Docker container.

Requires Docker, a locally cached mysql:8.4 image, and api/requirements-test.txt.
Never reads .env, connects to a deployed database, or uses an existing volume.
"""

import concurrent.futures
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from auth_store import AuthConflict, AuthStore, InvalidSession
from migrate_auth import migrate


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def tests_for(connect):
    class AuthMySQLTests(unittest.TestCase):
        def setUp(self):
            self.store = AuthStore(connect)
            self.prefix = uuid.uuid4().hex

        def user(self, suffix="", **values):
            return self.store.create_user(
                self.prefix + suffix + "@example.com", "Test Reader", now=1000, **values,
            )

        def tokens(self, suffix=""):
            return digest(self.prefix + suffix + "-access"), digest(self.prefix + suffix + "-refresh")

        def session(self, user=None, suffix="", **values):
            user = user or self.user(suffix)
            access, refresh = self.tokens(suffix)
            self.store.create_session(
                user["id"], access, refresh, now=1000,
                access_ttl=values.get("access_ttl", 100),
                refresh_ttl=values.get("refresh_ttl", 1000),
            )
            return user, access, refresh

        def test_migration_is_idempotent_and_preserves_users(self):
            user = self.user(password_hash="test-password-hash")
            migrate(connect)
            migrate(connect)
            self.store.check_schema()
            self.assertEqual(self.store.find_user_by_email(user["email"]), user)

        def test_user_normalization_google_and_nullable_identities(self):
            user = self.user(password_hash="test-password-hash", email_verified=True)
            self.assertEqual(self.store.find_user_by_email(" " + user["email"].upper() + " "), user)
            with self.assertRaises(AuthConflict):
                self.store.create_user(user["email"].upper(), "Duplicate", now=1000)
            google = self.user("google", google_sub=self.prefix + "-Google", email_verified=True)
            self.assertEqual(self.store.find_user_by_google_sub(google["google_sub"]), google)
            self.assertIsNone(self.store.find_user_by_google_sub(google["google_sub"].lower()))
            with self.assertRaises(AuthConflict):
                self.user("different", google_sub=google["google_sub"])
            self.user("another-password", password_hash="different-password-hash")
            self.assertIsNone(self.store.find_user_by_email(self.prefix + "missing@example.com"))

        def test_concurrent_duplicate_email_and_google_creation(self):
            for identity in ("email", "google"):
                barrier = threading.Barrier(6)

                def create(index):
                    barrier.wait(timeout=10)
                    try:
                        email_suffix = identity if identity == "email" else f"{identity}{index}"
                        return self.user(email_suffix, google_sub=self.prefix if identity == "google" else None)
                    except AuthConflict:
                        return None

                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    results = list(executor.map(create, range(6)))
                self.assertEqual(sum(result is not None for result in results), 1)

        def test_access_and_refresh_expiry_boundaries(self):
            user, access, refresh = self.session(access_ttl=20, refresh_ttl=30)
            self.assertEqual(self.store.get_user_for_access(access, 1019), user)
            with self.assertRaises(InvalidSession):
                self.store.get_user_for_access(access, 1020)
            new_access, new_refresh = self.tokens("rotated")
            result = self.store.rotate_session(refresh, new_access, new_refresh, 1025, 20)
            self.assertEqual(result["refresh_expires_at"], 1030)
            self.assertEqual(self.store.get_user_for_access(new_access, 1029), user)
            with self.assertRaises(InvalidSession):
                self.store.get_user_for_access(new_access, 1030)
            with self.assertRaises(InvalidSession):
                self.store.rotate_session(new_refresh, *self.tokens("expired"), 1030, 20)

        def test_rotation_single_use_and_reuse_revokes_session(self):
            user, access, refresh = self.session()
            new_access, new_refresh = self.tokens("new")
            self.assertEqual(self.store.rotate_session(refresh, new_access, new_refresh, 1010, 100)["user"], user)
            with self.assertRaises(InvalidSession):
                self.store.get_user_for_access(access, 1011)
            self.assertEqual(self.store.get_user_for_access(new_access, 1011), user)
            with self.assertRaises(InvalidSession):
                self.store.rotate_session(refresh, *self.tokens("reused"), 1012, 100)
            with self.assertRaises(InvalidSession):
                self.store.get_user_for_access(new_access, 1012)
            with self.assertRaises(InvalidSession):
                self.store.rotate_session(new_refresh, *self.tokens("revoked"), 1012, 100)

        def test_concurrent_refresh_has_one_winner_then_revokes_family(self):
            _, _, refresh = self.session()
            barrier = threading.Barrier(2)

            def refresh_once(index):
                barrier.wait(timeout=10)
                tokens = self.tokens(f"parallel{index}")
                try:
                    self.store.rotate_session(refresh, *tokens, 1010, 100)
                    return tokens
                except InvalidSession:
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(refresh_once, range(2)))
            winners = [result for result in results if result is not None]
            self.assertEqual(len(winners), 1)
            with self.assertRaises(InvalidSession):
                self.store.get_user_for_access(winners[0][0], 1011)
            with self.assertRaises(InvalidSession):
                self.store.rotate_session(winners[0][1], *self.tokens("after-race"), 1011, 100)

        def test_logout_old_token_and_cross_session_user_isolation(self):
            user, _, old_refresh = self.session()
            _, other_access, other_refresh = self.session(user=user, suffix="other-session")
            other_user, third_access, _ = self.session(suffix="other-user")
            access, refresh = self.tokens("first-rotated")
            self.store.rotate_session(old_refresh, access, refresh, 1010, 100)
            self.store.revoke_session(old_refresh)
            self.store.revoke_session(old_refresh)
            self.store.revoke_session(digest("unknown-" + self.prefix))
            with self.assertRaises(InvalidSession):
                self.store.get_user_for_access(access, 1011)
            with self.assertRaises(InvalidSession):
                self.store.rotate_session(refresh, *self.tokens("logged-out"), 1011, 100)
            self.assertEqual(self.store.get_user_for_access(other_access, 1011), user)
            self.assertEqual(self.store.get_user_for_access(third_access, 1011), other_user)
            self.store.revoke_session(other_refresh)
            with self.assertRaises(InvalidSession):
                self.store.get_user_for_access(other_access, 1011)

        def test_failed_rotation_rolls_back_old_token_and_access(self):
            user, access, refresh = self.session()
            _, conflicting_access, _ = self.session(user=user, suffix="collision")
            _, next_refresh = self.tokens("failed")
            with self.assertRaises(pymysql.err.IntegrityError):
                self.store.rotate_session(refresh, conflicting_access, next_refresh, 1010, 100)
            self.assertEqual(self.store.get_user_for_access(access, 1011), user)
            self.store.rotate_session(refresh, *self.tokens("successful-retry"), 1011, 100)

        def test_http_signup_login_me_refresh_logout_with_real_mysql(self):
            from fastapi.testclient import TestClient
            from auth import AuthSettings
            from main import Settings, create_app

            app = create_app(
                settings=Settings(api_key="test-media-key", access_key="test-access", secret_key="test-secret"),
                storage=object(), auth_store=self.store,
                auth_settings=AuthSettings(enabled=True, rate_limit_salt="test-rate-salt-" + self.prefix),
            )
            with TestClient(app) as client:
                signup = client.post("/auth/signup", json={
                    "email": self.prefix.upper() + "@EXAMPLE.COM",
                    "display_name": "고전 독자", "password": "test-only-passphrase-123",
                })
                self.assertEqual(signup.status_code, 201)
                account = signup.json()
                self.assertEqual(account["user"]["email"], self.prefix + "@example.com")
                self.assertNotIn("password_hash", account["user"])
                self.assertEqual(client.post("/auth/login", json={
                    "email": self.prefix + "@example.com", "password": "wrong-password",
                }).status_code, 401)
                login = client.post("/auth/login", json={
                    "email": self.prefix + "@example.com", "password": "test-only-passphrase-123",
                })
                self.assertEqual(login.status_code, 200)
                session = login.json()

                def me(token):
                    return client.get("/auth/me", headers={"Authorization": "Bearer " + token})

                self.assertEqual(me(session["access_token"]).json()["id"], account["user"]["id"])
                refreshed = client.post("/auth/refresh", json={"refresh_token": session["refresh_token"]})
                self.assertEqual(refreshed.status_code, 200)
                new_session = refreshed.json()
                self.assertEqual(me(session["access_token"]).status_code, 401)
                self.assertEqual(me(new_session["access_token"]).status_code, 200)
                self.assertEqual(client.post("/auth/logout", json={
                    "refresh_token": new_session["refresh_token"],
                }).status_code, 204)
                self.assertEqual(me(new_session["access_token"]).status_code, 401)
                self.assertEqual(client.post("/auth/refresh", json={
                    "refresh_token": new_session["refresh_token"],
                }).status_code, 401)
                self.assertEqual(me(account["access_token"]).status_code, 200)
                persisted = self.store.find_user_by_email(self.prefix + "@example.com")
                self.assertTrue(persisted["password_hash"].startswith("$argon2id$"))
                self.assertNotEqual(persisted["password_hash"], "test-only-passphrase-123")

        def test_bounded_session_cleanup_retains_live_and_revoked_history(self):
            while self.store.purge_expired_sessions(1049):
                pass
            user, _, refresh = self.session(refresh_ttl=50)
            new_access, new_refresh = self.tokens("new")
            self.store.rotate_session(refresh, new_access, new_refresh, 1010, 100)
            revoked_user, _, revoked_refresh = self.session(suffix="revoked", refresh_ttl=50)
            self.store.revoke_session(revoked_refresh)
            active_user, active_access, _ = self.session(suffix="active", refresh_ttl=5000)
            # Other test methods use later expiries. This batch should touch no
            # sessions while both active and revoked families are unexpired.
            self.assertEqual(self.store.purge_expired_sessions(1049, limit=1), 0)
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) AS count FROM auth_refresh_tokens WHERE token_hash IN (%s, %s, %s)",
                    (refresh, new_refresh, revoked_refresh),
                )
                self.assertEqual(cursor.fetchone()["count"], 3)
            self.assertEqual(self.store.purge_expired_sessions(1050, limit=1), 1)
            self.assertEqual(self.store.purge_expired_sessions(1050, limit=1), 1)
            self.assertEqual(self.store.purge_expired_sessions(1050, limit=1), 0)
            self.assertEqual(self.store.get_user_for_access(active_access, 1050), active_user)
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) AS count FROM auth_refresh_tokens WHERE token_hash IN (%s, %s, %s)",
                    (refresh, new_refresh, revoked_refresh),
                )
                self.assertEqual(cursor.fetchone()["count"], 0)
            self.assertEqual(self.store.find_user_by_email(user["email"]), user)
            self.assertEqual(self.store.find_user_by_email(revoked_user["email"]), revoked_user)

        def test_concurrent_rate_limit_window_and_bounded_cleanup(self):
            key = digest(self.prefix)
            barrier = threading.Barrier(12)

            def hit(_):
                barrier.wait(timeout=10)
                return self.store.hit_rate_limit(key, 4, 60, 1000)

            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
                results = list(executor.map(hit, range(12)))
            self.assertEqual(results.count(0), 4)
            self.assertEqual(results.count(20), 8)
            self.assertEqual(self.store.hit_rate_limit(key, 4, 60, 1020), 0)
            # A delayed request must not roll the shared row back a window.
            self.assertEqual(self.store.hit_rate_limit(key, 4, 60, 1019), 0)
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT window_started_at FROM auth_rate_limits WHERE rate_key = %s", (key,))
                self.assertEqual(cursor.fetchone()["window_started_at"], 1020)
            self.assertEqual(self.store.purge_expired_rate_limits(1079, limit=1), 0)
            self.assertEqual(self.store.purge_expired_rate_limits(1080, limit=1), 1)
            self.assertEqual(self.store.hit_rate_limit(key, 4, 60, 1080), 0)

    return unittest.defaultTestLoader.loadTestsFromTestCase(AuthMySQLTests)


def docker(*arguments, **options):
    return subprocess.run(
        ["docker", *arguments], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, **options,
    ).stdout.strip()


def main() -> int:
    container = "classic-bites-auth-test-" + uuid.uuid4().hex[:12]
    created = False
    password = secrets.token_urlsafe(32)
    try:
        docker("image", "inspect", "mysql:8.4")
        with tempfile.NamedTemporaryFile(mode="w", prefix="classic-bites-auth-test-", encoding="utf-8") as environment:
            os.chmod(environment.name, 0o600)
            environment.write(
                "MYSQL_ROOT_PASSWORD=" + secrets.token_urlsafe(32) + "\n"
                "MYSQL_DATABASE=auth_test\nMYSQL_USER=auth_test\nMYSQL_PASSWORD=" + password + "\n"
            )
            environment.flush()
            docker(
                "run", "--detach", "--rm", "--pull=never", "--name", container,
                "--label", "classic-bites.test=auth", "--env-file", environment.name,
                "--tmpfs", "/var/lib/mysql:rw,noexec,nosuid,size=768m",
                "--publish", "127.0.0.1::3306", "mysql:8.4",
            )
            created = True
        port = int(docker("port", container, "3306/tcp").rsplit(":", 1)[1])

        def connect():
            return pymysql.connect(
                host="127.0.0.1", port=port, user="auth_test", password=password,
                database="auth_test", charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                autocommit=False, connect_timeout=2, read_timeout=10, write_timeout=10,
            )

        deadline = time.monotonic() + 180
        while True:
            try:
                connection = connect()
                connection.close()
                break
            except pymysql.err.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Disposable MySQL did not become ready.") from None
                time.sleep(1)
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS version")
            print("Disposable local MySQL version:", cursor.fetchone()["version"], flush=True)
        migrate(connect)
        result = unittest.TextTestRunner(verbosity=2).run(tests_for(connect))
        return 0 if result.wasSuccessful() else 1
    except Exception as exc:
        # Docker/database diagnostics can contain generated credentials. Output
        # only the exception type, never command arguments, environment, or logs.
        print("Disposable authentication check failed (" + type(exc).__name__ + ").", file=sys.stderr)
        return 1
    finally:
        if created:
            try:
                docker("rm", "--force", container)
                print("Disposable authentication container removed.", flush=True)
            except Exception:
                print("Could not remove test container: " + container, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
