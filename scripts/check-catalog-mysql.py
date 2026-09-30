#!/usr/bin/env python3
"""Real catalogue DB + HTTP checks in a disposable local MySQL container only."""

import importlib.util
from pathlib import Path
import sys
import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from auth import AuthService, AuthSettings, router as auth_router
from auth_store import AuthStore
from catalog import CatalogSettings, router
from catalog_store import CatalogConflict, CatalogNotFound, CatalogStore
from migrate_auth import migrate as migrate_auth
from migrate_catalog import CLASSICS_ID, CLASSIC_WORKS, migrate


def tests_for(connect):
    class CatalogMySQLTests(unittest.TestCase):
        def setUp(self):
            self.store = CatalogStore(connect)
            self.prefix = uuid.uuid4().hex

        def create(self, kind, **values):
            data = {"title": "Test " + self.prefix, "is_published": True, "sort_order": 0}
            if kind in ("books", "works"):
                data["description"] = ""
            if kind == "chapters":
                data["work_id"] = None
            if kind == "bites":
                data.update(original="", translation="", commentary="")
            data.update(values)
            singular = {"books": "book", "works": "work", "chapters": "chapter", "bites": "bite"}[kind]
            return self.store.save(kind, data)[singular]

        def update(self, kind, row, **values):
            data = {key: value for key, value in row.items() if key not in ("id", "cover_key", "is_ready")}
            data.update(values)
            return self.store.save(kind, data, row["id"])

        def tree(self, with_work=True):
            book = self.create("books")
            work = self.create("works", book_id=book["id"]) if with_work else None
            chapter = self.create("chapters", book_id=book["id"], work_id=work["id"] if work else None)
            bite = self.create("bites", chapter_id=chapter["id"], original="검증용 원문", translation="검증용 번역", commentary="검증용 해설")
            return book, work, chapter, bite

        def test_migration_seed_is_repeatable_and_preserves_edits(self):
            book = self.create("books")
            migrate(connect, seed=True)
            self.assertEqual(len(self.store.get_detail("books", CLASSICS_ID)["works"]), 9)
            seed = self.store.get_detail("books", CLASSICS_ID)["book"]
            self.assertFalse(seed["is_ready"])
            self.update("books", seed, title="관리자 수정 보존", is_published=False)
            migrate(connect, seed=True)
            migrate(connect, seed=True)
            current = self.store.get_detail("books", CLASSICS_ID, False)
            self.assertEqual(current["book"]["title"], "관리자 수정 보존")
            self.assertFalse(current["book"]["is_published"])
            self.assertEqual([row["title"] for row in current["works"]], list(CLASSIC_WORKS))
            self.assertEqual(self.store.get_detail("books", book["id"])["book"]["title"], book["title"])

        def test_direct_and_nested_chapters_are_distinct(self):
            book, work, nested, bite = self.tree()
            direct = self.create("chapters", book_id=book["id"])
            result = self.store.get_detail("books", book["id"])
            self.assertEqual([x["id"] for x in result["chapters"]], [direct["id"]])
            self.assertEqual([x["id"] for x in result["works"]], [work["id"]])
            self.assertEqual([x["id"] for x in self.store.get_detail("works", work["id"])["chapters"]], [nested["id"]])
            self.assertTrue(result["book"]["is_ready"])
            self.assertFalse(result["chapters"][0]["is_ready"])

        def test_every_ancestor_unpublish_blocks_direct_reads_and_cover_metadata(self):
            for hidden_kind, hidden_index in (("books", 0), ("works", 1), ("chapters", 2), ("bites", 3)):
                with self.subTest(hidden_kind=hidden_kind):
                    rows = self.tree()
                    self.update(hidden_kind, rows[hidden_index], is_published=False)
                    for kind, row in zip(("books", "works", "chapters", "bites"), rows):
                        if ("books", "works", "chapters", "bites").index(kind) >= hidden_index:
                            with self.assertRaises(CatalogNotFound):
                                self.store.get_detail(kind, row["id"])
                        self.assertFalse(self.store.get_detail(kind, row["id"], False)[kind[:-1] if kind != "bites" else "bite"]["is_ready"])
                    self.assertEqual(self.store.get_detail("chapters", rows[2]["id"], False)["bites"][0]["id"], rows[3]["id"])

        def test_pending_content_and_visibility_control_readiness(self):
            book, _, chapter, bite = self.tree(False)
            self.update("bites", bite, original="", translation="", commentary="")
            self.assertFalse(self.store.get_detail("books", book["id"])["book"]["is_ready"])
            self.assertFalse(self.store.get_detail("bites", bite["id"])["bite"]["is_ready"])
            self.update("bites", bite, original="", translation="검증용 번역만", commentary="")
            self.assertTrue(self.store.get_detail("books", book["id"])["book"]["is_ready"])
            self.update("chapters", chapter, is_published=False)
            self.assertFalse(self.store.get_detail("books", book["id"])["book"]["is_ready"])

        def test_schema_rejects_cross_book_and_missing_parent_atomically(self):
            book, work, chapter, bite = self.tree()
            other = self.create("books")
            with self.assertRaises(CatalogConflict):
                self.create("chapters", book_id=other["id"], work_id=work["id"])
            with self.assertRaises(CatalogConflict):
                self.create("bites", chapter_id=str(uuid.uuid4()))
            self.assertEqual(self.store.get_detail("books", other["id"])["chapters"], [])
            self.assertEqual(len(self.store.get_detail("chapters", chapter["id"])["bites"]), 1)

        def test_parent_change_rejected_without_partial_update(self):
            book, work, chapter, bite = self.tree()
            other = self.create("books")
            with self.assertRaises(CatalogConflict) as caught:
                self.update("works", work, book_id=other["id"], title="Must not save")
            self.assertEqual(caught.exception.code, "parent_immutable")
            persisted = self.store.get_detail("works", work["id"])["work"]
            self.assertEqual(persisted["title"], work["title"])
            self.assertEqual(persisted["book_id"], book["id"])

        def test_sort_order_and_id_tiebreak_are_stable(self):
            book = self.create("books")
            works = [self.create("works", book_id=book["id"], sort_order=order) for order in (2, -1, 2)]
            actual = self.store.get_detail("books", book["id"])["works"]
            self.assertEqual([item["id"] for item in actual], [item["id"] for item in sorted(works, key=lambda row: (row["sort_order"], row["id"]))])

        def test_cover_persists_across_content_edit_and_new_store(self):
            book = self.create("books")
            self.store.set_cover("books", book["id"], "images/catalog/test.jpg")
            self.update("books", book, title="수정한 제목")
            current = CatalogStore(connect).get_detail("books", book["id"])["book"]
            self.assertEqual(current["cover_key"], "images/catalog/test.jpg")
            self.assertEqual(current["title"], "수정한 제목")
            self.store.set_cover("books", book["id"], None)
            self.assertIsNone(self.store.get_detail("books", book["id"])["book"]["cover_key"])
            with self.assertRaises(CatalogNotFound):
                self.store.set_cover("books", str(uuid.uuid4()), "images/none.jpg")

        def test_http_real_account_admin_to_guest_read_and_unpublish(self):
            migrate_auth(connect)
            app = FastAPI()
            app.state.catalog_store = self.store
            app.state.catalog_settings = CatalogSettings(True)
            app.state.auth = AuthService(AuthSettings(enabled=True, rate_limit_salt="test-catalog-rate-salt-" + self.prefix), AuthStore(connect))
            app.include_router(auth_router)
            app.include_router(router)
            try:
                with TestClient(app) as client:
                    signup = client.post("/auth/signup", json={"email": self.prefix + "@example.com", "password": "Test-password-only-1234", "display_name": "Test Admin"})
                    self.assertEqual(signup.status_code, 201)
                    token = signup.json()
                    headers = {"Authorization": "Bearer " + token["access_token"]}
                    self.assertEqual(client.post("/admin/api/books", headers=headers, json={"title": "Test"}).status_code, 403)
                    app.state.catalog_settings = CatalogSettings(True, (token["user"]["id"],))
                    created = client.post("/admin/api/books", headers=headers, json={"title": "검증용 책", "is_published": True})
                    self.assertEqual(created.status_code, 201)
                    book_id = created.json()["book"]["id"]
                    chapter = client.post("/admin/api/chapters", headers=headers, json={"title": "검증용 장", "book_id": book_id, "is_published": True}).json()["chapter"]
                    bite = client.post("/admin/api/bites", headers=headers, json={"title": "검증용 한입", "chapter_id": chapter["id"], "is_published": True}).json()["bite"]
                    bite_path = "/catalog/bites/" + bite["id"]
                    self.assertEqual(client.get(bite_path).status_code, 409)
                    client.put("/admin/api/bites/" + bite["id"], headers=headers, json={"title": "검증용 한입", "chapter_id": chapter["id"], "original": "검증용 원문", "translation": "검증용 번역", "commentary": "검증용 해설", "is_published": True}).raise_for_status()
                    self.assertEqual(client.get(bite_path).json()["bite"]["translation"], "검증용 번역")
                    self.assertTrue(client.get("/catalog/books/" + book_id).json()["book"]["is_ready"])
                    client.put("/admin/api/books/" + book_id, headers=headers, json={"title": "검증용 책", "is_published": False}).raise_for_status()
                    self.assertEqual(client.get(bite_path).status_code, 404)
                    self.assertNotIn(book_id, [row["id"] for row in client.get("/catalog/books").json()["items"]])
            finally:
                app.state.auth.close()

    return unittest.defaultTestLoader.loadTestsFromTestCase(CatalogMySQLTests)


def main():
    # Reuse the established disposable runner, not any configured server DB.
    spec = importlib.util.spec_from_file_location("auth_mysql_runner", ROOT / "scripts/check-auth-mysql.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.migrate = migrate
    runner.tests_for = tests_for
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
