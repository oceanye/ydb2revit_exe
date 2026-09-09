# coding: utf-8
"""Atomic, scope-limited updates for the unified Revit handoff database."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path


UPPER_MODE = "upper"
FOUNDATION_MODE = "foundation"
TARGET_TABLES = {
    UPPER_MODE: frozenset(("tbl1", "tbl2", "tbl3", "tbl4")),
    FOUNDATION_MODE: frozenset(("tbl5", "tbl6", "tbl7", "tbl8", "tbl9")),
}
METADATA_PREFIXES = {
    UPPER_MODE: "Upper.",
    FOUNDATION_MODE: "Foundation.",
}


class HandoffUpdateError(RuntimeError):
    """Base error for a rejected handoff database replacement."""


class ScopeViolationError(HandoffUpdateError):
    """Raised when an extractor changes data outside its assigned scope."""


class ConcurrentUpdateError(HandoffUpdateError):
    """Raised when the official database changes while extraction is running."""


def _quote_identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def _is_unc_path(path):
    """Return whether *path* uses a Windows UNC spelling."""
    text = os.fspath(path).replace("/", "\\")
    return text.startswith("\\\\")


def open_read_only_connection(path):
    """Open SQLite without permitting writes, including for UNC paths.

    SQLite URI authorities reject ``file://server/share`` in the builds used
    by this project.  UNC paths therefore use the native filename together
    with ``PRAGMA query_only``; local paths retain the stronger ``mode=ro``
    URI open.  Callers still receive the same sqlite3 connection interface.
    """
    expanded = Path(path).expanduser()
    if not expanded.is_file():
        raise FileNotFoundError("SQLite database does not exist: " + str(expanded))
    is_unc = _is_unc_path(expanded)
    resolved = expanded if is_unc else expanded.resolve()
    if is_unc:
        connection = sqlite3.connect(str(resolved))
        connection.execute("PRAGMA query_only=ON")
    else:
        connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    return connection


def _read_only_connection(path):
    connection = open_read_only_connection(path)
    connection.execute("BEGIN")
    return connection


def _value_token(value):
    if value is None:
        return ["null", None]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, bool):
        return ["integer", int(value)]
    if isinstance(value, int):
        return ["integer", value]
    if isinstance(value, float):
        if math.isnan(value):
            encoded = "nan"
        elif math.isinf(value):
            encoded = "+inf" if value > 0 else "-inf"
        else:
            encoded = value.hex()
        return ["real", encoded]
    if isinstance(value, str):
        return ["text", value]
    return [type(value).__name__, str(value)]


def _feed(digest, value):
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def _table_payload(connection, table_name, row_filter=None):
    columns = [
        list(row)
        for row in connection.execute(
            "PRAGMA table_info(" + _quote_identifier(table_name) + ")"
        )
    ]
    rows = []
    if columns:
        names = [row[1] for row in columns]
        for row in connection.execute(
            "SELECT * FROM " + _quote_identifier(table_name)
        ):
            values = dict(zip(names, row))
            if row_filter is None or row_filter(values):
                rows.append([_value_token(value) for value in row])
    rows.sort(key=lambda row: json.dumps(row, ensure_ascii=True, separators=(",", ":")))
    return {
        "name": table_name,
        "columns": [[_value_token(value) for value in row] for row in columns],
        "rows": rows,
    }


def _schema_objects(connection):
    return list(connection.execute(
        """
        SELECT type,name,tbl_name,sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type,name,tbl_name
        """
    ))


def _protected_scope_sha256(path, mode):
    """Hash every database object the selected extractor is not allowed to alter."""
    allowed_tables = TARGET_TABLES[mode]
    allowed_metadata_prefix = METADATA_PREFIXES[mode]
    digest = hashlib.sha256()
    connection = _read_only_connection(path)
    try:
        _feed(digest, ["scope", mode, "protected-v1"])
        _feed(digest, ["application_id", connection.execute("PRAGMA application_id").fetchone()[0]])
        _feed(digest, ["user_version", connection.execute("PRAGMA user_version").fetchone()[0]])

        table_names = []
        for object_type, name, table_name, sql in _schema_objects(connection):
            belongs_to_target = name in allowed_tables or table_name in allowed_tables
            shared_meta = (
                name == "handoff_meta" or table_name == "handoff_meta"
            )
            if belongs_to_target or shared_meta:
                continue
            _feed(digest, ["schema", object_type, name, table_name, sql])
            if object_type == "table":
                table_names.append(name)

        for table_name in sorted(table_names):
            _feed(digest, _table_payload(connection, table_name))

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "handoff_meta" not in tables:
            _feed(digest, ["handoff_meta-schema", "KeyValue-v1"])
            _feed(digest, ["handoff_meta-protected", []])
        else:
            column_rows = list(
                connection.execute("PRAGMA table_info(handoff_meta)")
            )
            expected_schema = (
                len(column_rows) == 2
                and [row[1] for row in column_rows] == ["Key", "Value"]
                and all(str(row[2]).upper() == "TEXT" for row in column_rows)
                and column_rows[0][5] == 1
                and column_rows[1][3] == 1
            )
            if not expected_schema:
                _feed(digest, _table_payload(connection, "handoff_meta"))
            else:
                _feed(digest, ["handoff_meta-schema", "KeyValue-v1"])
                payload = _table_payload(
                    connection,
                    "handoff_meta",
                    lambda values: not str(values.get("Key", "")).startswith(
                        allowed_metadata_prefix
                    ),
                )
                _feed(digest, ["handoff_meta-protected", payload["rows"]])
        return digest.hexdigest().upper()
    finally:
        connection.rollback()
        connection.close()


def _full_database_sha256(path):
    digest = hashlib.sha256()
    connection = _read_only_connection(path)
    try:
        _feed(digest, ["database", "logical-v1"])
        _feed(digest, ["application_id", connection.execute("PRAGMA application_id").fetchone()[0]])
        _feed(digest, ["user_version", connection.execute("PRAGMA user_version").fetchone()[0]])
        table_names = []
        for object_type, name, table_name, sql in _schema_objects(connection):
            _feed(digest, ["schema", object_type, name, table_name, sql])
            if object_type == "table":
                table_names.append(name)
        for table_name in sorted(table_names):
            _feed(digest, _table_payload(connection, table_name))
        return digest.hexdigest().upper()
    finally:
        connection.rollback()
        connection.close()


def foundation_contract_sha256(path):
    """Hash foundation contract tables and all Foundation.* metadata rows."""
    digest = hashlib.sha256()
    connection = _read_only_connection(path)
    try:
        _feed(digest, ["foundation-contract", "v1"])
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table_name in ("tbl5", "tbl6", "tbl7", "tbl8", "tbl9"):
            if table_name in tables:
                _feed(digest, _table_payload(connection, table_name))
            else:
                _feed(digest, [table_name, "absent"])
        if "handoff_meta" in tables:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(handoff_meta)")
            }
            if "Key" in columns:
                payload = _table_payload(
                    connection,
                    "handoff_meta",
                    lambda values: str(values.get("Key", "")).startswith(
                        "Foundation."
                    ),
                )
                _feed(digest, ["Foundation.*", payload["rows"]])
            else:
                _feed(digest, ["Foundation.*", "unreadable"])
        else:
            # Creating the shared metadata table with Upper.* rows must not
            # look like a foundation-contract change.
            _feed(digest, ["Foundation.*", []])
        return digest.hexdigest().upper()
    finally:
        connection.rollback()
        connection.close()


def _backup_database(source_path, pending_path):
    source = _read_only_connection(source_path)
    destination = sqlite3.connect(str(pending_path))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.rollback()
        source.close()
    shutil.copymode(source_path, pending_path)


def _check_integrity(path):
    connection = sqlite3.connect(str(path))
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or str(result[0]).lower() != "ok":
            raise HandoffUpdateError(
                "staged database failed integrity_check: "
                + ("no result" if result is None else str(result[0]))
            )
    finally:
        connection.close()


def _sidecar_paths(path):
    raw = str(path)
    return [Path(raw + suffix) for suffix in ("-wal", "-shm", "-journal")]


def _cleanup_pending(path):
    for candidate in [path, *_sidecar_paths(path)]:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_update_database(destination_path, mode, writer):
    """Run *writer* against one staging copy and atomically replace on success."""
    if mode not in TARGET_TABLES:
        raise ValueError("unknown handoff update mode: " + str(mode))

    destination_path = Path(destination_path).expanduser().resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists() and not destination_path.is_file():
        raise HandoffUpdateError("destination is not a file: " + str(destination_path))

    descriptor, pending_name = tempfile.mkstemp(
        prefix=destination_path.name + ".pending-",
        dir=str(destination_path.parent),
    )
    os.close(descriptor)
    pending_path = Path(pending_name)
    destination_existed = destination_path.exists()

    try:
        if destination_existed:
            before_copy_sha256 = _full_database_sha256(destination_path)
            _backup_database(destination_path, pending_path)
            after_copy_sha256 = _full_database_sha256(destination_path)
            staged_baseline_sha256 = _full_database_sha256(pending_path)
            if not (
                before_copy_sha256 == after_copy_sha256 == staged_baseline_sha256
            ):
                raise ConcurrentUpdateError(
                    "official database changed while the staging snapshot was created"
                )
        else:
            sqlite3.connect(str(pending_path)).close()
            before_copy_sha256 = None

        protected_before = _protected_scope_sha256(pending_path, mode)
        foundation_before = (
            foundation_contract_sha256(pending_path)
            if mode == UPPER_MODE
            else None
        )

        result = writer(pending_path)
        _check_integrity(pending_path)

        protected_after = _protected_scope_sha256(pending_path, mode)
        if protected_after != protected_before:
            raise ScopeViolationError(
                mode + " extraction modified data outside its assigned tables"
            )

        foundation_after = (
            foundation_contract_sha256(pending_path)
            if mode == UPPER_MODE
            else None
        )
        if foundation_after != foundation_before:
            raise ScopeViolationError(
                "upper extraction changed foundation contract tables or Foundation.* metadata"
            )

        if destination_existed:
            current_sha256 = _full_database_sha256(destination_path)
            if current_sha256 != before_copy_sha256:
                raise ConcurrentUpdateError(
                    "official database changed before staged replacement"
                )
            active_sidecars = [
                str(path)
                for path in _sidecar_paths(destination_path)
                if path.exists()
            ]
            if active_sidecars:
                raise ConcurrentUpdateError(
                    "official database has active SQLite sidecar files: "
                    + ", ".join(active_sidecars)
                )
        elif destination_path.exists():
            raise ConcurrentUpdateError(
                "destination was created by another process during extraction"
            )

        with pending_path.open("rb+") as stream:
            os.fsync(stream.fileno())
        os.replace(pending_path, destination_path)

        summary = dict(result or {})
        summary.update({
            "mode": mode,
            "status": "success",
            "destination": str(destination_path),
            "protected_sha256": protected_after,
        })
        if mode == UPPER_MODE:
            summary["foundation_sha256"] = foundation_after
        return summary
    finally:
        _cleanup_pending(pending_path)
