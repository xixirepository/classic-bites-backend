#!/usr/bin/env python3
"""Catalogue HTTP contracts and authorization without external services."""

from datetime import datetime, timezone
from copy import deepcopy
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
WORK_ID = str(uuid.uuid4())
CHAPTER_ID = str(uuid.uuid4())


def user(user_id):
    return UserOutput(id=user_id, email="test@example.com", display_name="Test", provider="password",
                      email_verified=False, created_at=datetime.now(timezone.utc))


class RecordingStore:
    def __init__(self):
        self.public_calls = []
        self.ready = False
        self.saved = None
        self.learning = None

    def list_books(self, public=True):
        self.public_calls.append(public)
        return [{"id": BOOK_ID, "title": "사서오경", "description": "", "cover_key": "images/private.jpg",
                 "sort_order": 0, "is_ready": False, "is_published": True}]

    def get_detail(self, kind, item_id, public=True):
        self.public_calls.append(public)
        if item_id not in (BOOK_ID, BITE_ID, WORK_ID, CHAPTER_ID):
            raise CatalogNotFound()
        if kind == "bites":
            return {"bite": {"id": BITE_ID, "title": "Test", "chapter_id": BOOK_ID,
                             "original": "", "pinyin": "míng", "translation": "", "commentary": "", "learning": self.learning, "sort_order": 0,
                             "is_ready": self.ready, "is_published": True}}
        if kind == "works":
            return {"work": {**self.list_books(public)[0], "id": WORK_ID, "book_id": BOOK_ID,
                             "title_hanzi": "大學", "title_pinyin": "Dà Xué", "source_edition": "Test edition",
                             "source_url": "https://example.com/source", "pinyin_source": "Test only",
                             "review_status": "draft"}, "chapters": []}
        if kind == "chapters":
            return {"chapter": {"id": CHAPTER_ID, "book_id": BOOK_ID, "work_id": WORK_ID,
                                "title": "Test", "title_hanzi": "一", "title_pinyin": "Yī", "sort_order": 0,
                                "is_ready": self.ready, "is_published": True},
                    "bites": [self.get_detail("bites", BITE_ID, public)["bite"]]}
        return {"book": self.list_books(public)[0], "works": [], "chapters": []}

    def get_work_reader(self, item_id):
        if item_id != WORK_ID:
            raise CatalogNotFound()
        return {"work": self.get_detail("works", item_id)["work"],
                "chapters": [self.get_detail("chapters", CHAPTER_ID)]}

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

    def test_reader_keeps_tone_marks_and_hides_all_management_fields(self):
        response = self.client.get(f"/catalog/works/{WORK_ID}/reader")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        result = response.json()
        self.assertEqual(result["work"]["title_hanzi"], "大學")
        self.assertEqual(result["work"]["title_pinyin"], "Dà Xué")
        chapter = result["chapters"][0]
        self.assertEqual(chapter["chapter"]["title_pinyin"], "Yī")
        self.assertEqual(chapter["bites"][0]["pinyin"], "míng")
        for row in (result["work"], chapter["chapter"], chapter["bites"][0]):
            for field in ("is_published", "cover_key", "source_edition", "source_url", "pinyin_source", "review_status"):
                self.assertNotIn(field, row)
        self.assertTrue(all(self.store.public_calls))
        summary = self.client.get(f"/catalog/chapters/{CHAPTER_ID}").json()["bites"][0]
        self.assertNotIn("pinyin", summary)

    def test_reader_preparing_unknown_and_disabled_responses(self):
        result = self.client.get(f"/catalog/works/{WORK_ID}/reader")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["work"]["is_ready"])
        self.assertFalse(result.json()["chapters"][0]["bites"][0]["is_ready"])
        self.assertEqual(self.client.get(f"/catalog/works/{uuid.uuid4()}/reader").status_code, 404)
        self.app.state.catalog_settings = CatalogSettings(False)
        self.assertEqual(self.client.get(f"/catalog/works/{WORK_ID}/reader").status_code, 503)

    def test_reading_metadata_and_pinyin_validate_and_preserve_existing_content(self):
        self.authorize()
        data = {"title": "대학", "book_id": BOOK_ID, "title_hanzi": " 大學 ", "title_pinyin": " Dà Xué ",
                "source_edition": " Test edition ", "source_url": " https://example.com/source ",
                "pinyin_source": " Test only ", "review_status": "reviewed"}
        response = self.client.put(f"/admin/api/works/{WORK_ID}", json=data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.saved[1]["title_pinyin"], "Dà Xué")
        self.assertEqual(self.store.saved[1]["source_edition"], "Test edition")
        self.assertIn("review_status", response.json()["work"])
        for field, invalid in (("title_hanzi", "a\nb"), ("title_pinyin", "x" * 201),
                               ("source_edition", "x" * 1001), ("source_url", "x" * 2049),
                               ("pinyin_source", "x" * 2001), ("review_status", "verified")):
            with self.subTest(field=field):
                self.assertEqual(self.client.put(f"/admin/api/works/{WORK_ID}", json={**data, field: invalid}).status_code, 422)
        bite = {"title": "Test", "chapter_id": CHAPTER_ID, "original": " 明 ", "pinyin": " míng ",
                "translation": "보존할 번역", "commentary": "보존할 해설"}
        self.assertEqual(self.client.put(f"/admin/api/bites/{BITE_ID}", json=bite).status_code, 200)
        self.assertEqual(self.store.saved[1]["pinyin"], "míng")
        self.assertEqual(self.store.saved[1]["translation"], "보존할 번역")
        self.assertEqual(self.store.saved[1]["commentary"], "보존할 해설")
        self.assertEqual(self.client.put(f"/admin/api/bites/{BITE_ID}", json={**bite, "pinyin": "x" * 100001}).status_code, 422)

    def test_learning_response_and_omitted_edit_fields(self):
        learning = {"version": 1, "source_note": "검사용 설명", "units": [{
            "id": "first", "title": "밝음", "original": "明", "pinyin": "míng",
            "opening_question": "밝다는 것은?", "glossary": [{"term": "明", "reading": "명", "meaning": "밝다"}],
            "translation": "밝다", "summary": "밝음의 뜻", "everyday_example": "오늘의 예시",
            "check": {"question": "뜻은?", "choices": ["밝다", "어둡다"], "answer_index": 0, "explanation": "명은 밝음입니다."}}]}
        self.store.learning = learning
        self.store.ready = True
        self.assertEqual(self.client.get(f"/catalog/bites/{BITE_ID}").json()["bite"]["learning"], learning)
        reader = self.client.get(f"/catalog/works/{WORK_ID}/reader").json()
        self.assertEqual(reader["chapters"][0]["bites"][0]["learning"], learning)
        self.assertNotIn("learning", self.client.get(f"/catalog/chapters/{CHAPTER_ID}").json()["bites"][0])
        self.authorize()
        base = {"title": "수정 제목", "chapter_id": CHAPTER_ID}
        self.assertEqual(self.client.put(f"/admin/api/bites/{BITE_ID}", json=base).status_code, 200)
        self.assertEqual(self.store.saved[1], base)
        self.assertEqual(self.client.put(f"/admin/api/bites/{BITE_ID}", json={**base, "learning": None}).status_code, 200)
        self.assertIn("learning", self.store.saved[1])
        self.assertIsNone(self.store.saved[1]["learning"])
        response = self.client.put(f"/admin/api/bites/{BITE_ID}", json={**base, "learning": learning})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.saved[1]["learning"], learning)
        invalid = deepcopy(learning)
        invalid["units"][0]["check"]["answer_index"] = 2
        self.assertEqual(self.client.put(f"/admin/api/bites/{BITE_ID}", json={**base, "learning": invalid}).status_code, 422)
        with patch.object(self.store, "save", side_effect=CatalogConflict("learning_source_mismatch")):
            response = self.client.put(f"/admin/api/bites/{BITE_ID}", json=base)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "learning_source_mismatch")


if __name__ == "__main__":
    unittest.main(verbosity=2)
