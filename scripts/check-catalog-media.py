#!/usr/bin/env python3
"""Isolated catalogue cover, origin, resource-limit and response-cache tests."""
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from minio.error import S3Error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from auth import AuthSettings, current_user
from catalog import CatalogSettings
from catalog_guard import admin_origins_from_env
from catalog_store import CatalogNotFound
from main import Settings, create_app

ADMIN_ID = str(uuid.uuid4())
BOOK_ID = str(uuid.uuid4())
ORIGIN = "http://127.0.0.1:8020"
# Test image payload; parser uses a signature check, browser decoding is separate.
PNG = b"\x89PNG\r\n\x1a\n" + b"cover-test"


class FakeStore:
    key = None
    published = True
    calls = 0

    def get_detail(self, kind, item_id, public=True):
        self.calls += 1
        if item_id != BOOK_ID or public and not self.published:
            raise CatalogNotFound()
        return {"book": {"cover_key": self.key}}

    def set_cover(self, kind, item_id, key):
        self.key = key


class Download:
    def __init__(self, data, mime):
        self.data = data
        self.headers = {"Content-Type": mime}
        self.closed = False
        self.released = False

    def stream(self, amt):
        yield self.data

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class FakeStorage:
    def __init__(self):
        self.objects = {}
        self.downloads = []
        self.reads = 0

    def put_object(self, bucket, key, stream, size, *, content_type, num_parallel_uploads):
        data = stream.read()
        assert len(data) == size
        self.objects[key] = data, content_type

    def get_object(self, bucket, key):
        self.reads += 1
        if key not in self.objects:
            raise S3Error(response=None, code="NoSuchKey", message="not found", resource=None, request_id=None, host_id=None)
        obj = Download(*self.objects[key])
        self.downloads.append(obj)
        return obj


class CatalogMediaTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.storage = FakeStorage()
        with patch.dict("os.environ", {"CATALOG_ADMIN_ORIGINS": ORIGIN}):
            self.app = create_app(
                settings=Settings(api_key="test-media", access_key="test-access", secret_key="test-secret"),
                storage=self.storage, auth_store=object(),
                auth_settings=AuthSettings(enabled=True, rate_limit_salt="test-catalog-media-rate-salt-123456"),
                catalog_store=self.store, catalog_settings=CatalogSettings(True, (ADMIN_ID,)),
            )
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.path = f"/admin/api/books/{BOOK_ID}/cover"
        self.public_path = f"/catalog/books/{BOOK_ID}/cover"

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for obj in self.storage.downloads:
            self.assertTrue(obj.closed)
            self.assertTrue(obj.released)

    def authorize(self, user_id=ADMIN_ID):
        from types import SimpleNamespace
        self.app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=user_id)

    def upload(self, data=PNG):
        return self.client.post(self.path, files={"file": ("test.png", data, "image/png")})

    def test_authentication_and_authorization_precede_multipart_parser(self):
        with patch("catalog_media.StrictMultipartParser", side_effect=AssertionError("Parser must not run")):
            self.assertEqual(self.upload().status_code, 401)
            self.authorize(str(uuid.uuid4()))
            self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.storage.objects, {})
        self.assertEqual(self.store.calls, 0)

    def test_upload_stream_read_and_unpublish_blocks_storage_access(self):
        self.authorize()
        self.assertEqual(self.upload().status_code, 200)
        self.assertTrue(self.store.key.startswith("images/catalog/"))
        response = self.client.get(self.public_path)
        self.assertEqual(response.content, PNG)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.store.published = False
        count = self.storage.reads
        self.assertEqual(self.client.get(self.public_path).status_code, 404)
        self.assertEqual(self.storage.reads, count)
        self.assertEqual(self.client.get(self.path).status_code, 200)

    def test_invalid_type_and_size_do_not_replace_existing_cover(self):
        self.authorize()
        self.upload().raise_for_status()
        key = self.store.key
        self.assertEqual(self.upload(b"<svg><script>test</script></svg>").status_code, 415)
        with patch("catalog_media.COVER_BYTES", 12):
            self.assertEqual(self.upload(PNG + b"x" * 20).status_code, 413)
        self.assertEqual(self.store.key, key)
        self.assertEqual(len(self.storage.objects), 1)
        # The bounded upload slot is released after rejected writes.
        self.assertEqual(self.upload().status_code, 200)

    def test_missing_object_and_unsafe_stored_mime(self):
        self.assertEqual(self.client.get(self.public_path).status_code, 404)
        self.store.key = "images/catalog/missing.png"
        self.assertEqual(self.client.get(self.public_path).status_code, 404)
        self.storage.objects[self.store.key] = (b"<html>", "text/html")
        self.assertEqual(self.client.get(self.public_path).status_code, 415)

    def test_all_error_responses_are_no_store_and_hide_input(self):
        response = self.client.get("/catalog/books/not-a-uuid")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("not-a-uuid", response.text)
        response = self.client.get(self.public_path)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.authorize()
        response = self.client.post("/admin/api/books", content=b"x" * (2 * 1024 * 1024 + 1), headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_only_explicit_origins_receive_cors_permission(self):
        for origin, expected in ((ORIGIN, 200), ("https://unlisted.example", 400)):
            response = self.client.options(self.path, headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type"})
            self.assertEqual(response.status_code, expected)
            self.assertEqual(response.headers.get("access-control-allow-origin"), ORIGIN if expected == 200 else None)
        response = self.client.get(self.public_path, headers={"Origin": ORIGIN})
        self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_origin_configuration_rejects_wildcards_credentials_and_remote_http(self):
        for origin in ("*", "http://example.com", "https://user:secret@example.com", "https://example.com/path", "https://example.com?query=x"):
            with self.subTest(origin=origin), patch.dict("os.environ", {"CATALOG_ADMIN_ORIGINS": origin}):
                with self.assertRaises(ValueError):
                    admin_origins_from_env()


if __name__ == "__main__":
    unittest.main(verbosity=2)
