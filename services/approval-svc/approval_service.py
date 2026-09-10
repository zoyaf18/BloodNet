"""Explicit human approval lifecycle for allowlisted recommendations."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class ApprovalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    ESCALATED = "ESCALATED"


class ApprovalRecord(BaseModel):
    approval_id: str
    recommendation_id: str
    request_id: str
    case_id: str
    status: ApprovalState = ApprovalState.PENDING
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ApprovalServiceError(RuntimeError):
    """Raised when an approval transition violates the lifecycle."""


class PostgreSQLApprovalRepository:
    """Durable storage for generic recommendation approval records."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def load_all(self) -> list[ApprovalRecord]:
        import psycopg

        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                "SELECT payload FROM approval_records"
            ).fetchall()
        return [ApprovalRecord.model_validate(row[0]) for row in rows]

    def get(self, approval_id: str) -> ApprovalRecord | None:
        import psycopg

        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT payload FROM approval_records WHERE approval_id = %s",
                (approval_id,),
            ).fetchone()
        return ApprovalRecord.model_validate(row[0]) if row else None

    def save(self, record: ApprovalRecord) -> None:
        import psycopg
        from psycopg.types.json import Jsonb

        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                INSERT INTO approval_records (
                    approval_id, recommendation_id, request_id, case_id,
                    status, payload, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (approval_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    record.approval_id,
                    record.recommendation_id,
                    record.request_id,
                    record.case_id,
                    record.status.value,
                    Jsonb(record.model_dump(mode="json")),
                    record.created_at,
                    record.updated_at,
                ),
            )


class ApprovalService:
    def __init__(self, repository: PostgreSQLApprovalRepository | None = None) -> None:
        self.records: dict[str, ApprovalRecord] = {}
        self.repository = repository
        if repository is not None:
            self.records = {
                record.approval_id: record for record in repository.load_all()
            }

    def create(self, *, recommendation_id: str, request_id: str, case_id: str,
               approval_id: str | None = None) -> ApprovalRecord:
        now = datetime.now(timezone.utc)
        record = ApprovalRecord(
            approval_id=approval_id or f"APRV-{uuid4().hex}",
            recommendation_id=recommendation_id,
            request_id=request_id,
            case_id=case_id,
            created_at=now,
            updated_at=now,
        )
        existing = self.repository.get(record.approval_id) if self.repository is not None else None
        if record.approval_id in self.records or existing is not None:
            raise ApprovalServiceError(f"Approval '{record.approval_id}' already exists")
        self.records[record.approval_id] = record
        self._save(record)
        return record

    def approve(self, approval_id: str, *, actor: str) -> ApprovalRecord:
        record = self._get(approval_id)
        self._require(record, ApprovalState.PENDING)
        if not actor:
            raise ApprovalServiceError("Approval actor is required")
        now = datetime.now(timezone.utc)
        record.status = ApprovalState.APPROVED
        record.approved_by = actor
        record.approved_at = now
        record.updated_at = now
        self._save(record)
        return record

    def reject(self, approval_id: str, *, actor: str, reason: str) -> ApprovalRecord:
        record = self._get(approval_id)
        self._require(record, ApprovalState.PENDING)
        if not actor or not reason:
            raise ApprovalServiceError("Rejection actor and reason are required")
        now = datetime.now(timezone.utc)
        record.status = ApprovalState.REJECTED
        record.rejected_by = actor
        record.rejected_at = now
        record.rejection_reason = reason
        record.updated_at = now
        self._save(record)
        return record

    def mark_executing(self, approval_id: str) -> ApprovalRecord:
        return self._transition(approval_id, ApprovalState.APPROVED, ApprovalState.EXECUTING)

    def mark_executed(self, approval_id: str) -> ApprovalRecord:
        return self._transition(approval_id, ApprovalState.EXECUTING, ApprovalState.EXECUTED)

    def mark_failed(self, approval_id: str) -> ApprovalRecord:
        return self._transition(approval_id, ApprovalState.EXECUTING, ApprovalState.FAILED)

    def mark_partial(self, approval_id: str) -> ApprovalRecord:
        return self._transition(approval_id, ApprovalState.EXECUTING, ApprovalState.PARTIAL)

    def mark_escalated(self, approval_id: str) -> ApprovalRecord:
        return self._transition(
            approval_id, (ApprovalState.FAILED, ApprovalState.PARTIAL), ApprovalState.ESCALATED
        )

    def _get(self, approval_id: str) -> ApprovalRecord:
        if self.repository is not None:
            record = self.repository.get(approval_id)
            if record is None:
                raise ApprovalServiceError(f"Approval '{approval_id}' was not found")
            self.records[approval_id] = record
            return record
        try:
            return self.records[approval_id]
        except KeyError as exc:
            raise ApprovalServiceError(f"Approval '{approval_id}' was not found") from exc

    @staticmethod
    def _require(record: ApprovalRecord, expected: ApprovalState) -> None:
        if record.status != expected:
            raise ApprovalServiceError(
                f"Approval '{record.approval_id}' is {record.status.value}; expected {expected.value}"
            )

    def _transition(
        self,
        approval_id: str,
        expected: ApprovalState | tuple[ApprovalState, ...],
        target: ApprovalState,
    ) -> ApprovalRecord:
        record = self._get(approval_id)
        allowed = expected if isinstance(expected, tuple) else (expected,)
        if record.status not in allowed:
            expected_values = ", ".join(state.value for state in allowed)
            raise ApprovalServiceError(
                f"Approval '{approval_id}' is {record.status.value}; expected {expected_values}"
            )
        record.status = target
        record.updated_at = datetime.now(timezone.utc)
        self._save(record)
        return record

    def _save(self, record: ApprovalRecord) -> None:
        if self.repository is not None:
            self.repository.save(record)