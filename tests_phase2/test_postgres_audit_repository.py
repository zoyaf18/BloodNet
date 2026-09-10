from datetime import datetime, timezone
import os

import psycopg
import pytest

from contracts.audit_postgres import PostgreSQLAuditRepository
from contracts.models import AuditRecord


DATABASE_URL = os.environ.get("BLOODNET_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for PostgreSQL tests",
)
TEST_PREFIX = "PG-AUDIT-REPOSITORY-TEST-"


@pytest.fixture
def repository_rows():
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM audit_records WHERE audit_id LIKE %s",
            (f"{TEST_PREFIX}%",),
        )
    yield
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM audit_records WHERE audit_id LIKE %s",
            (f"{TEST_PREFIX}%",),
        )


def make_record(audit_id: str, action: str = "test") -> AuditRecord:
    return AuditRecord(
        audit_id=f"{TEST_PREFIX}{audit_id}",
        action=action,
        request_id="REQ-AUDIT-TEST",
        case_id="CASE-AUDIT-TEST",
        at=datetime.now(timezone.utc),
    )


def test_audit_persists_across_repository_instances(repository_rows):
    assert DATABASE_URL is not None
    record = make_record("001")

    PostgreSQLAuditRepository(DATABASE_URL).append(record)
    persisted = PostgreSQLAuditRepository(DATABASE_URL).all()

    assert record in persisted


def test_same_audit_id_preserves_original_evidence_without_duplicates(repository_rows):
    assert DATABASE_URL is not None
    record = make_record("001", action="original")
    updated = record.model_copy(update={"action": "updated"})
    repository = PostgreSQLAuditRepository(DATABASE_URL)

    repository.append(record)
    repository.append(updated)
    matching_records = [
        item for item in repository.all() if item.audit_id == record.audit_id
    ]

    assert matching_records == [record]


def test_audits_are_returned_in_deterministic_order(repository_rows):
    assert DATABASE_URL is not None
    first = make_record("001")
    second = make_record("002")
    repository = PostgreSQLAuditRepository(DATABASE_URL)

    repository.append(first)
    repository.append(second)
    ordered_ids = [
        item.audit_id
        for item in repository.all()
        if item.audit_id.startswith(TEST_PREFIX)
    ]

    assert ordered_ids == [first.audit_id, second.audit_id]
