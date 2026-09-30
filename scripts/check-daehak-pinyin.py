#!/usr/bin/env python3
"""Exercise missing-reading repair only in a disposable local MySQL database."""

import concurrent.futures
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import threading
import unittest
import uuid

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from fill_daehak_pinyin import ReadingSchemaMissing, fill_readings, prepare_readings
from import_daehak import CLASSICS_ID, DAEHAK_ID, ImportConflict, import_content, load_content, prepare_content
from migrate_catalog import migrate, seed_classics


# Test-only model of the already deployed 003 schema. No other worktree, server,
# environment file, production migration or production database is involved.
READING_SCHEMA_FIXTURE = (
    "ALTER TABLE catalog_works ADD COLUMN title_hanzi VARCHAR(200) NOT NULL DEFAULT ''",
    "ALTER TABLE catalog_works ADD COLUMN title_pinyin VARCHAR(200) NOT NULL DEFAULT ''",
    "ALTER TABLE catalog_works ADD COLUMN source_edition VARCHAR(1000) NOT NULL DEFAULT ''",
    "ALTER TABLE catalog_works ADD COLUMN source_url VARCHAR(2048) NOT NULL DEFAULT ''",
    "ALTER TABLE catalog_works ADD COLUMN pinyin_source VARCHAR(2000) NOT NULL DEFAULT ''",
    "ALTER TABLE catalog_works ADD COLUMN review_status VARCHAR(16) NOT NULL DEFAULT 'draft'",
    "ALTER TABLE catalog_chapters ADD COLUMN title_hanzi VARCHAR(200) NOT NULL DEFAULT ''",
    "ALTER TABLE catalog_chapters ADD COLUMN title_pinyin VARCHAR(200) NOT NULL DEFAULT ''",
    "ALTER TABLE catalog_bites ADD COLUMN pinyin MEDIUMTEXT NOT NULL DEFAULT ('')",
)


def migrate_fixture(connect):
    migrate(connect)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            for statement in READING_SCHEMA_FIXTURE:
                cursor.execute(statement)
        connection.commit()
    finally:
        connection.close()


def fixture():
    return {
        "edition": "독립 MySQL 검사 전용 가상 본문", "source_url": "https://example.com/test-only",
        "title_hanzi": "大學", "title_pinyin": "dà xué", "pinyin_source": "독립 검사 전용 병음",
        "chapters": [{
            "key": "chapter-" + str(index), "title": "검사용 장 " + str(index),
            "title_hanzi": "經 " + str(index), "title_pinyin": "jīng " + str(index),
            "bites": [{"key": "bite-" + str(part), "title": "검사용 한입 " + str(part),
                       "original": "大學之道 " + str(index) + "/" + str(part),
                       "translation": "검증용 번역", "commentary": "검증용 해설",
                       "pinyin": "dà xué zhī dào " + str(index) + "/" + str(part)} for part in range(2)],
        } for index in range(11)],
    }


