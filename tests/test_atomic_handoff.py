# coding: utf-8
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from handoff_atomic import (
    FOUNDATION_MODE,
    ScopeViolationError,
    UPPER_MODE,
    _is_unc_path,
    atomic_update_database,
    foundation_contract_sha256,
    open_read_only_connection,
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

    def test_each_mode_can_only_change_its_own_metadata_prefix(self):
        def upper_writer(pending_path):
            connection = sqlite3.connect(pending_path)
            try:
                connection.execute(
                    "INSERT INTO handoff_meta VALUES ('Upper.ContractVersion','V1')"
                )
                connection.commit()
            finally:
                connection.close()

        atomic_update_database(self.database, UPPER_MODE, upper_writer)
        original = self.database.read_bytes()

        def foundation_writer_that_changes_upper(pending_path):
            connection = sqlite3.connect(pending_path)
            try:
                connection.execute(
                    "UPDATE handoff_meta SET Value='V2' "
                    "WHERE Key='Upper.ContractVersion'"
                )
                connection.commit()
            finally:
                connection.close()

        with self.assertRaises(ScopeViolationError):
            atomic_update_database(
                self.database, FOUNDATION_MODE, foundation_writer_that_changes_upper
            )
        self.assertEqual(original, self.database.read_bytes())

    def test_read_only_connection_rejects_local_writes(self):
        connection = open_read_only_connection(self.database)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("UPDATE KeepMe SET Value=X'FF'")
        finally:
            connection.close()

    def test_unc_paths_use_native_filename_and_query_only(self):
        unc = r"\\server\share\source.ydb"
        self.assertTrue(_is_unc_path(unc))
        self.assertTrue(_is_unc_path("//server/share/source.ydb"))
        self.assertFalse(_is_unc_path(self.database))

        fake_connection = MagicMock()
        with patch("handoff_atomic.Path.is_file", return_value=True), patch(
            "handoff_atomic.sqlite3.connect", return_value=fake_connection
        ) as connect:
            result = open_read_only_connection(unc)
        self.assertIs(fake_connection, result)
        connect.assert_called_once_with(unc)
        fake_connection.execute.assert_called_once_with("PRAGMA query_only=ON")

    def test_read_only_connection_does_not_create_missing_database(self):
        missing = Path(self.temp_dir.name) / "missing.ydb"
        with self.assertRaises(FileNotFoundError):
            open_read_only_connection(missing)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
