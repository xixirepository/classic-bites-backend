#!/usr/bin/env python3
"""Learning contracts and atomic imports in a disposable local MySQL database."""

import concurrent.futures
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import threading
import unittest

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from catalog_learning import BiteLearning, validate_learning
from catalog_store import CatalogConflict, CatalogNotFound, CatalogStore
from fill_daehak_pinyin import fill_readings
from import_daehak import CLASSICS_ID, DAEHAK_ID, ImportConflict, import_content, load_content
from import_daehak_learning import (LearningSchemaMissing, import_learning, load_learning, prepare_learning)
from migrate_catalog import migrate, seed_classics


def lesson():
    return {"version": 1, "source_note": "독립 검사 전용 학습 자료", "units": [{
        "id": "first", "title": "검사용 배움", "original": "大學", "pinyin": "dà xué",
        "opening_question": "무엇을 배우나요?", "glossary": [
            {"term": "大學", "reading": "대학", "meaning": "검사에서 사용하는 단어"}],
        "translation": "검사용 풀이", "summary": "검사용 핵심", "everyday_example": "오늘의 예시입니다.",
        "check": {"question": "배운 단어는?", "choices": ["대학", "다른 말"],
                  "answer_index": 0, "explanation": "원문에서 확인할 수 있어요."},
    }]}


def fixture():
    source = {
        "edition": "독립 검사 전용 판본", "source_url": "https://example.com/test-only",
        "title_hanzi": "大學", "title_pinyin": "dà xué", "pinyin_source": "독립 검사 전용 병음",
        "chapters": [{"key": "chapter-" + str(index), "title": "검사 장 " + str(index),
                      "title_hanzi": "大學", "title_pinyin": "dà xué", "bites": [{
                          "key": "bite", "title": "검사 한입", "original": "大學", "pinyin": "dà xué",
                          "translation": "보존할 번역", "commentary": "보존할 해설"}]} for index in range(11)],
    }
    document = {"version": 1, "work_id": DAEHAK_ID, "bites": [
        {"chapter_key": chapter["key"], "bite_key": "bite", "learning": lesson()}
        for chapter in source["chapters"]]}
    return source, document


class LearningValidationTests(unittest.TestCase):
    def test_whitespace_punctuation_and_case_do_not_change_source(self):
        data = lesson()
        data["units"][0].update(original="大，學。", pinyin="Dà， Xué。")
        self.assertEqual(validate_learning(data, "大學", "dà xué")["version"], 1)

    def test_structure_bounds_answers_glossary_and_unknown_fields_are_rejected(self):
        for mutate in (
            lambda data: data.update(version=True),
            lambda data: data.update(version=2),
            lambda data: data.update(units=[]),
            lambda data: data.update(units=data["units"] * 65),
            lambda data: data.update(source_note=" " ),
            lambda data: data["units"].append(deepcopy(data["units"][0])),
            lambda data: data["units"][0].update(unexpected="value"),
            lambda data: data["units"][0].update(title="x" * 201),
            lambda data: data["units"][0].update(glossary=[]),
            lambda data: data["units"][0]["glossary"][0].update(term="不存在"),
            lambda data: data["units"][0]["check"].update(answer_index=True),
            lambda data: data["units"][0]["check"].update(answer_index=2),
            lambda data: data["units"][0]["check"].update(choices=["하나"]),
            lambda data: data["units"][0]["check"].update(choices=["같음", "같음"]),
        ):
            with self.subTest(mutation=mutate):
                data = lesson()
                mutate(data)
                with self.assertRaises(ValueError):
                    BiteLearning.model_validate(data)

    def test_wrong_source_reading_tone_and_unit_boundary_are_rejected(self):
        for original, pinyin in (("學大", "dà xué"), ("大學", "xué dà"), ("大學", "dá xué")):
            with self.assertRaises(ValueError):
                validate_learning(lesson(), original, pinyin)
        data = lesson()
        data["units"][0]["pinyin"] = "dàxué"
        with self.assertRaises(ValueError):
            BiteLearning.model_validate(data)

    def test_all_source_bites_required_before_any_database_connection(self):
        source, data = fixture()
        for mutate in (lambda doc: doc["bites"].pop(),
                       lambda doc: doc["bites"].append(deepcopy(doc["bites"][0])),
                       lambda doc: doc["bites"][0].update(bite_key="unknown"),
                       lambda doc: doc.update(work_id=CLASSICS_ID)):
            changed = deepcopy(data)
            mutate(changed)
            with self.assertRaises(ValueError):
                import_learning(changed, source, lambda: self.fail("Must validate before connecting"))

    def test_complete_learning_manuscript_matches_all_existing_source_bites(self):
        source = load_content(ROOT / "api/content/daehak.json")
        data = load_learning(ROOT / "api/content/daehak-learning.json")
        rows = prepare_learning(data, source)
        self.assertEqual(len(rows["chapters"]), 11)
        self.assertEqual(len(rows["bites"]), 34)
        self.assertTrue(all(row["learning"]["units"] for row in rows["bites"]))


