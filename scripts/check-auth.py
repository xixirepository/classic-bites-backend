#!/usr/bin/env python3
"""Isolated authentication contract and security regression tests.

Run from the repository root after installing api/requirements-test.txt:
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-auth.py

No real database, network, Google account, credentials, or deployment is used.
The in-memory store is a test double for HTTP/service behavior, not proof of
MySQL transactions. Google tests verify real RSA signatures against an ephemeral
test certificate; only certificate transport is replaced.
"""

import base64
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import pymysql


source = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(source))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from google.auth.exceptions import TransportError  # noqa: E402

from auth import AuthSettings, BoundedGoogleRequest, GoogleVerifier, digest  # noqa: E402
from auth_guard import AuthGuardMiddleware  # noqa: E402
from auth_store import AuthConflict, InvalidSession  # noqa: E402
from main import Settings, create_app  # noqa: E402


PASSWORD = "test-only-passphrase-123"
CLIENT_ID = "test-web.apps.googleusercontent.com"
AUTH_SETTINGS = AuthSettings(enabled=True, google_client_ids=(CLIENT_ID,),
                             rate_limit_salt="test-only-rate-limit-salt-32-characters")
MEDIA_SETTINGS = Settings(api_key="test-media-api-key", access_key="test-access",
                          secret_key="test-secret")


class FakeStore:
    """Small stateful boundary double; production SQL has separate integration tests."""

    def __init__(self):
        self.users = {}
        self.sessions = []
        self.limits = {}

    def create_user(self, email, display_name, *, now, password_hash=None,
                    google_sub=None, email_verified=False):
        if self.find_user_by_email(email) or (google_sub and self.find_user_by_google_sub(google_sub)):
            raise AuthConflict()
        user = {"id": str(uuid.uuid4()), "email": email, "display_name": display_name,
                "password_hash": password_hash, "google_sub": google_sub,
                "email_verified": email_verified, "created_at": now}
        self.users[user["id"]] = user
        return dict(user)

    def find_user_by_email(self, email):
        return next((dict(user) for user in self.users.values() if user["email"] == email), None)

    def find_user_by_google_sub(self, subject):
        return next((dict(user) for user in self.users.values() if user["google_sub"] == subject), None)

    def create_session(self, user_id, access_hash, refresh_hash, now, access_ttl, refresh_ttl):
        session = {"user_id": user_id, "access_hash": access_hash, "refresh_hash": refresh_hash,
                   "access_expires_at": now + access_ttl, "refresh_expires_at": now + refresh_ttl,
                   "revoked": False, "previous": set()}
        self.sessions.append(session)
        return dict(session)

    def get_user_for_access(self, token_hash, now):
        for session in self.sessions:
            if (session["access_hash"] == token_hash and not session["revoked"]
                    and now < session["access_expires_at"] and now < session["refresh_expires_at"]):
                return dict(self.users[session["user_id"]])
        raise InvalidSession()

    def rotate_session(self, token_hash, access_hash, refresh_hash, now, access_ttl):
        for session in self.sessions:
            if token_hash in session["previous"]:
                session["revoked"] = True
                raise InvalidSession()
            if session["refresh_hash"] == token_hash:
                if session["revoked"] or now >= session["refresh_expires_at"]:
                    raise InvalidSession()
                session["previous"].add(token_hash)
                session.update(access_hash=access_hash, refresh_hash=refresh_hash,
                               access_expires_at=min(now + access_ttl, session["refresh_expires_at"]))
                return {"user": dict(self.users[session["user_id"]]),
                        "refresh_expires_at": session["refresh_expires_at"]}
        raise InvalidSession()

    def revoke_session(self, token_hash):
        for session in self.sessions:
            if session["refresh_hash"] == token_hash or token_hash in session["previous"]:
                session["revoked"] = True

    def hit_rate_limit(self, key, count, window, now):
        attempts = self.limits.setdefault(key, [])
        attempts[:] = [timestamp for timestamp in attempts if timestamp > now - window]
        if len(attempts) >= count:
            return max(1, window - (now - attempts[0]))
        attempts.append(now)
        return 0


class FakeGoogle:
    def __init__(self):
        self.identity = {"sub": "google-test-subject", "email": "google.user@gmail.com",
                         "name": "구글 사용자", "email_verified": True}

    def verify(self, token):
        return dict(self.identity)


