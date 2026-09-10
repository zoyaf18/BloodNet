"""PostgreSQL-backed audit repository."""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from contracts.models import AuditRecord


class PostgreSQLAuditRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def append(self, record: AuditRecord, cursor: psycopg.Cursor | None = None) -> None:
        """
        Append an audit record to the database.
        
        If cursor is provided, uses that cursor (within an existing transaction).
        Otherwise, creates a new connection.
        """
        if cursor is not None:
            # Use provided cursor (within transaction)
            self._execute_insert(cursor, record)
        else:
            # Create new connection
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as new_cursor:
                    self._execute_insert(new_cursor, record)

    def _execute_insert(self, cursor: psycopg.Cursor, record: AuditRecord) -> None:
        """Execute the audit insert using the provided cursor.

        Preserve the first record for an audit_id, including across retries and
        concurrent writers. Later calls must never rewrite historical evidence.
        """
        cursor.execute(
            """
            INSERT INTO audit_records (audit_id, payload)
            VALUES (%s, %s)
            ON CONFLICT (audit_id) DO NOTHING
            """,
            (
                record.audit_id,
                Jsonb(record.model_dump(mode="json")),
            ),
        )

    def all(self) -> list[AuditRecord]:
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT payload
                    FROM audit_records
                    ORDER BY created_at, audit_id
                    """
                )
                rows = cursor.fetchall()
            return [AuditRecord.model_validate(row[0]) for row in rows]