def tests_for(connect):
    class LearningMySQLTests(unittest.TestCase):
        def execute(self, sql, values=()):
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute(sql, values)
                rows = cursor.fetchall()
                connection.commit()
                return rows

        def setUp(self):
            self.source, self.document = fixture()
            self.expected = prepare_learning(self.document, self.source)
            for kind in ("bites", "chapters", "works", "books"):
                self.execute("DELETE FROM catalog_" + kind)
            seed_classics(connect)
            import_content(self.source, connect, apply=True)
            fill_readings(self.source, connect, apply=True)

        def snapshot(self, without_learning=False):
            rows = {kind: list(self.execute("SELECT * FROM catalog_" + kind + " ORDER BY id"))
                    for kind in ("books", "works", "chapters", "bites")}
            if without_learning:
                for row in rows["bites"]:
                    row.pop("learning", None)
            return rows

        def apply(self, connect_to=connect):
            return import_learning(self.document, self.source, connect_to, apply=True)

        def assert_refused_unchanged(self):
            before = self.snapshot()
            for apply in (False, True):
                with self.assertRaises(ImportConflict):
                    import_learning(self.document, self.source, connect, apply=apply)
                self.assertEqual(self.snapshot(), before)

        def test_plan_apply_repeat_preserve_all_other_columns_and_reader_contract(self):
            before = self.snapshot()
            self.assertEqual(import_learning(self.document, self.source, connect)["action"], "would_update")
            self.assertEqual(self.snapshot(), before)
            untouched = self.snapshot(without_learning=True)
            self.assertEqual(self.apply()["bites_to_fill"], 11)
            self.assertEqual(self.snapshot(without_learning=True), untouched)
            after = self.snapshot()
            self.assertEqual(self.apply()["action"], "unchanged")
            migrate(connect)
            self.assertEqual(self.snapshot(), after)
            reader = CatalogStore(connect).get_work_reader(DAEHAK_ID)
            self.assertEqual(reader["chapters"][0]["bites"][0]["learning"], lesson())
            self.execute("UPDATE catalog_works SET is_published=0 WHERE id=%s", (DAEHAK_ID,))
            with self.assertRaises(CatalogNotFound):
                CatalogStore(connect).get_work_reader(DAEHAK_ID)

        def test_admin_edits_and_publication_flags_are_preserved(self):
            self.execute("UPDATE catalog_works SET title='편집 제목',source_edition='보존 출처',review_status='reviewed',is_published=0")
            self.execute("UPDATE catalog_chapters SET title='편집 장',sort_order=19,is_published=0")
            self.execute("UPDATE catalog_bites SET title='편집 한입',translation='편집 번역',commentary='편집 해설',sort_order=20,is_published=0")
            before = self.snapshot(without_learning=True)
            self.assertEqual(self.apply()["action"], "updated")
            self.assertEqual(self.snapshot(without_learning=True), before)

        def test_changed_source_pinyin_parent_missing_or_conflicting_learning_refuses_all(self):
            first, second = self.expected["bites"][:2]
            for sql, values in (
                ("UPDATE catalog_bites SET original='學大' WHERE id=%s", (first["id"],)),
                ("UPDATE catalog_bites SET pinyin='xué dà' WHERE id=%s", (first["id"],)),
                ("UPDATE catalog_bites SET chapter_id=%s WHERE id=%s", (second["chapter_id"], first["id"])),
                ("DELETE FROM catalog_bites WHERE id=%s", (first["id"],)),
                ("UPDATE catalog_bites SET learning=%s WHERE id=%s", ('{"edited":true}', second["id"])),
            ):
                self.setUp()
                self.execute(sql, values)
                self.assert_refused_unchanged()

        def test_partial_matching_learning_fills_only_null_values(self):
            first = self.expected["bites"][0]
            self.execute("UPDATE catalog_bites SET learning=%s WHERE id=%s", (json.dumps(lesson()), first["id"]))
            self.assertEqual(self.apply()["bites_to_fill"], 10)
            self.assertEqual(self.apply()["bites_to_fill"], 0)

        def test_database_failure_rolls_back_previous_learning_updates(self):
            refused = self.expected["bites"][1]["id"]
            before = self.snapshot()
            self.execute("ALTER TABLE catalog_bites ADD CONSTRAINT refuse_learning CHECK (learning IS NULL OR id<>%s)", (refused,))
            try:
                with self.assertRaises(pymysql.MySQLError):
                    self.apply()
                self.assertEqual(self.snapshot(), before)
            finally:
                self.execute("ALTER TABLE catalog_bites DROP CHECK refuse_learning")

        def test_missing_schema_never_installs_columns_or_writes(self):
            self.execute("ALTER TABLE catalog_bites DROP COLUMN learning")
            try:
                before = self.snapshot()
                with self.assertRaises(LearningSchemaMissing):
                    self.apply()
                self.assertEqual(self.snapshot(), before)
            finally:
                migrate(connect)

        def test_omitted_fields_survive_store_edits_and_explicit_null_removes_learning(self):
            self.apply()
            store = CatalogStore(connect)
            first = self.expected["bites"][0]
            before = store.get_detail("bites", first["id"], False)["bite"]
            after = store.save("bites", {"title": "새 제목"}, first["id"])["bite"]
            self.assertEqual({**after, "title": before["title"]}, before)
            with self.assertRaises(CatalogConflict):
                store.save("bites", {"original": "學大"}, first["id"])
            after = store.save("bites", {"learning": None, "original": "學大"}, first["id"])["bite"]
            self.assertIsNone(after["learning"])
            self.assertEqual(after["pinyin"], before["pinyin"])
            work = store.get_detail("works", DAEHAK_ID, False)["work"]
            changed = store.save("works", {"title": "새 작품 제목"}, DAEHAK_ID)["work"]
            self.assertEqual({**changed, "title": work["title"]}, work)

        def test_two_concurrent_imports_serialize_to_one_update_and_one_noop(self):
            barrier = threading.Barrier(2)
            def synchronized_connect():
                connection = connect()
                barrier.wait(timeout=5)
                return connection
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.apply, synchronized_connect) for _ in range(2)]
                self.assertEqual(sorted(future.result(timeout=15)["action"] for future in futures), ["unchanged", "updated"])

        def test_concurrent_source_edit_is_read_under_lock_before_writes(self):
            bite_id = self.expected["bites"][0]["id"]
            reached_lock = threading.Event()
            class ObservingCursor:
                def __init__(self, cursor): self.cursor = cursor
                def __enter__(self): self.cursor.__enter__(); return self
                def __exit__(self, *args): return self.cursor.__exit__(*args)
                def __getattr__(self, key): return getattr(self.cursor, key)
                def execute(self, sql, values=()):
                    if "FROM catalog_bites" in sql and "FOR UPDATE" in sql: reached_lock.set()
                    return self.cursor.execute(sql, values)
            class ObservingConnection:
                def __init__(self): self.connection = connect()
                def cursor(self): return ObservingCursor(self.connection.cursor())
                def __getattr__(self, key): return getattr(self.connection, key)
            editor = connect()
            try:
                editor.begin()
                with editor.cursor() as cursor:
                    cursor.execute("SELECT id FROM catalog_bites WHERE id=%s FOR UPDATE", (bite_id,))
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.apply, ObservingConnection)
                    try:
                        self.assertTrue(reached_lock.wait(timeout=5))
                        with editor.cursor() as cursor:
                            cursor.execute("UPDATE catalog_bites SET pinyin='xué dà' WHERE id=%s", (bite_id,))
                        editor.commit()
                        with self.assertRaises(ImportConflict): future.result(timeout=10)
                    finally:
                        editor.rollback()
                self.assertEqual(self.execute("SELECT COUNT(*) AS count FROM catalog_bites WHERE learning IS NOT NULL")[0]["count"], 0)
            finally:
                editor.close()

    return unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(LearningValidationTests),
                               unittest.defaultTestLoader.loadTestsFromTestCase(LearningMySQLTests)])


def main():
    if sys.argv[1:] == ["--models-only"]:
        return 0 if unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(LearningValidationTests)).wasSuccessful() else 1
    spec = importlib.util.spec_from_file_location("auth_mysql_runner", ROOT / "scripts/check-auth-mysql.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.migrate = migrate
    runner.tests_for = tests_for
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