@contextmanager
def client_for(*, settings=AUTH_SETTINGS, store=None, verifier=None):
    app = create_app(settings=MEDIA_SETTINGS, storage=object(), auth_settings=settings,
                     auth_store=store or FakeStore(), google_verifier=verifier)
    with TestClient(app) as client:
        yield client


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        self.store, self.google = FakeStore(), FakeGoogle()
        self.context = client_for(store=self.store, verifier=self.google)
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.now = 1800000000
        self.client.app.state.auth.clock = lambda: self.now

    def signup(self, email="reader@example.com", **overrides):
        payload = {"email": email, "password": PASSWORD, "display_name": "고전 독자"}
        payload.update(overrides)
        return self.client.post("/auth/signup", json=payload)

    def me(self, token):
        return self.client.get("/auth/me", headers={"Authorization": "Bearer " + token})

    def test_signup_login_and_me_publish_only_safe_user_fields(self):
        response = self.signup("  READER@EXAMPLE.COM ", display_name="  고전 독자  ")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["is_new_user"])
        self.assertEqual(body["user"]["email"], "reader@example.com")
        self.assertEqual(body["user"]["display_name"], "고전 독자")
        self.assertFalse(body["user"]["email_verified"])
        self.assertEqual(body["user"]["provider"], "password")
        self.assertEqual(len(body["access_token"]), 43)
        self.assertEqual(len(body["refresh_token"]), 43)
        self.assertNotEqual(body["access_token"], body["refresh_token"])
        self.assertEqual(body["expires_in"], AUTH_SETTINGS.access_ttl)
        self.assertEqual(response.headers["cache-control"], "no-store")
        user = self.store.find_user_by_email("reader@example.com")
        self.assertTrue(user["password_hash"].startswith("$argon2id$"))
        self.assertNotIn(PASSWORD, response.text)
        self.assertNotIn(user["password_hash"], response.text)
        stored = self.store.sessions[0]
        self.assertEqual(stored["access_hash"], digest(body["access_token"]))
        self.assertEqual(stored["refresh_hash"], digest(body["refresh_token"]))
        self.assertNotIn(body["access_token"], repr(stored))
        profile = self.me(body["access_token"])
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json(), body["user"])
        self.assertNotIn("password_hash", profile.text)
        self.assertNotIn("google_sub", profile.text)
        logged_in = self.client.post("/auth/login", json={"email": "READER@example.com", "password": PASSWORD})
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(logged_in.json()["user"]["id"], body["user"]["id"])
        self.assertFalse(logged_in.json()["is_new_user"])

    def test_duplicate_signup_is_case_insensitive_and_preserves_password(self):
        self.assertEqual(self.signup().status_code, 201)
        first = self.store.find_user_by_email("reader@example.com")
        duplicate = self.signup("READER@EXAMPLE.COM", password="different-test-passphrase")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["detail"]["code"], "email_in_use")
        self.assertEqual(len(self.store.users), 1)
        self.assertEqual(self.store.find_user_by_email("reader@example.com")["password_hash"], first["password_hash"])

    def test_wrong_missing_and_google_only_credentials_have_same_error(self):
        self.signup()
        self.client.post("/auth/google", json={"id_token": "test-token"})
        responses = [self.client.post("/auth/login", json={"email": email, "password": "incorrect"})
                     for email in ("reader@example.com", "missing@example.com", "google.user@gmail.com")]
        self.assertTrue(all(response.status_code == 401 for response in responses))
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(responses[0].json(), responses[2].json())

    def test_me_rejects_absent_malformed_and_refresh_credentials(self):
        tokens = self.signup().json()
        for authorization in (None, "Basic abc", "Bearer short", "Bearer " + "z" * 43,
                              "Bearer " + tokens["refresh_token"]):
            with self.subTest(authorization=authorization):
                headers = {} if authorization is None else {"Authorization": authorization}
                response = self.client.get("/auth/me", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_refresh_rotates_tokens_and_passes_original_absolute_expiry(self):
        original = self.signup().json()
        self.now += AUTH_SETTINGS.access_ttl
        self.assertEqual(self.me(original["access_token"]).status_code, 401)
        response = self.client.post("/auth/refresh", json={"refresh_token": original["refresh_token"]})
        self.assertEqual(response.status_code, 200)
        rotated = response.json()
        self.assertNotEqual(rotated["access_token"], original["access_token"])
        self.assertNotEqual(rotated["refresh_token"], original["refresh_token"])
        self.assertEqual(rotated["refresh_expires_in"], AUTH_SETTINGS.refresh_ttl - AUTH_SETTINGS.access_ttl)
        self.assertEqual(self.me(rotated["access_token"]).status_code, 200)
        self.assertEqual(self.me(original["access_token"]).status_code, 401)

    def test_refresh_store_reuse_rejection_is_401_and_revokes_rotated_session(self):
        original = self.signup().json()
        rotated = self.client.post("/auth/refresh", json={"refresh_token": original["refresh_token"]}).json()
        replay = self.client.post("/auth/refresh", json={"refresh_token": original["refresh_token"]})
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(replay.json()["detail"]["code"], "invalid_token")
        self.assertEqual(self.me(rotated["access_token"]).status_code, 401)

    def test_refresh_expired_or_unknown_token_cannot_issue_session(self):
        tokens = self.signup().json()
        self.now += AUTH_SETTINGS.refresh_ttl
        for token in (tokens["refresh_token"], "z" * 43, tokens["access_token"]):
            self.assertEqual(self.client.post("/auth/refresh", json={"refresh_token": token}).status_code, 401)

    def test_logout_revokes_only_its_session_and_is_idempotent(self):
        first = self.signup().json()
        second = self.client.post("/auth/login", json={"email": "reader@example.com", "password": PASSWORD}).json()
        for unused in range(2):
            response = self.client.post("/auth/logout", json={"refresh_token": first["refresh_token"]})
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.content, b"")
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.me(first["access_token"]).status_code, 401)
        self.assertEqual(self.me(second["access_token"]).status_code, 200)
        self.assertEqual(self.client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code, 401)

    def test_google_first_and_repeat_signin_share_subject_identity(self):
        first = self.client.post("/auth/google", json={"id_token": "test-token"})
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["is_new_user"])
        self.assertEqual(first.json()["user"]["provider"], "google")
        self.google.identity["email"] = "changed@gmail.com"
        second = self.client.post("/auth/google", json={"id_token": "test-token"})
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["is_new_user"])
        self.assertEqual(first.json()["user"]["id"], second.json()["user"]["id"])
        self.assertEqual(len(self.store.users), 1)

    def test_google_email_collision_never_links_existing_password_account(self):
        original = self.signup("google.user@gmail.com").json()
        result = self.client.post("/auth/google", json={"id_token": "test-token"})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["detail"]["code"], "email_in_use")
        self.assertEqual(len(self.store.users), 1)
        self.assertIsNone(self.store.find_user_by_email("google.user@gmail.com")["google_sub"])
        self.assertEqual(self.me(original["access_token"]).status_code, 200)

    def test_google_email_collision_with_different_subject_does_not_merge_users(self):
        first = self.client.post("/auth/google", json={"id_token": "test-token"}).json()
        self.google.identity["sub"] = "different-google-subject"
        result = self.client.post("/auth/google", json={"id_token": "test-token"})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(len(self.store.users), 1)
        self.assertEqual(self.me(first["access_token"]).status_code, 200)

    def test_validation_errors_do_not_reflect_credentials_or_unexpected_fields(self):
        secret_password = "validation-password-marker-" * 8
        secret_google = "validation-id-token-marker-" * 400
        secret_refresh = "validation-refresh-marker"
        cases = [("signup", {"email": "reader@example.com", "password": secret_password,
                              "display_name": "reader"}, secret_password),
                 ("google", {"id_token": secret_google}, secret_google),
                 ("refresh", {"refresh_token": secret_refresh}, secret_refresh),
                 ("login", {"email": "not-an-email", "password": PASSWORD,
                             "password_hash": "hash-leak-marker"}, "hash-leak-marker"),
                 ("login", {"email": "reader@example.com", "password": PASSWORD,
                             "secret-extra-key-marker": "ignored"}, "secret-extra-key-marker"),
                 ("login", {"email": {"secret-nested-key-marker": "ignored"},
                             "password": PASSWORD}, "secret-nested-key-marker")]
        for path, payload, sensitive in cases:
            with self.subTest(path=path):
                result = self.client.post("/auth/" + path, json=payload)
                self.assertEqual(result.status_code, 422)
                self.assertNotIn(sensitive, result.text)
                self.assertNotIn(PASSWORD, result.text)
                self.assertNotIn('"input"', result.text)
                self.assertEqual(result.headers["cache-control"], "no-store")

    def test_invalid_json_does_not_echo_request_body(self):
        marker = "malformed-json-password-marker"
        result = self.client.post("/auth/login", content='{"password":"' + marker,
                                  headers={"Content-Type": "application/json"})
        self.assertEqual(result.status_code, 422)
        self.assertNotIn(marker, result.text)

    def test_login_rate_limit_returns_retry_after_and_later_recovers(self):
        for unused in range(10):
            response = self.client.post("/auth/login", json={"email": "absent@example.com", "password": "incorrect"})
            self.assertEqual(response.status_code, 401)
        limited = self.client.post("/auth/login", json={"email": "ABSENT@example.com", "password": "incorrect"})
        self.assertEqual(limited.status_code, 429)
        self.assertGreater(int(limited.headers["retry-after"]), 0)
        self.assertTrue(all("absent" not in key for key in self.store.limits))
        self.now += 301
        self.assertEqual(self.client.post("/auth/login", json={"email": "absent@example.com", "password": "incorrect"}).status_code, 401)

    def test_capacity_limit_rejects_and_releases_without_creating_user(self):
        semaphore = self.client.app.state.auth.expensive_work
        semaphore.acquire()
        semaphore.acquire()
        try:
            result = self.signup()
            self.assertEqual(result.status_code, 429)
            self.assertEqual(len(self.store.users), 0)
        finally:
            semaphore.release()
            semaphore.release()
        self.assertEqual(self.signup().status_code, 201)

    def test_ip_limit_cannot_be_bypassed_with_forwarding_headers(self):
        # All test requests share the same direct peer; arbitrary forwarding
        # headers must not create new client identities in application code.
        for unused in range(60):
            result = self.client.post("/auth/logout", json={"refresh_token": "z" * 43})
            self.assertEqual(result.status_code, 204)
        result = self.client.post("/auth/logout", json={"refresh_token": "z" * 43},
                                  headers={"X-Forwarded-For": "203.0.113.25", "X-Real-IP": "203.0.113.26"})
        self.assertEqual(result.status_code, 429)
        self.assertGreater(int(result.headers["retry-after"]), 0)

    def test_user_token_does_not_grant_media_administration(self):
        tokens = self.signup().json()
        result = self.client.get("/files", headers={"Authorization": "Bearer " + tokens["access_token"]})
        self.assertEqual(result.status_code, 401)

    def test_database_error_never_exposes_driver_details(self):
        with patch.object(self.store, "find_user_by_email", side_effect=pymysql.OperationalError(2003, "private-db-detail-marker")):
            result = self.client.post("/auth/login", json={"email": "reader@example.com", "password": PASSWORD})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["detail"]["code"], "auth_unavailable")
        self.assertNotIn("private-db-detail-marker", result.text)
        self.assertEqual(result.headers["cache-control"], "no-store")