def tests_for(connect):
    class DaehakPinyinTests(unittest.TestCase):
        def setUp(self):
            self.document = fixture()
            for kind in ("bites", "chapters", "works", "books"):
                self.execute("DELETE FROM catalog_" + kind)
            seed_classics(connect)
            import_content(self.document, connect, apply=True)

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

        def nonreading_snapshot(self):
            result = self.snapshot()
            excluded = {"works": {"title_hanzi", "title_pinyin", "source_edition", "source_url", "pinyin_source"},
                        "chapters": {"title_hanzi", "title_pinyin"}, "bites": {"pinyin"}}
            return {kind: [{key: value for key, value in row.items() if key not in excluded.get(kind, set())}
                           for row in rows] for kind, rows in result.items()}

        def apply(self):
            return fill_readings(self.document, connect, apply=True)

        def assert_refused_unchanged(self):
            before = self.snapshot()
            for apply in (False, True):
                with self.assertRaises(ImportConflict):
                    fill_readings(self.document, connect, apply=apply)
                self.assertEqual(self.snapshot(), before)

        def test_dry_run_apply_repeat_and_old_import_preserve_every_other_field(self):
            before = self.snapshot()
            untouched = self.nonreading_snapshot()
            plan = fill_readings(self.document, connect)
            self.assertEqual(plan["action"], "would_update")
            self.assertEqual((plan["work_fields_to_fill"], plan["chapter_fields_to_fill"], plan["bites_to_fill"]), (5, 22, 22))
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.apply()["action"], "updated")
            self.assertEqual(self.nonreading_snapshot(), untouched)
            after = self.snapshot()
            self.assertEqual(self.apply()["action"], "unchanged")
            self.assertEqual(fill_readings(self.document, connect)["bites_to_fill"], 0)
            self.assertEqual(import_content(self.document, connect, apply=True)["action"], "unchanged")
            self.assertEqual(self.snapshot(), after)
            work = self.execute("SELECT * FROM catalog_works WHERE id=%s", (DAEHAK_ID,))[0]
            self.assertEqual(work["review_status"], "draft")
            self.assertEqual(work["pinyin_source"], self.document["pinyin_source"])
            for row in prepare_readings(self.document)["bites"]:
                actual = self.execute("SELECT original,pinyin FROM catalog_bites WHERE id=%s", (row["id"],))[0]
                self.assertEqual(actual, {field: row[field] for field in ("original", "pinyin")})

        def test_partial_matching_readings_complete_only_empty_fields(self):
            expected = prepare_readings(self.document)
            self.execute("UPDATE catalog_works SET title_hanzi=%s WHERE id=%s", ("大學", DAEHAK_ID))
            self.execute("UPDATE catalog_bites SET pinyin=%s WHERE id=%s", (expected["bites"][0]["pinyin"], expected["bites"][0]["id"]))
            result = self.apply()
            self.assertEqual(result["work_fields_to_fill"], 4)
            self.assertEqual(result["bites_to_fill"], 21)
            self.assertEqual(self.apply()["action"], "unchanged")

        def test_any_conflicting_nonempty_reading_refuses_all_writes(self):
            for kind, field, item_id in (("works", "title_pinyin", DAEHAK_ID),
                                        ("chapters", "title_hanzi", prepare_content(self.document)["chapters"][-1]["id"]),
                                        ("bites", "pinyin", prepare_content(self.document)["bites"][-1]["id"])):
                with self.subTest(kind=kind):
                    self.setUp()
                    self.execute("UPDATE catalog_" + kind + " SET " + field + "=%s WHERE id=%s", ("edited reading", item_id))
                    self.assert_refused_unchanged()

        def test_existing_provenance_review_and_editorial_changes_are_preserved(self):
            self.execute("UPDATE catalog_works SET source_edition='existing edition',source_url='https://example.com/existing',"
                         "pinyin_source='existing source',review_status='reviewed',title='관리자 제목',sort_order=19,is_published=0 WHERE id=%s", (DAEHAK_ID,))
            self.execute("UPDATE catalog_chapters SET title='편집한 장',sort_order=20,is_published=0")
            self.execute("UPDATE catalog_bites SET title='편집한 한입',translation='수정 번역',commentary='수정 해설',sort_order=21,is_published=0")
            untouched = self.nonreading_snapshot()
            metadata = self.execute("SELECT source_edition,source_url,pinyin_source,review_status FROM catalog_works WHERE id=%s", (DAEHAK_ID,))
            self.assertEqual(self.apply()["work_fields_to_fill"], 2)
            self.assertEqual(self.nonreading_snapshot(), untouched)
            self.assertEqual(self.execute("SELECT source_edition,source_url,pinyin_source,review_status FROM catalog_works WHERE id=%s", (DAEHAK_ID,)), metadata)

        def test_changed_original_missing_rows_extra_rows_and_wrong_parent_are_refused(self):
            expected = prepare_content(self.document)
            for sql, values in (
                ("UPDATE catalog_bites SET original='changed original' WHERE id=%s", (expected["bites"][0]["id"],)),
                ("DELETE FROM catalog_bites WHERE id=%s", (expected["bites"][0]["id"],)),
                ("UPDATE catalog_bites SET chapter_id=%s WHERE id=%s", (expected["chapters"][1]["id"], expected["bites"][0]["id"])),
                ("UPDATE catalog_chapters SET work_id=NULL WHERE id=%s", (expected["chapters"][0]["id"],)),
                ("INSERT INTO catalog_chapters (id,book_id,work_id,title) VALUES (%s,%s,%s,%s)", (str(uuid.uuid4()), CLASSICS_ID, DAEHAK_ID, "다른 장")),
            ):
                with self.subTest(sql=sql):
                    self.setUp()
                    self.execute(sql, values)
                    self.assert_refused_unchanged()

        def test_sql_failure_rolls_back_readings_titles_and_metadata(self):
            refused_id = prepare_content(self.document)["bites"][1]["id"]
            before = self.snapshot()
            self.execute("ALTER TABLE catalog_bites ADD CONSTRAINT fail_pinyin_fill CHECK (pinyin='' OR id<>%s)", (refused_id,))
            try:
                with self.assertRaises(pymysql.MySQLError):
                    self.apply()
                self.assertEqual(self.snapshot(), before)
            finally:
                self.execute("ALTER TABLE catalog_bites DROP CHECK fail_pinyin_fill")

        def test_missing_schema_refuses_without_creating_columns_or_writing(self):
            self.execute("ALTER TABLE catalog_bites DROP COLUMN pinyin")
            try:
                before = self.snapshot()
                with self.assertRaisesRegex(ReadingSchemaMissing, "schema is not installed"):
                    self.apply()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.execute("SHOW COLUMNS FROM catalog_bites LIKE 'pinyin'"), ())
            finally:
                self.execute(READING_SCHEMA_FIXTURE[-1])

        def test_concurrent_original_edit_is_seen_before_any_update(self):
            bite_id = prepare_content(self.document)["bites"][0]["id"]
            editor = connect()
            reached_lock = threading.Event()

            class ObservingCursor:
                def __init__(self, cursor):
                    self.cursor = cursor
                def __enter__(self):
                    self.cursor.__enter__()
                    return self
                def __exit__(self, *args):
                    return self.cursor.__exit__(*args)
                def __getattr__(self, key):
                    return getattr(self.cursor, key)
                def execute(self, sql, values=()):
                    if "FROM catalog_bites" in sql and "FOR UPDATE" in sql:
                        reached_lock.set()
                    return self.cursor.execute(sql, values)

            class ObservingConnection:
                def __init__(self):
                    self.connection = connect()
                def cursor(self):
                    return ObservingCursor(self.connection.cursor())
                def __getattr__(self, key):
                    return getattr(self.connection, key)

            before = self.snapshot()
            try:
                editor.begin()
                with editor.cursor() as cursor:
                    cursor.execute("SELECT id FROM catalog_bites WHERE id=%s FOR UPDATE", (bite_id,))
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(fill_readings, self.document, ObservingConnection, apply=True)
                    try:
                        self.assertTrue(reached_lock.wait(timeout=5))
                        with editor.cursor() as cursor:
                            cursor.execute("UPDATE catalog_bites SET original=%s WHERE id=%s", ("concurrent original edit", bite_id))
                        editor.commit()
                        with self.assertRaises(ImportConflict):
                            future.result(timeout=10)
                    finally:
                        editor.rollback()
                for row in before["bites"]:
                    if row["id"] == bite_id:
                        row["original"] = "concurrent original edit"
                self.assertEqual(self.snapshot(), before)
            finally:
                editor.close()

        def test_two_concurrent_fills_produce_one_update_and_one_noop(self):
            barrier = threading.Barrier(2)
            def synchronized_connect():
                connection = connect()
                barrier.wait(timeout=5)
                return connection
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(fill_readings, self.document, synchronized_connect, apply=True) for _ in range(2)]
                self.assertEqual(sorted(future.result(timeout=15)["action"] for future in futures), ["unchanged", "updated"])

        def test_invalid_readings_rejected_before_contacting_database(self):
            for mutate in (
                lambda document: document.update(title_pinyin=""),
                lambda document: document.update(pinyin_source=""),
                lambda document: document["chapters"][0].update(title_hanzi="bad\nheading"),
                lambda document: document["chapters"][0]["bites"][0].update(pinyin=" "),
            ):
                document = deepcopy(self.document)
                mutate(document)
                with self.assertRaises(ValueError):
                    fill_readings(document, lambda: self.fail("Invalid readings must not connect"), apply=True)

        def test_bundled_34_bites_are_filled_and_original_import_stays_repeatable(self):
            document = load_content(ROOT / "api/content/daehak.json")
            for kind in ("bites", "chapters"):
                self.execute("DELETE FROM catalog_" + kind)
            import_content(document, connect, apply=True)
            before = self.nonreading_snapshot()
            result = fill_readings(document, connect, apply=True)
            self.assertEqual((result["chapters"], result["bites"], result["bites_to_fill"]), (11, 34, 34))
            self.assertEqual(self.nonreading_snapshot(), before)
            after = self.snapshot()
            self.assertEqual(fill_readings(document, connect, apply=True)["action"], "unchanged")
            self.assertEqual(import_content(document, connect, apply=True)["action"], "unchanged")
            self.assertEqual(self.snapshot(), after)
            for row in prepare_readings(document)["bites"]:
                actual = self.execute("SELECT original,pinyin FROM catalog_bites WHERE id=%s", (row["id"],))[0]
                self.assertEqual(actual, {field: row[field] for field in ("original", "pinyin")})

    return unittest.defaultTestLoader.loadTestsFromTestCase(DaehakPinyinTests)


def main():
    spec = importlib.util.spec_from_file_location("auth_mysql_runner", ROOT / "scripts/check-auth-mysql.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.migrate = migrate_fixture
    runner.tests_for = tests_for
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
