#!/usr/bin/env python3
"""Verify the Daehak importer using only a disposable local MySQL database."""

from copy import deepcopy
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
from catalog import CatalogSettings, router
from catalog_store import CatalogStore
from import_daehak import DAEHAK_ID, ImportConflict, import_content, load_content, prepare_content
from migrate_catalog import CLASSICS_ID, migrate, seed_classics


def fixture():
    return {
        "edition": "독립 MySQL 검사 전용 가상 본문", "source_url": "https://example.com/test-only",
        "chapters": [{
            "key": "chapter-" + str(index), "title": "검사용 장 " + str(index),
            "bites": [{"key": "bite-" + str(part), "title": "검사용 한입 " + str(part),
                       "original": "大學之道 " + str(index) + "/" + str(part),
                       "translation": "검증용 번역", "commentary": "검증용 해설"}
                      for part in range(2)],
        } for index in range(11)],
    }


def tests_for(connect):
    class DaehakImportTests(unittest.TestCase):
        def setUp(self):
            self.document = fixture()
            self.execute("DELETE FROM catalog_bites")
            self.execute("DELETE FROM catalog_chapters")
            self.execute("DELETE FROM catalog_works")
            self.execute("DELETE FROM catalog_books")
            seed_classics(connect)

        def execute(self, sql, values=()):
            connection = connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(sql, values)
                    rows = cursor.fetchall()
                connection.commit()
                return rows
            finally:
                connection.close()

        def snapshot(self):
            return {kind: self.execute("SELECT * FROM catalog_" + kind + " ORDER BY id")
                    for kind in ("books", "works", "chapters", "bites")}

        def apply(self):
            return import_content(self.document, connect, apply=True)

        def assert_refused_unchanged(self):
            before = self.snapshot()
            for apply in (False, True):
                with self.assertRaises(ImportConflict):
                    import_content(self.document, connect, apply=apply)
                self.assertEqual(self.snapshot(), before)

        def test_default_dry_run_writes_nothing(self):
            before = self.snapshot()
            self.assertEqual(import_content(self.document, connect), {"action": "would_create", "chapters": 11, "bites": 22})
            self.assertEqual(self.snapshot(), before)

        def test_atomic_apply_repeat_and_public_http_order_and_text(self):
            parents = self.snapshot()
            self.assertEqual(self.apply(), {"action": "created", "chapters": 11, "bites": 22})
            after = self.snapshot()
            self.assertEqual(after["books"], parents["books"])
            self.assertEqual(after["works"], parents["works"])
            self.assertEqual(self.apply()["action"], "unchanged")
            self.assertEqual(import_content(self.document, connect)["action"], "unchanged")
            self.assertEqual(self.snapshot(), after)
            app = FastAPI()
            app.state.catalog_store = CatalogStore(connect)
            app.state.catalog_settings = CatalogSettings(True)
            app.include_router(router)
            with TestClient(app) as client:
                book_response = client.get("/catalog/books/" + CLASSICS_ID)
                self.assertEqual(book_response.status_code, 200)
                self.assertTrue(book_response.json()["book"]["is_ready"])
                self.assertEqual(book_response.json()["chapters"], [])
                self.assertEqual(len(book_response.json()["works"]), 9)
                work_response = client.get("/catalog/works/" + DAEHAK_ID)
                self.assertEqual(work_response.status_code, 200)
                self.assertEqual(work_response.headers["cache-control"], "no-store")
                work = work_response.json()
                self.assertTrue(work["work"]["is_ready"])
                self.assertEqual([row["title"] for row in work["chapters"]], [row["title"] for row in self.document["chapters"]])
                self.assertEqual([row["sort_order"] for row in work["chapters"]], list(range(11)))
                for chapter, expected in zip(work["chapters"], self.document["chapters"]):
                    self.assertTrue(chapter["is_ready"])
                    result = client.get("/catalog/chapters/" + chapter["id"]).json()
                    self.assertEqual([row["sort_order"] for row in result["bites"]], [0, 1])
                    self.assertEqual([row["title"] for row in result["bites"]], [row["title"] for row in expected["bites"]])
                    for bite, expected_bite in zip(result["bites"], expected["bites"]):
                        response = client.get("/catalog/bites/" + bite["id"])
                        self.assertEqual(response.status_code, 200)
                        actual = response.json()["bite"]
                        self.assertTrue(actual["is_ready"])
                        self.assertEqual(actual["chapter_id"], chapter["id"])
                        for field in ("original", "translation", "commentary"):
                            self.assertEqual(actual[field], expected_bite[field])

        def test_edited_text_title_order_or_publication_is_preserved(self):
            for table, field, value in (("bites", "translation", "관리자 수정"), ("chapters", "title", "관리자 목차"),
                                        ("chapters", "sort_order", 99), ("bites", "is_published", False)):
                with self.subTest(table=table, field=field):
                    self.setUp()
                    self.apply()
                    self.execute("UPDATE catalog_" + table + " SET " + field + "=%s ORDER BY id LIMIT 1", (value,))
                    self.assert_refused_unchanged()

        def test_partial_import_and_unrelated_existing_chapter_are_preserved(self):
            self.apply()
            self.execute("DELETE FROM catalog_bites ORDER BY id LIMIT 1")
            self.assert_refused_unchanged()
            self.setUp()
            self.execute("INSERT INTO catalog_chapters (id,book_id,work_id,title,is_published) VALUES (%s,%s,%s,%s,1)",
                         (str(uuid.uuid4()), CLASSICS_ID, DAEHAK_ID, "기존 목차"))
            self.assert_refused_unchanged()

        def test_extra_bite_is_preserved(self):
            self.apply()
            chapter_id = self.execute("SELECT id FROM catalog_chapters ORDER BY sort_order LIMIT 1")[0]["id"]
            self.execute("INSERT INTO catalog_bites (id,chapter_id,title,original,translation,commentary,is_published) "
                         "VALUES (%s,%s,%s,%s,%s,%s,1)", (str(uuid.uuid4()), chapter_id, "추가 한입", "原文", "번역", ""))
            self.assert_refused_unchanged()

        def test_missing_renamed_hidden_or_reparented_parent_is_refused(self):
            for sql, values in (
                ("DELETE FROM catalog_works WHERE id=%s", (DAEHAK_ID,)),
                ("UPDATE catalog_works SET title=%s WHERE id=%s", ("편집한 작품", DAEHAK_ID)),
                ("UPDATE catalog_books SET is_published=0 WHERE id=%s", (CLASSICS_ID,)),
                ("UPDATE catalog_works SET is_published=0 WHERE id=%s", (DAEHAK_ID,)),
            ):
                with self.subTest(sql=sql):
                    self.setUp()
                    self.execute(sql, values)
                    self.assert_refused_unchanged()
            self.setUp()
            other_book = str(uuid.uuid4())
            self.execute("INSERT INTO catalog_books (id,title,description,is_published) VALUES (%s,%s,'',1)", (other_book, "다른 책"))
            self.execute("UPDATE catalog_works SET book_id=%s WHERE id=%s", (other_book, DAEHAK_ID))
            self.assert_refused_unchanged()

        def test_id_collision_outside_daehak_is_preserved(self):
            expected = prepare_content(self.document)
            self.execute("INSERT INTO catalog_chapters (id,book_id,title,is_published) VALUES (%s,%s,%s,1)",
                         (expected["chapters"][0]["id"], CLASSICS_ID, "다른 위치의 목차"))
            self.assert_refused_unchanged()
            self.setUp()
            other_chapter = str(uuid.uuid4())
            self.execute("INSERT INTO catalog_chapters (id,book_id,title,is_published) VALUES (%s,%s,%s,1)",
                         (other_chapter, CLASSICS_ID, "책 직속 장"))
            self.execute("INSERT INTO catalog_bites (id,chapter_id,title,original,translation,commentary,is_published) "
                         "VALUES (%s,%s,%s,%s,%s,%s,1)",
                         (expected["bites"][0]["id"], other_chapter, "다른 위치의 한입", "原文", "번역", ""))
            self.assert_refused_unchanged()

        def test_database_failure_rolls_back_chapters_and_previous_bites(self):
            before = self.snapshot()
            self.execute("ALTER TABLE catalog_bites ADD CONSTRAINT fail_daehak_bite CHECK (sort_order <> 1)")
            try:
                with self.assertRaises(pymysql.MySQLError):
                    self.apply()
                self.assertEqual(self.snapshot(), before)
            finally:
                self.execute("ALTER TABLE catalog_bites DROP CHECK fail_daehak_bite")

        def test_optional_future_columns_are_not_overwritten(self):
            self.apply()
            self.execute("UPDATE catalog_bites SET pinyin=%s ORDER BY id LIMIT 1", ("dà xué",))
            self.execute("UPDATE catalog_bites SET learning=%s ORDER BY id LIMIT 1", ('{"test_only": "preserve"}',))
            before = self.snapshot()
            self.assertEqual(self.apply()["action"], "unchanged")
            self.assertEqual(self.snapshot(), before)

        def test_invalid_content_is_rejected_before_opening_a_connection(self):
            mutations = [
                lambda doc: doc["chapters"].pop(),
                lambda doc: doc["chapters"][0]["bites"].clear(),
                lambda doc: doc["chapters"][1].update(key=doc["chapters"][0]["key"]),
                lambda doc: doc["chapters"][0]["bites"][1].update(key=doc["chapters"][0]["bites"][0]["key"]),
                lambda doc: doc["chapters"][0]["bites"][0].update(original=""),
                lambda doc: doc["chapters"][0]["bites"][0].update(translation=" "),
                lambda doc: doc["chapters"][0].update(title="bad\nheading"),
                lambda doc: doc["chapters"][0].update(key="../escape"),
            ]
            def must_not_connect():
                self.fail("Malformed content must not contact the database")
            for mutate in mutations:
                with self.subTest(mutate=mutate):
                    document = deepcopy(self.document)
                    mutate(document)
                    with self.assertRaises(ValueError):
                        import_content(document, must_not_connect, apply=True)

        def test_bundled_edition_import_is_complete_and_repeatable(self):
            document = load_content(ROOT / "api/content/daehak.json")
            self.assertEqual(len(document["chapters"]), 11)
            result = import_content(document, connect, apply=True)
            self.assertEqual(result["action"], "created")
            self.assertEqual(result["chapters"], 11)
            self.assertEqual(result["bites"], sum(len(row["bites"]) for row in document["chapters"]))
            self.assertEqual(import_content(document, connect, apply=True)["action"], "unchanged")
            for row in prepare_content(document)["bites"]:
                actual = CatalogStore(connect).get_detail("bites", row["id"])["bite"]
                self.assertEqual(actual["original"], row["original"])
                self.assertEqual(actual["translation"], row["translation"])
                self.assertTrue(actual["is_ready"])

    return unittest.defaultTestLoader.loadTestsFromTestCase(DaehakImportTests)


def main():
    # The shared runner creates its own database/container and never reads .env.
    spec = importlib.util.spec_from_file_location("auth_mysql_runner", ROOT / "scripts/check-auth-mysql.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.migrate = migrate
    runner.tests_for = tests_for
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