class AuthConfigurationTests(unittest.TestCase):
    def test_disabled_auth_keeps_health_available_and_does_not_create_users(self):
        store = FakeStore()
        with client_for(settings=replace(AUTH_SETTINGS, enabled=False), store=store) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            for method, path, payload in (("GET", "me", None), ("POST", "login", {"email": "reader@example.com", "password": PASSWORD})):
                response = client.request(method, "/auth/" + path, json=payload)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["detail"]["code"], "auth_disabled")
            self.assertFalse(store.users)

    def test_google_without_configuration_fails_closed(self):
        with client_for(settings=replace(AUTH_SETTINGS, google_client_ids=())) as client:
            result = client.post("/auth/google", json={"id_token": "test-token"})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()["detail"]["code"], "google_not_configured")


class GoogleSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "isolated-auth-test")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(cls.key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                .sign(cls.key, hashes.SHA256()))
        cls.certificates = json.dumps({"test-key": cert.public_bytes(serialization.Encoding.PEM).decode()}).encode()

    def setUp(self):
        self.verifier = GoogleVerifier((CLIENT_ID,))
        self.addCleanup(self.verifier.close)
        self.verifier.request = lambda *args, **kwargs: SimpleNamespace(status=200, data=self.certificates)
        now = int(time.time())
        self.claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "test-google-subject",
                       "iat": now - 5, "exp": now + 300, "email": "reader@gmail.com",
                       "email_verified": True, "name": "고전 독자"}

    @staticmethod
    def encode_part(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=")

    def signed(self, **changes):
        claims = {**self.claims, **changes}
        body = self.encode_part({"alg": "RS256", "kid": "test-key", "typ": "JWT"}) + b"." + self.encode_part(claims)
        signature = self.key.sign(body, padding.PKCS1v15(), hashes.SHA256())
        return (body + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()

    def assert_invalid(self, token):
        with self.assertRaises(HTTPException) as caught:
            self.verifier.verify(token)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.detail["code"], "invalid_google_token")

    def test_valid_signature_identity_and_email_authority(self):
        identity = self.verifier.verify(self.signed())
        self.assertEqual(identity["sub"], self.claims["sub"])
        self.assertTrue(identity["email_verified"])
        external = self.verifier.verify(self.signed(email="reader@example.com"))
        self.assertFalse(external["email_verified"])
        workspace = self.verifier.verify(self.signed(email="reader@example.com", hd="example.com"))
        self.assertTrue(workspace["email_verified"])

    def test_wrong_audience_issuer_expiry_future_issued_time_and_unverified_email(self):
        now = int(time.time())
        cases = [{"aud": "attacker.apps.googleusercontent.com"}, {"iss": "https://attacker.invalid"},
                 {"iat": now - 600, "exp": now - 300}, {"iat": now + 300, "exp": now + 600},
                 {"email_verified": False}, {"email_verified": "true"}, {"sub": ""},
                 {"sub": "비정상"}, {"email": "invalid-email"}]
        for claims in cases:
            with self.subTest(claims=claims):
                self.assert_invalid(self.signed(**claims))

    def test_tampered_payload_unsigned_and_malformed_tokens(self):
        header, payload, signature = self.signed().split(".")
        changed = self.encode_part({**self.claims, "sub": "attacker"}).decode()
        self.assert_invalid(header + "." + changed + "." + signature)
        unsigned = self.encode_part({"alg": "none", "typ": "JWT"}).decode() + "." + payload + "."
        self.assert_invalid(unsigned)
        self.assert_invalid("not-a-jwt")

    def test_transport_failure_is_unavailable_and_never_authenticates(self):
        def unavailable(*args, **kwargs):
            raise TransportError("test-only network failure")
        self.verifier.request = unavailable
        with client_for(verifier=self.verifier) as client:
            result = client.post("/auth/google", json={"id_token": self.signed()})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()["detail"]["code"], "google_unavailable")
            self.assertNotIn("test-only network failure", result.text)

    def test_google_transport_has_explicit_bounded_timeout(self):
        with patch("auth.GoogleRequest.__call__", return_value=object()) as transport:
            request = BoundedGoogleRequest()
            request("https://example.invalid/certs", timeout=300)
            self.assertEqual(transport.call_args.kwargs["timeout"], 5)
            request.session.close()


class AuthBodyGuardTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, chunks, *, limit=16384, path="/auth/login"):
        called = []
        messages = []
        pending = list(chunks)

        async def app(scope, receive, send):
            called.append(await receive())
            await send({"type": "http.response.start", "status": 200, "headers": [(b"cache-control", b"public")]})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            chunk = pending.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "method": "POST", "path": path, "headers": []}
        await AuthGuardMiddleware(app, max_bytes=limit)(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        return start, called

    async def test_chunked_request_without_content_length_cannot_exceed_cap(self):
        start, called = await self.invoke([b"a" * 8192, b"b" * 8193])
        self.assertEqual(start["status"], 413)
        self.assertFalse(called)
        self.assertEqual(dict(start["headers"])[b"cache-control"], b"no-store")

    async def test_exact_body_limit_preserves_bytes_and_replaces_cache_headers(self):
        start, called = await self.invoke([b"a" * 8192, b"b" * 8192])
        self.assertEqual(start["status"], 200)
        self.assertEqual(called[0]["body"], b"a" * 8192 + b"b" * 8192)
        cache_headers = [(key, value) for key, value in start["headers"] if key.lower() == b"cache-control"]
        self.assertEqual(cache_headers, [(b"cache-control", b"no-store")])

    async def test_disconnected_request_never_reaches_authentication(self):
        called = []
        async def app(scope, receive, send):
            called.append(True)
        async def receive():
            return {"type": "http.disconnect"}
        async def send(message):
            self.fail("A response cannot be sent after this disconnected request")
        await AuthGuardMiddleware(app)({"type": "http", "path": "/auth/signup"}, receive, send)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
