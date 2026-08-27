# coding: utf-8
import sqlite3
import tempfile
import unittest
from pathlib import Path

from handoff_atomic import (
    FOUNDATION_MODE,
    ScopeViolationError,
    UPPER_MODE,
    atomic_update_database,
    foundation_contract_sha256,
)


class AtomicHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "ydb转换数据库.db"
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript(
                """
                CREATE TABLE tbl5 (ID INTEGER, Value TEXT);
                INSERT INTO tbl5 VALUES (1,'pile-type');
                CREATE TABLE tbl6 (ID INTEGER, Value TEXT);
                INSERT INTO tbl6 VALUES (1,'cap-type');
                CREATE TABLE tbl7 (ID INTEGER, Value TEXT);
                INSERT INTO tbl7 VALUES (1,'placement');
                CREATE TABLE handoff_meta (Key TEXT PRIMARY KEY, Value TEXT NOT NULL);
                INSERT INTO handoff_meta VALUES ('Foundation.SourceSHA256','ABC123');
                CREATE TABLE KeepMe (Value BLOB);
                INSERT INTO KeepMe VALUES (X'001122');
                """
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _pending_files(self):
        return list(self.database.parent.glob(self.database.name + ".pending-*"))

    def test_writer_failure_leaves_official_file_byte_for_byte_unchanged(self):
        original = self.database.read_bytes()

        def failing_writer(pending_path):
            connection = sqlite3.connect(pending_path)
            try:
                connection.execute("DROP TABLE tbl5")
                connection.execute("CREATE TABLE tbl1 (ID INTEGER)")
                connection.commit()
            finally:
                connection.close()
            raise RuntimeError("synthetic extraction failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            atomic_update_database(self.database, UPPER_MODE, failing_writer)

        self.assertEqual(original, self.database.read_bytes())
        self.assertEqual([], self._pending_files())

    def test_out_of_scope_change_is_rejected_before_replacement(self):
        original = self.database.read_bytes()

        def violating_writer(pending_path):
            connection = sqlite3.connect(pending_path)
            try:
                connection.execute("UPDATE tbl5 SET Value='changed'")
                connection.execute("CREATE TABLE tbl1 (ID INTEGER)")
                connection.commit()
            finally:
                connection.close()
            return {"rows": 1}

        with self.assertRaises(ScopeViolationError):
            atomic_update_database(self.database, UPPER_MODE, violating_writer)

        self.assertEqual(original, self.database.read_bytes())
        self.assertEqual([], self._pending_files())

    def test_foundation_mode_rejects_changes_to_non_foundation_objects(self):
        original = self.database.read_bytes()

        def violating_writer(pending_path):
            connection = sqlite3.connect(pending_path)
            try:
                connection.execute("UPDATE KeepMe SET Value=X'FF'")
                connection.execute(
                    "INSERT OR REPLACE INTO handoff_meta VALUES "
                    "('Foundation.SourceSHA256','DEF456')"
                )
                connection.commit()
            finally:
                connection.close()
            return {"rows": 1}

        with self.assertRaises(ScopeViolationError):
            atomic_update_database(
                self.database, FOUNDATION_MODE, violating_writer
            )

        self.assertEqual(original, self.database.read_bytes())
        self.assertEqual([], self._pending_files())

    def test_success_reports_scope_hash_and_replaces_only_after_validation(self):
        foundation_before = foundation_contract_sha256(self.database)

        def upper_writer(pending_path):
            connection = sqlite3.connect(pending_path)
            try:
                for number in range(1, 5):
                    connection.execute(
                        "CREATE TABLE tbl{} (ID INTEGER)".format(number)
                    )
                    connection.execute(
                        "INSERT INTO tbl{} VALUES (?)".format(number),
                        (number,),
                    )
                connection.commit()
            finally:
                connection.close()
            return {"rows": 4}

        summary = atomic_update_database(self.database, UPPER_MODE, upper_writer)

        self.assertEqual("upper", summary["mode"])
        self.assertEqual("success", summary["status"])
        self.assertEqual(foundation_before, summary["foundation_sha256"])
        self.assertEqual(foundation_before, foundation_contract_sha256(self.database))
        self.assertEqual([], self._pending_files())


if __name__ == "__main__":
    unittest.main()
