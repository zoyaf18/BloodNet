"""Storage-agnostic audit log for local state-changing workflows."""

from __future__ import annotations

import sqlite3

from contracts.models import AuditRecord


class AuditRepository:
    def __init__(self, db_path: str | None = None) -> None:
        self._records: dict[str, AuditRecord] = {}
        self._db_path = db_path
        if self._db_path:
            with sqlite3.connect(self._db_path) as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS audit_records (audit_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def append(self, record: AuditRecord, cursor=None) -> None:
        """
        Append an audit record to the database.

        Audit records are intentionally immutable by audit_id: once written, a later
        record with the same audit_id is ignored instead of silently overwriting the
        original evidence.
        """
        if record.audit_id in self._records:
            return
        self._records[record.audit_id] = record
        if self._db_path:
            if cursor is not None:
                cursor.execute(
                    "INSERT OR IGNORE INTO audit_records(audit_id, payload) VALUES (?, ?)",
                    (record.audit_id, record.model_dump_json()),
                )
            else:
                with sqlite3.connect(self._db_path) as connection:
                    connection.execute(
                        "INSERT OR IGNORE INTO audit_records(audit_id, payload) VALUES (?, ?)",
                        (record.audit_id, record.model_dump_json()),
                    )

    def all(self) -> list[AuditRecord]:
        if not self._db_path:
            return list(self._records.values())
        with sqlite3.connect(self._db_path) as connection:
            rows = connection.execute("SELECT payload FROM audit_records ORDER BY rowid").fetchall()
        return [AuditRecord.model_validate_json(row[0]) for row in rows]