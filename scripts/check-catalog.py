#!/usr/bin/env python3
"""Catalogue HTTP contracts and authorization without external services."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
import uuid
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from auth import UserOutput, current_user
from catalog import CatalogSettings, router
from catalog_store import CatalogConflict, CatalogNotFound

ADMIN_ID = str(uuid.uuid4())
BOOK_ID = str(uuid.uuid4())
BITE_ID = str(uuid.uuid4())


def user(user_id):
    return UserOutput(id=user_id, email="test@example.com", display_name="Test", provider="password",
                      email_verified=False, created_at=datetime.now(timezone.utc))


class RecordingStore:
    def __init__(self):
        self.public_calls = []
        self.ready = False
        self.saved = None

    def list_books(self, public=True):
        self.public_calls.append(public)
        return [{"id": BOOK_ID, "title": "사서오경", "description": "", "cover_key": "images/private.jpg",
                 "sort_order": 0, "is_ready": False, "is_published": True}]

    def get_detail(self, kind, item_id, public=True):
        self.public_calls.append(public)
        if item_id not in (BOOK_ID, BITE_ID):
            raise CatalogNotFound()
        if kind == "bites":
            return {"bite": {"id": BITE_ID, "title": "Test", "chapter_id": BOOK_ID,
                             "original": "", "translation": "", "commentary": "", "sort_order": 0,
                             "is_ready": self.ready, "is_published": True}}
        return {"book": self.list_books(public)[0], "works": [], "chapters": []}

    def save(self, kind, data, item_id=None):
        self.saved = (kind, data, item_id)
        return self.get_detail(kind, item_id or BOOK_ID, False)


class CatalogHTTPTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.store = RecordingStore()
        self.app.state.catalog_settings = CatalogSettings(True, (ADMIN_ID,))
        self.app.state.catalog_store = self.store
        self.app.state.auth = SimpleNamespace()
        self.app.include_router(router)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    def authorize(self, user_id=ADMIN_ID):
        self.app.dependency_overrides[current_user] = lambda: user(user_id)

    def test_guest_list_hides_admin_fields_and_uses_public_filter(self):
        response = self.client.get("/catalog/books")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        book = response.json()["items"][0]
        self.assertNotIn("cover_key", book)
        self.assertNotIn("is_published", book)
        self.assertEqual(book["cover_url"], f"/catalog/books/{BOOK_ID}/cover")
        self.assertTrue(all(self.store.public_calls))

    def test_guest_and_regular_member_cannot_administer(self):
        self.assertEqual(self.client.post("/admin/api/books", json={"title": "Test"}).status_code, 401)
        self.authorize(str(uuid.uuid4()))
        self.assertEqual(self.client.get("/admin/api/books").status_code, 403)
        self.assertEqual(self.client.post("/admin/api/books", json={"title": "Test"}).status_code, 403)
        self.assertEqual(self.client.put(f"/admin/api/books/{BOOK_ID}", json={"title": "Test"}).status_code, 403)
        self.assertIsNone(self.store.saved)

    def test_allowlisted_admin_can_create_trimmed_draft(self):
        self.authorize()
        response = self.client.post("/admin/api/books", json={"title": "  Test  "})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.store.saved[1], {"title": "Test", "description": "", "sort_order": 0, "is_published": False})
        self.assertEqual(response.json()["book"]["cover_url"], f"/admin/api/books/{BOOK_ID}/cover")
        self.assertIn("cover_key", response.json()["book"])
        self.assertIn("is_published", response.json()["book"])

    def test_invalid_payloads_and_storage_keys_are_rejected(self):
        self.authorize()
        for data in ({"title": " "}, {"title": "a\nb"}, {"title": "x" * 201},
                     {"title": "x", "sort_order": 2147483648}, {"title": "x", "sort_order": True},
                     {"title": "x", "is_published": "false"}, {"title": "x", "cover_key": "images/private.jpg"}):
            with self.subTest(data=list(data)):
                self.assertEqual(self.client.post("/admin/api/books", json=data).status_code, 422)
        self.assertEqual(self.client.get("/catalog/books/not-a-uuid").status_code, 422)

    def test_disabled_service_and_unknown_content(self):
        self.assertEqual(self.client.get("/catalog/books/" + str(uuid.uuid4())).status_code, 404)
        self.app.state.catalog_settings = CatalogSettings(False)
        self.assertEqual(self.client.get("/catalog/books").status_code, 503)

    def test_preparing_bite_has_explicit_status_and_admin_can_edit(self):
        response = self.client.get("/catalog/bites/" + BITE_ID)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "content_preparing")
        self.authorize()
        self.assertEqual(self.client.get("/admin/api/bites/" + BITE_ID).status_code, 200)
        self.store.ready = True
        self.assertEqual(self.client.get("/catalog/bites/" + BITE_ID).status_code, 200)

    def test_conflicts_and_missing_updates_are_safe(self):
        self.authorize()
        with patch.object(self.store, "save", side_effect=CatalogConflict("parent_immutable")):
            response = self.client.put("/admin/api/books/" + BOOK_ID, json={"title": "Test"})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "parent_immutable")
        with patch.object(self.store, "save", side_effect=CatalogNotFound()):
            self.assertEqual(self.client.put("/admin/api/books/" + BOOK_ID, json={"title": "Test"}).status_code, 404)

    def test_settings_reject_invalid_ids_and_enable_value(self):
        with patch.dict("os.environ", {"CATALOG_ENABLED": "invalid"}, clear=True):
            with self.assertRaises(ValueError):
                CatalogSettings.from_env()
        with patch.dict("os.environ", {"CATALOG_ADMIN_USER_IDS": "not-an-id"}, clear=True):
            with self.assertRaises(ValueError):
                CatalogSettings.from_env()
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(CatalogSettings.from_env().enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
